"""
Bear-regime slope guard prototype — should downtrend entries require cts_slope > 0?

Tests whether filtering downtrend entries by cts_slope direction improves signal quality.

Variants (applied ONLY to downtrend-regime entries; non-downtrend entries pass unchanged):
  A  baseline       — no slope guard (current behavior)
  B  slope > 0      — require cts_slope > 0 in downtrend
  C  slope > -0.01  — require cts_slope > -0.01 in downtrend (allow near-flat)
  D  slope > -0.02  — require cts_slope > -0.02 in downtrend (only block steep declines)

All 4 gates use the latest NextGen v2 logic (vel_dp5>=4, vel>-0.10, pdd<threshold).

Output: output/gate_slope_guard_nifty500.txt

Usage:
    venv/bin/python3 scripts/gate_slope_guard_nifty500.py
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from io import StringIO

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.nextgen import _can_enter, NextGenEntryConfig

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"
OUT_DIR = Path(__file__).resolve().parent.parent / "output"
OUT_DIR.mkdir(exist_ok=True)

CFG = NextGenEntryConfig()

VARIANT_NAMES = [
    "A  baseline (no guard)",
    "B  slope > 0",
    "C  slope > -0.01",
    "D  slope > -0.02",
]

SLOPE_THRESHOLDS = [None, 0.0, -0.01, -0.02]


def get_symbols(name: str) -> list[str]:
    db = sqlite3.connect(str(DB_PATH))
    row = db.execute("SELECT id FROM watchlists WHERE name = ?", (name,)).fetchone()
    if not row:
        avail = [r[0] for r in db.execute("SELECT name FROM watchlists ORDER BY name").fetchall()]
        db.close()
        print(f"Watchlist '{name}' not found. Available: {avail}")
        sys.exit(1)
    syms = [r[0] for r in db.execute(
        "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY display_order",
        (row[0],),
    ).fetchall()]
    db.close()
    return syms


def scan_symbol(sym: str, start: str, end: str) -> list[dict]:
    result = DivergenceEngine(sym).run()
    df = result.ledger.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.reset_index(drop=True)
    records = df.to_dict("records")
    n = len(records)

    window = df[(df["date"] >= start) & (df["date"] <= end)]

    rows = []
    for i in window.index:
        row = records[i]
        prev = records[i - 1] if i > 0 else row
        ok, intensity, reason = _can_enter(row, prev, CFG)
        if not ok:
            continue

        regime = str(row.get("regime", ""))
        is_down = "down" in regime.lower()
        slope = row.get("cts_slope", np.nan)

        # Evaluate slope guard variants
        flags = []
        for threshold in SLOPE_THRESHOLDS:
            if threshold is None:
                # Baseline — no guard
                flags.append(True)
            elif not is_down:
                # Guard only applies to downtrend; non-downtrend always passes
                flags.append(True)
            else:
                # Downtrend: check slope
                flags.append(not np.isnan(slope) and slope > threshold)

        if not any(flags):
            continue

        # Forward returns
        fwd = {}
        for h in [5, 10, 20]:
            j = min(i + h, n - 1)
            c0, cf = row["close"], records[j]["close"]
            fwd[h] = round((cf / c0 - 1) * 100, 3) if c0 > 0 else np.nan

        entry = {
            "sym": sym,
            "date": row["date"].strftime("%Y-%m-%d"),
            "close": row["close"],
            "regime": regime,
            "cts": round(row.get("cts", np.nan), 4),
            "cts_slope": round(slope, 4) if not np.isnan(slope) else np.nan,
            "cts_accel": round(row.get("cts_accel", np.nan), 5),
            "vel": round(row.get("velocity_60_norm", np.nan), 4),
            "vel_dp5": int(row.get("vel_dp5", 0)),
            "pdd_120": round(row.get("pdd_120", np.nan), 3),
            "is_down": int(is_down),
            "ret_5": fwd[5],
            "ret_10": fwd[10],
            "ret_20": fwd[20],
        }
        for vi in range(len(VARIANT_NAMES)):
            entry[f"v{vi}"] = int(flags[vi])
        rows.append(entry)
    return rows


def summary(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return {}
    n = len(df)
    return {
        "label": label, "n": n,
        "win5":  (df["ret_5"] > 0).mean() * 100,
        "win10": (df["ret_10"] > 0).mean() * 100,
        "win20": (df["ret_20"] > 0).mean() * 100,
        "avg5":  df["ret_5"].mean(),
        "avg10": df["ret_10"].mean(),
        "avg20": df["ret_20"].mean(),
        "med10": df["ret_10"].median(),
    }


def quintile_breakdown(df: pd.DataFrame, col: str, label: str, out: StringIO) -> None:
    if df.empty or col not in df.columns:
        return
    valid = df[[col, "ret_10"]].dropna()
    if len(valid) < 20:
        return
    valid = valid.copy()
    n_unique = valid[col].nunique()
    n_bins = min(5, n_unique)
    if n_bins < 2:
        return
    bin_labels = [f"Q{i+1}" for i in range(n_bins)]
    try:
        valid["q"] = pd.qcut(valid[col], n_bins, labels=bin_labels, duplicates="drop")
    except ValueError:
        return
    qsumm = valid.groupby("q", observed=True).agg(
        n=("ret_10", "count"),
        win=("ret_10", lambda x: (x > 0).mean() * 100),
        avg=("ret_10", "mean"),
    )
    out.write(f"\n  Breakdown by {label} ({n_bins} bins):\n")
    out.write(f"  {'Bin':<4} {'N':>5} {'Win10%':>8} {'Avg10%':>8}\n")
    for q, row in qsumm.iterrows():
        out.write(f"  {q:<4} {int(row['n']):>5} {row['win']:>7.1f}% {row['avg']:>+7.2f}%\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", default="NIFTY 500")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-03-15")
    args = parser.parse_args()

    out_path = OUT_DIR / "gate_slope_guard_nifty500.txt"
    symbols = get_symbols(args.watchlist)

    print(f"Scanning {len(symbols)} symbols from '{args.watchlist}' ({args.start} -> {args.end})")
    print(f"Output -> {out_path}\n")

    all_rows: list[dict] = []
    skipped: list[str] = []

    for idx, sym in enumerate(symbols, 1):
        print(f"  [{idx:>3}/{len(symbols)}] {sym:<20}", end=" ", flush=True)
        try:
            rows = scan_symbol(sym, args.start, args.end)
            all_rows.extend(rows)
            print(f"{len(rows)} entries")
        except Exception as e:
            skipped.append(f"{sym}: {e}")
            print(f"SKIP - {e}")

    df = pd.DataFrame(all_rows)

    out = StringIO()
    W = 120

    out.write("=" * W + "\n")
    out.write(f"  SLOPE GUARD PROTOTYPE — {args.watchlist}\n")
    out.write(f"  Period: {args.start} -> {args.end}\n")
    out.write(f"  Symbols scanned: {len(symbols)}   Skipped: {len(skipped)}\n")
    out.write(f"  Total NextGen entries (all 4 gates pass): {len(df)}\n")
    if not df.empty:
        n_down = df["is_down"].sum()
        out.write(f"  Of which downtrend: {n_down}   Non-downtrend: {len(df) - n_down}\n")
    out.write("=" * W + "\n\n")

    if df.empty:
        out.write("No data.\n")
    else:
        variant_dfs = {}
        for vi, vname in enumerate(VARIANT_NAMES):
            variant_dfs[vi] = df[df[f"v{vi}"] == 1]

        # ── Overall summary ───────────────────────────────────────────────────
        out.write("── OVERALL SUMMARY (all regimes) ───────────────────────────────────────────────────\n\n")
        hdr = f"  {'Variant':<28} {'N':>6}  {'Win5d':>7}  {'Win10d':>7}  {'Win20d':>7}  {'Avg5d':>7}  {'Avg10d':>7}  {'Avg20d':>7}  {'Med10d':>7}\n"
        out.write(hdr)
        out.write("  " + "-" * (W - 2) + "\n")
        for vi, vname in enumerate(VARIANT_NAMES):
            s = summary(variant_dfs[vi], vname)
            if s:
                out.write(
                    f"  {s['label']:<28} {s['n']:>6}  "
                    f"{s['win5']:>6.1f}%  {s['win10']:>6.1f}%  {s['win20']:>6.1f}%  "
                    f"{s['avg5']:>+6.2f}%  {s['avg10']:>+6.2f}%  {s['avg20']:>+6.2f}%  {s['med10']:>+6.2f}%\n"
                )

        # ── Delta from baseline ───────────────────────────────────────────────
        out.write("\n── DELTA FROM BASELINE ─────────────────────────────────────────────────────────────\n\n")
        s_base = summary(variant_dfs[0], "baseline")
        if s_base:
            out.write(f"  {'Variant':<28} {'dN':>6}  {'dWin10':>8}  {'dAvg10':>8}  {'dMed10':>8}\n")
            out.write("  " + "-" * 60 + "\n")
            for vi, vname in enumerate(VARIANT_NAMES):
                s = summary(variant_dfs[vi], vname)
                if s:
                    out.write(
                        f"  {vname:<28} {s['n'] - s_base['n']:>+6}  "
                        f"{s['win10'] - s_base['win10']:>+7.1f}%  "
                        f"{s['avg10'] - s_base['avg10']:>+7.2f}%  "
                        f"{s['med10'] - s_base['med10']:>+7.2f}%\n"
                    )

        # ── Downtrend-only breakdown ──────────────────────────────────────────
        out.write("\n── DOWNTREND ENTRIES ONLY ──────────────────────────────────────────────────────────\n\n")
        out.write(hdr)
        out.write("  " + "-" * (W - 2) + "\n")
        for vi, vname in enumerate(VARIANT_NAMES):
            vdf = variant_dfs[vi]
            down_only = vdf[vdf["is_down"] == 1]
            s = summary(down_only, vname)
            if s:
                out.write(
                    f"  {s['label']:<28} {s['n']:>6}  "
                    f"{s['win5']:>6.1f}%  {s['win10']:>6.1f}%  {s['win20']:>6.1f}%  "
                    f"{s['avg5']:>+6.2f}%  {s['avg10']:>+6.2f}%  {s['avg20']:>+6.2f}%  {s['med10']:>+6.2f}%\n"
                )

        # ── Downtrend entries split by slope sign ─────────────────────────────
        all_down = df[df["is_down"] == 1].copy()
        if not all_down.empty:
            slope_neg = all_down[all_down["cts_slope"] < 0]
            slope_pos = all_down[all_down["cts_slope"] >= 0]

            out.write("\n── DOWNTREND: SLOPE NEGATIVE vs POSITIVE ──────────────────────────────────────────\n\n")
            for label, sub in [("slope < 0", slope_neg), ("slope >= 0", slope_pos)]:
                s = summary(sub, label)
                if s:
                    out.write(
                        f"  {s['label']:<28} {s['n']:>6}  "
                        f"{s['win5']:>6.1f}%  {s['win10']:>6.1f}%  {s['win20']:>6.1f}%  "
                        f"{s['avg5']:>+6.2f}%  {s['avg10']:>+6.2f}%  {s['avg20']:>+6.2f}%  {s['med10']:>+6.2f}%\n"
                    )

            # Further split slope-negative by depth
            out.write("\n── DOWNTREND SLOPE-NEGATIVE: BY SLOPE DEPTH ───────────────────────────────────────\n\n")
            for lo, hi, label in [
                (-999, -0.03, "slope < -0.03 (steep)"),
                (-0.03, -0.02, "-0.03 < slope < -0.02"),
                (-0.02, -0.01, "-0.02 < slope < -0.01"),
                (-0.01, 0.0, "-0.01 < slope < 0"),
            ]:
                sub = slope_neg[(slope_neg["cts_slope"] > lo) & (slope_neg["cts_slope"] <= hi)]
                s = summary(sub, label)
                if s:
                    out.write(
                        f"  {s['label']:<28} {s['n']:>6}  "
                        f"{s['win5']:>6.1f}%  {s['win10']:>6.1f}%  {s['win20']:>6.1f}%  "
                        f"{s['avg5']:>+6.2f}%  {s['avg10']:>+6.2f}%  {s['avg20']:>+6.2f}%  {s['med10']:>+6.2f}%\n"
                    )

        # ── Quintile analysis on cts_slope for downtrend entries ──────────────
        out.write("\n── QUINTILE ANALYSIS (downtrend entries) ───────────────────────────────────────────\n")
        quintile_breakdown(all_down, "cts_slope", "cts_slope", out)
        quintile_breakdown(all_down, "cts_accel", "cts_accel", out)
        quintile_breakdown(all_down, "vel", "velocity_60_norm", out)
        quintile_breakdown(all_down, "pdd_120", "PDD-120", out)

        # ── Dropped signals: baseline fires, variant B doesn't ────────────────
        base_set = set(zip(variant_dfs[0]["sym"], variant_dfs[0]["date"]))
        for vi in range(1, len(VARIANT_NAMES)):
            vname = VARIANT_NAMES[vi]
            vdf = variant_dfs[vi]
            v_set = set(zip(vdf["sym"], vdf["date"]))
            dropped = variant_dfs[0][~variant_dfs[0].apply(lambda r: (r["sym"], r["date"]) in v_set, axis=1)]

            out.write(f"\n── DROPPED: baseline fires, {vname} doesn't ─────────────────────────────────\n")
            out.write(f"  Count: {len(dropped)}\n")
            if not dropped.empty:
                out.write(f"  Win 10d:  {(dropped['ret_10'] > 0).mean()*100:.1f}%\n")
                out.write(f"  Avg 10d:  {dropped['ret_10'].mean():+.2f}%\n")
                out.write(f"  Avg 20d:  {dropped['ret_20'].mean():+.2f}%\n\n")
                pd.set_option("display.max_rows", 200, "display.width", W,
                              "display.float_format", "{:.3f}".format)
                show_cols = ["sym", "date", "close", "regime", "cts", "cts_slope",
                             "vel", "vel_dp5", "pdd_120", "ret_5", "ret_10", "ret_20"]
                out.write(dropped[show_cols].to_string(index=False))
                out.write("\n")

    if skipped:
        out.write(f"\n── SKIPPED ({len(skipped)}) ─────────────────────────────────────────────────────\n")
        for s in skipped:
            out.write(f"  {s}\n")

    report = out.getvalue()
    with open(out_path, "w") as f:
        f.write(report)

    # Console summary
    lines = report.split("\n")
    for line in lines:
        if any(x in line for x in [
            "OVERALL SUMMARY", "DELTA FROM", "====", "Variant", "---",
            "baseline", "slope >", "Period", "Symbols", "Total", "downtrend",
            "DOWNTREND", "SLOPE", "slope <", "slope >=",
        ]):
            print(line)
    print(f"\nFull report -> {out_path}")


if __name__ == "__main__":
    main()
