"""
Gate 4 PDD-120 prototype — test whether Gate 4 can be relaxed when
delivery momentum confirms the breakout.

Variants:
  A  baseline     — current: pdd_120 >= pdd_threshold blocks entry
  B  waive_g3     — Gate 3 pass (vel_dp5>=4 + vel>-0.10) waives Gate 4
  C  delta        — block only if pdd_120 >= threshold AND pdd_delta_5 > 0
                    (divergence still widening)
  D  delta+vel    — block only if pdd_120 >= threshold AND pdd_delta_5 > 0
                    AND vel_dp5 < 4 (divergence widening + velocity not confirming)

Gates 1, 2, 3 use the new NextGen v2 logic (vel_dp5>=4, vel>-0.10).

Output: output/gate4_pdd_nifty500.txt

Usage:
    venv/bin/python3 scripts/gate4_pdd_nifty500.py
    venv/bin/python3 scripts/gate4_pdd_nifty500.py --watchlist "NIFTY 500"
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

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"
OUT_DIR = Path(__file__).resolve().parent.parent / "output"
OUT_DIR.mkdir(exist_ok=True)


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


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Add vel_dp5 and pdd_delta_5."""
    df = df.copy()
    # vel_dp5: velocity delta positive on n of last 5 bars
    vel_delta = df["velocity_60_norm"].diff()
    pos = vel_delta.apply(lambda x: 1 if x > 0 else 0)
    df["vel_dp5"] = pos.rolling(5, min_periods=5).sum()
    # pdd_delta_5: 5-bar change in pdd_120 (positive = divergence widening)
    df["pdd_delta_5"] = df["pdd_120"].diff(5)
    return df


def gates_123_pass(row: dict) -> bool:
    """Check Gates 1, 2, 3 (new v2 Gate 3)."""
    cts_accel = row.get("cts_accel", np.nan)
    accel_t   = row.get("cts_accel_threshold", np.nan)
    cts       = row.get("cts", np.nan)
    cts_buy_t = row.get("cts_buy_threshold", np.nan)
    vel       = row.get("velocity_60_norm", np.nan)
    dp5       = row.get("vel_dp5", 0)

    if any(np.isnan(v) for v in [cts_accel, accel_t, cts, cts_buy_t]):
        return False

    regime = str(row.get("regime", ""))
    is_bear = "bear" in regime.lower() or "down" in regime.lower()
    eff_t = accel_t * 0.8 if is_bear else accel_t

    # Gate 1
    if cts_accel <= eff_t:
        return False
    # Gate 2
    if cts < cts_buy_t:
        return False
    # Gate 3 (new): vel_dp4 + floor
    if np.isnan(vel) or np.isnan(dp5):
        return False
    if not (dp5 >= 4 and vel > -0.10):
        return False

    return True


# ── Gate 4 variants ──────────────────────────────────────────────────────────

VARIANT_NAMES = [
    "A  baseline (current)",
    "B  waive if G3 pass",
    "C  delta (block widening)",
    "D  delta + vel confirm",
]


def eval_variants(row: dict) -> list[bool]:
    """Returns a bool per variant: True = Gate 4 PASSES (entry allowed)."""
    pdd       = row.get("pdd_120", np.nan)
    pdd_t     = row.get("pdd_120_threshold", np.nan)
    pdd_d5    = row.get("pdd_delta_5", np.nan)
    vel       = row.get("velocity_60_norm", np.nan)
    dp5       = row.get("vel_dp5", 0)

    if np.isnan(pdd) or np.isnan(pdd_t):
        return [False] * len(VARIANT_NAMES)

    pdd_ok = pdd < pdd_t  # baseline: pdd below threshold
    pdd_widening = (not np.isnan(pdd_d5)) and pdd_d5 > 0
    g3_pass = (not np.isnan(vel)) and vel > -0.10 and dp5 >= 4

    return [
        pdd_ok,                                            # A: current
        pdd_ok or g3_pass,                                 # B: waive if G3 confirms
        pdd_ok or (not pdd_widening),                      # C: allow if divergence narrowing
        pdd_ok or (not pdd_widening) or g3_pass,           # D: allow if narrowing OR vel confirms
    ]


def scan_symbol(sym: str, start: str, end: str) -> list[dict]:
    result = DivergenceEngine(sym).run()
    df = result.ledger.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = enrich(df)
    df = df.reset_index(drop=True)

    window = df[(df["date"] >= start) & (df["date"] <= end)]
    records = df.to_dict("records")
    n = len(records)

    rows = []
    for i in window.index:
        row = records[i]
        if not gates_123_pass(row):
            continue

        flags = eval_variants(row)
        if not any(flags):
            continue

        fwd = {}
        for h in [5, 10, 20]:
            j = min(i + h, n - 1)
            c0, cf = row["close"], records[j]["close"]
            fwd[h] = round((cf / c0 - 1) * 100, 3) if c0 > 0 else np.nan

        entry = {
            "sym":    sym,
            "date":   row["date"].strftime("%Y-%m-%d"),
            "close":  row["close"],
            "regime": row.get("regime", ""),
            "cts":    round(row.get("cts", np.nan), 4),
            "cdvl":   round(row.get("cdvl", np.nan), 4),
            "vel":    round(row.get("velocity_60_norm", np.nan), 4),
            "vel_dp5": int(row.get("vel_dp5", 0)),
            "pdd_120": round(row.get("pdd_120", np.nan), 3),
            "pdd_t":   round(row.get("pdd_120_threshold", np.nan), 3),
            "pdd_d5":  round(row.get("pdd_delta_5", np.nan), 3) if not np.isnan(row.get("pdd_delta_5", np.nan)) else np.nan,
            "ret_5":  fwd[5],
            "ret_10": fwd[10],
            "ret_20": fwd[20],
        }
        for vi, vname in enumerate(VARIANT_NAMES):
            entry[f"v{vi}"] = int(flags[vi])
        rows.append(entry)
    return rows


def summary(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return {}
    n = len(df)
    return {
        "label": label, "n": n,
        "win5":  (df["ret_5"]  > 0).mean() * 100,
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
    parser.add_argument("--start",    default="2026-01-01")
    parser.add_argument("--end",      default="2026-03-15")
    args = parser.parse_args()

    out_path = OUT_DIR / "gate4_pdd_nifty500.txt"
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
            print(f"{len(rows)} bars")
        except Exception as e:
            skipped.append(f"{sym}: {e}")
            print(f"SKIP - {e}")

    df = pd.DataFrame(all_rows)

    out = StringIO()
    W = 120

    out.write("=" * W + "\n")
    out.write(f"  GATE 4 PDD-120 PROTOTYPE — {args.watchlist}\n")
    out.write(f"  Period: {args.start} -> {args.end}\n")
    out.write(f"  Symbols scanned: {len(symbols)}   Skipped: {len(skipped)}\n")
    out.write(f"  Total qualifying bars (gates 1+2+3 pass, any Gate 4 fires): {len(df)}\n")
    out.write("=" * W + "\n\n")

    if df.empty:
        out.write("No data.\n")
    else:
        # ── Overall summary table ─────────────────────────────────────────────
        out.write("── OVERALL SUMMARY ─────────────────────────────────────────────────────────────────\n\n")
        hdr = f"  {'Variant':<28} {'N':>6}  {'Win5d':>7}  {'Win10d':>7}  {'Win20d':>7}  {'Avg5d':>7}  {'Avg10d':>7}  {'Avg20d':>7}  {'Med10d':>7}\n"
        out.write(hdr)
        out.write("  " + "-" * (W - 2) + "\n")

        variant_dfs = {}
        for vi, vname in enumerate(VARIANT_NAMES):
            vdf = df[df[f"v{vi}"] == 1]
            variant_dfs[vi] = vdf
            s = summary(vdf, vname)
            if s:
                out.write(
                    f"  {s['label']:<28} {s['n']:>6}  "
                    f"{s['win5']:>6.1f}%  {s['win10']:>6.1f}%  {s['win20']:>6.1f}%  "
                    f"{s['avg5']:>+6.2f}%  {s['avg10']:>+6.2f}%  {s['avg20']:>+6.2f}%  {s['med10']:>+6.2f}%\n"
                )

        # ── Delta from baseline (A) ──────────────────────────────────────────
        out.write("\n── DELTA FROM BASELINE (A current) ─────────────────────────────────────────────────\n\n")
        s_base = summary(variant_dfs[0], "baseline")
        if s_base:
            out.write(f"  {'Variant':<28} {'dN':>6}  {'dWin10':>8}  {'dAvg10':>8}  {'dMed10':>8}\n")
            out.write("  " + "-" * 60 + "\n")
            for vi, vname in enumerate(VARIANT_NAMES):
                s = summary(variant_dfs[vi], vname)
                if s:
                    dn = s["n"] - s_base["n"]
                    dw = s["win10"] - s_base["win10"]
                    da = s["avg10"] - s_base["avg10"]
                    dm = s["med10"] - s_base["med10"]
                    out.write(f"  {vname:<28} {dn:>+6}  {dw:>+7.1f}%  {da:>+7.2f}%  {dm:>+7.2f}%\n")

        # ── Regime breakdown for each variant ─────────────────────────────────
        out.write("\n── REGIME BREAKDOWN ────────────────────────────────────────────────────────────────\n")
        for vi, vname in enumerate(VARIANT_NAMES):
            vdf = variant_dfs[vi]
            if vdf.empty:
                continue
            gb = vdf.groupby("regime", observed=True).agg(
                n=("ret_10", "count"),
                win10=("ret_10", lambda x: (x > 0).mean() * 100),
                avg10=("ret_10", "mean"),
                avg20=("ret_20", "mean"),
            ).reset_index().sort_values("n", ascending=False)
            out.write(f"\n  {vname}:\n")
            out.write(f"  {'Regime':<15} {'N':>6} {'Win10%':>8} {'Avg10%':>9} {'Avg20%':>9}\n")
            for _, r in gb.iterrows():
                out.write(f"  {str(r['regime']):<15} {int(r['n']):>6} {r['win10']:>7.1f}% {r['avg10']:>+8.2f}% {r['avg20']:>+8.2f}%\n")

        # ── PDD-120 quintile analysis for each variant ────────────────────────
        out.write("\n── QUINTILE ANALYSIS ───────────────────────────────────────────────────────────────\n")
        for vi, vname in enumerate(VARIANT_NAMES):
            vdf = variant_dfs[vi]
            out.write(f"\n  {vname}:")
            quintile_breakdown(vdf, "pdd_120", "PDD-120", out)
            quintile_breakdown(vdf, "pdd_d5",  "PDD delta-5", out)
            quintile_breakdown(vdf, "vel",     "velocity_60_norm", out)

        # ── Signals unique to relaxed variants vs baseline ────────────────────
        base_set = set(zip(variant_dfs[0]["sym"], variant_dfs[0]["date"]))

        for vi in range(1, len(VARIANT_NAMES)):
            vname = VARIANT_NAMES[vi]
            vdf = variant_dfs[vi]
            gained = vdf[~vdf.apply(lambda r: (r["sym"], r["date"]) in base_set, axis=1)]

            out.write(f"\n── GAINED SIGNALS: {vname} fires, baseline doesn't ──────────────────────────────\n")
            out.write(f"  Count: {len(gained)}\n")
            if not gained.empty:
                out.write(f"  Win 10d:  {(gained['ret_10'] > 0).mean()*100:.1f}%\n")
                out.write(f"  Avg 10d:  {gained['ret_10'].mean():+.2f}%\n")
                out.write(f"  Avg 20d:  {gained['ret_20'].mean():+.2f}%\n\n")
                pd.set_option("display.max_rows", 200, "display.width", W,
                              "display.float_format", "{:.3f}".format)
                show_cols = ["sym", "date", "close", "regime", "cts", "vel", "vel_dp5",
                             "pdd_120", "pdd_t", "pdd_d5", "ret_5", "ret_10", "ret_20"]
                out.write(gained[show_cols].to_string(index=False))
                out.write("\n")

        # ── Lost signals: baseline fires but best relaxed variant drops ───────
        # (shouldn't happen since all relaxed variants are supersets of baseline,
        #  but include for safety)

    if skipped:
        out.write(f"\n── SKIPPED ({len(skipped)}) ─────────────────────────────────────────────────────\n")
        for s in skipped:
            out.write(f"  {s}\n")

    report = out.getvalue()
    with open(out_path, "w") as f:
        f.write(report)

    # Print summary table to console
    lines = report.split("\n")
    for line in lines:
        if any(x in line for x in ["OVERALL SUMMARY", "DELTA FROM", "====", "Variant", "---",
                                     "baseline", "waive", "delta", "Period", "Symbols", "Total"]):
            print(line)
    print(f"\nFull report -> {out_path}")


if __name__ == "__main__":
    main()
