"""
Gate 3 vel_dp4 prototype — NIFTY 500 validation.

Compares current level-based Gate 3 (cdvl > 0 OR velocity > 0)
against the proposed vel_dp4 gate (velocity_60_norm delta positive 4 of last 5 bars).

Gates 1, 2, 4 remain unchanged (cts_accel threshold, cts buy threshold, pdd_120 threshold).

Output: results written to output/gate3_vel_dp4_nifty500.txt

Usage:
    venv/bin/python3 scripts/gate3_vel_dp4_nifty500.py
    venv/bin/python3 scripts/gate3_vel_dp4_nifty500.py --watchlist "NIFTY 500"
    venv/bin/python3 scripts/gate3_vel_dp4_nifty500.py --watchlist "NIFTY 500" --start 2025-01-01 --end 2026-03-15
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import traceback
from pathlib import Path
from io import StringIO

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"
OUT_DIR  = Path(__file__).resolve().parent.parent / "output"
OUT_DIR.mkdir(exist_ok=True)


# ── Watchlist ─────────────────────────────────────────────────────────────────

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


# ── Feature enrichment ────────────────────────────────────────────────────────

def enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["vel_delta"] = df["velocity_60_norm"].diff()
    pos = df["vel_delta"].apply(lambda x: 1 if x > 0 else 0)
    df["vel_dp5"] = pos.rolling(5, min_periods=5).sum()
    return df


# ── Gate checks ───────────────────────────────────────────────────────────────

def gates_124_pass(row: dict) -> bool:
    """Gates 1, 2, 4 — unchanged from the NextGen signal."""
    cts_accel   = row.get("cts_accel", np.nan)
    accel_t     = row.get("cts_accel_threshold", np.nan)
    cts         = row.get("cts", np.nan)
    cts_buy_t   = row.get("cts_buy_threshold", np.nan)
    pdd         = row.get("pdd_120", np.nan)
    pdd_t       = row.get("pdd_120_threshold", np.nan)

    if any(np.isnan(v) for v in [cts_accel, accel_t, cts, cts_buy_t, pdd, pdd_t]):
        return False

    regime = str(row.get("regime", ""))
    is_bear = "bear" in regime.lower() or "down" in regime.lower()
    effective_accel_t = accel_t * 0.8 if is_bear else accel_t

    return (cts_accel > effective_accel_t) and (cts >= cts_buy_t) and (pdd < pdd_t)


def gate3_level(row: dict) -> bool:
    """Current Gate 3: cdvl > 0 OR velocity_60_norm > 0 (level-based)."""
    cdvl = row.get("cdvl", np.nan)
    vel  = row.get("velocity_60_norm", np.nan)
    return (not np.isnan(cdvl) and cdvl > 0) or (not np.isnan(vel) and vel > 0)


def gate3_vel_dp4(row: dict) -> bool:
    """Proposed Gate 3: velocity_60_norm delta positive on 4 of the last 5 bars."""
    return row.get("vel_dp5", 0) >= 4


# ── Per-symbol scan ───────────────────────────────────────────────────────────

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
        if not gates_124_pass(row):
            continue

        lvl  = gate3_level(row)
        dp4  = gate3_vel_dp4(row)

        if not lvl and not dp4:
            continue  # neither gate fires — skip

        # Forward returns
        fwd = {}
        for h in [5, 10, 20]:
            j = min(i + h, n - 1)
            c0, cf = row["close"], records[j]["close"]
            fwd[h] = round((cf / c0 - 1) * 100, 3) if c0 > 0 else np.nan

        rows.append({
            "sym":       sym,
            "date":      row["date"].strftime("%Y-%m-%d"),
            "close":     row["close"],
            "regime":    row.get("regime", ""),
            "cts":       round(row.get("cts", np.nan), 4),
            "cts_accel": round(row.get("cts_accel", np.nan), 5),
            "cdvl":      round(row.get("cdvl", np.nan), 4),
            "vel":       round(row.get("velocity_60_norm", np.nan), 4),
            "vel_dp5":   int(row.get("vel_dp5", 0)),
            "pdd_120":   round(row.get("pdd_120", np.nan), 3),
            "gate_level": int(lvl),
            "gate_dp4":   int(dp4),
            "ret_5":     fwd[5],
            "ret_10":    fwd[10],
            "ret_20":    fwd[20],
        })
    return rows


# ── Summary stats ─────────────────────────────────────────────────────────────

def summary(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return {}
    n   = len(df)
    w5  = (df["ret_5"]  > 0).mean() * 100
    w10 = (df["ret_10"] > 0).mean() * 100
    w20 = (df["ret_20"] > 0).mean() * 100
    a5  = df["ret_5"].mean()
    a10 = df["ret_10"].mean()
    a20 = df["ret_20"].mean()
    m10 = df["ret_10"].median()
    return {
        "label": label, "n": n,
        "win5": w5, "win10": w10, "win20": w20,
        "avg5": a5, "avg10": a10, "avg20": a20, "med10": m10,
    }


def regime_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    gb = df.groupby("regime", observed=True).agg(
        n=("ret_10", "count"),
        win10=("ret_10", lambda x: (x > 0).mean() * 100),
        avg10=("ret_10", "mean"),
        avg20=("ret_20", "mean"),
    ).reset_index().sort_values("n", ascending=False)
    return gb


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
    qsumm = valid.groupby("q", observed=False).agg(
        n=("ret_10", "count"),
        win=("ret_10", lambda x: (x > 0).mean() * 100),
        avg=("ret_10", "mean"),
    )
    out.write(f"\n  Breakdown by {label} ({n_bins} bins, {n_unique} unique values):\n")
    out.write(f"  {'Bin':<4} {'N':>5} {'Win10%':>8} {'Avg10%':>8}\n")
    for q, row in qsumm.iterrows():
        out.write(f"  {q:<4} {int(row['n']):>5} {row['win']:>7.1f}% {row['avg']:>+7.2f}%\n")


def print_summary_row(s: dict, out: StringIO) -> None:
    out.write(
        f"  {s['label']:<30} {s['n']:>6}  "
        f"{s['win5']:>6.1f}%  {s['win10']:>6.1f}%  {s['win20']:>6.1f}%  "
        f"{s['avg5']:>+6.2f}%  {s['avg10']:>+6.2f}%  {s['avg20']:>+6.2f}%  {s['med10']:>+6.2f}%\n"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", default="NIFTY 500")
    parser.add_argument("--start",     default="2026-01-01")
    parser.add_argument("--end",       default="2026-03-15")
    args = parser.parse_args()

    out_path = OUT_DIR / "gate3_vel_dp4_nifty500.txt"
    symbols  = get_symbols(args.watchlist)

    print(f"Scanning {len(symbols)} symbols from '{args.watchlist}' ({args.start} → {args.end})")
    print(f"Output → {out_path}\n")

    all_rows: list[dict] = []
    skipped: list[str] = []

    for idx, sym in enumerate(symbols, 1):
        print(f"  [{idx:>3}/{len(symbols)}] {sym:<20}", end=" ", flush=True)
        try:
            rows = scan_symbol(sym, args.start, args.end)
            all_rows.extend(rows)
            print(f"{len(rows)} qualifying bars")
        except Exception as e:
            skipped.append(f"{sym}: {e}")
            print(f"SKIP — {e}")

    df = pd.DataFrame(all_rows)

    # ── Build report ─────────────────────────────────────────────────────────
    out = StringIO()

    W = 110
    out.write("=" * W + "\n")
    out.write(f"  GATE 3 vel_dp4 PROTOTYPE — {args.watchlist}\n")
    out.write(f"  Period: {args.start} → {args.end}\n")
    out.write(f"  Symbols scanned: {len(symbols)}   Skipped: {len(skipped)}\n")
    out.write(f"  Total qualifying bars (gates 1+2+4 pass, at least one Gate 3 fires): {len(df)}\n")
    out.write("=" * W + "\n\n")

    if df.empty:
        out.write("No data.\n")
    else:
        # Split by which gate fired
        lvl_df  = df[df["gate_level"] == 1].copy()
        dp4_df  = df[df["gate_dp4"]   == 1].copy()
        both_df = df[(df["gate_level"] == 1) & (df["gate_dp4"] == 1)].copy()
        dp4_only = df[(df["gate_dp4"] == 1) & (df["gate_level"] == 0)].copy()  # delta fires, level doesn't
        lvl_only = df[(df["gate_level"] == 1) & (df["gate_dp4"] == 0)].copy()  # level fires, delta doesn't

        s_lvl     = summary(lvl_df,   "A  level  (current Gate 3)")
        s_dp4     = summary(dp4_df,   "B  vel_dp4 (proposed Gate 3)")
        s_both    = summary(both_df,  "C  both agree")
        s_dp4only = summary(dp4_only, "D  dp4 only  (new catches)")
        s_lvlonly = summary(lvl_only, "E  level only (dp4 rejects)")

        hdr = f"  {'Variant':<30} {'N':>6}  {'Win5d':>7}  {'Win10d':>7}  {'Win20d':>7}  {'Avg5d':>7}  {'Avg10d':>7}  {'Avg20d':>7}  {'Med10d':>7}\n"
        sep = "  " + "-" * (W - 2) + "\n"

        out.write("── OVERALL SUMMARY ─────────────────────────────────────────────────────────\n\n")
        out.write(hdr)
        out.write(sep)
        for s in [s_lvl, s_dp4, s_both, s_dp4only, s_lvlonly]:
            if s:
                print_summary_row(s, out)

        # ── Regime breakdown ─────────────────────────────────────────────────
        out.write("\n── REGIME BREAKDOWN ────────────────────────────────────────────────────────\n")
        for label, sub in [("A  level", lvl_df), ("B  vel_dp4", dp4_df)]:
            rb = regime_breakdown(sub)
            if not rb.empty:
                out.write(f"\n  {label}:\n")
                out.write(f"  {'Regime':<20} {'N':>5} {'Win10%':>8} {'Avg10%':>8} {'Avg20%':>8}\n")
                for _, r in rb.iterrows():
                    out.write(f"  {str(r['regime']):<20} {int(r['n']):>5} {r['win10']:>7.1f}% {r['avg10']:>+7.2f}% {r['avg20']:>+7.2f}%\n")

        # ── Quintile analysis on key features ────────────────────────────────
        out.write("\n── QUINTILE ANALYSIS (vel_dp4 signals) ─────────────────────────────────────\n")
        quintile_breakdown(dp4_df, "vel",     "velocity_60_norm level", out)
        quintile_breakdown(dp4_df, "vel_dp5", "vel_dp5 count (4 or 5)", out)
        quintile_breakdown(dp4_df, "cts",     "CTS level", out)
        quintile_breakdown(dp4_df, "pdd_120", "PDD-120", out)

        # ── dp4-only signals detail ───────────────────────────────────────────
        out.write("\n── NEW CATCHES: dp4 fires, level doesn't (early entry candidates) ──────────\n")
        out.write(f"  Count: {len(dp4_only)}\n")
        if not dp4_only.empty:
            out.write(f"  Win 10d: {(dp4_only['ret_10'] > 0).mean()*100:.1f}%\n")
            out.write(f"  Avg 10d: {dp4_only['ret_10'].mean():+.2f}%\n")
            out.write(f"  Avg 20d: {dp4_only['ret_20'].mean():+.2f}%\n\n")
            pd.set_option("display.max_rows", 500, "display.width", W,
                          "display.float_format", "{:.3f}".format)
            out.write(dp4_only[["sym","date","close","regime","cts","cdvl","vel","vel_dp5",
                                 "pdd_120","ret_5","ret_10","ret_20"]].to_string(index=False))
            out.write("\n")

        # ── level-only signals detail ─────────────────────────────────────────
        out.write("\n── REJECTED BY dp4, KEPT BY LEVEL (signals dp4 drops) ─────────────────────\n")
        out.write(f"  Count: {len(lvl_only)}\n")
        if not lvl_only.empty:
            out.write(f"  Win 10d: {(lvl_only['ret_10'] > 0).mean()*100:.1f}%\n")
            out.write(f"  Avg 10d: {lvl_only['ret_10'].mean():+.2f}%\n")
            out.write(f"  Avg 20d: {lvl_only['ret_20'].mean():+.2f}%\n\n")
            out.write(lvl_only[["sym","date","close","regime","cts","cdvl","vel","vel_dp5",
                                 "pdd_120","ret_5","ret_10","ret_20"]].to_string(index=False))
            out.write("\n")

        # ── Per-symbol win rate table ─────────────────────────────────────────
        out.write("\n── PER-SYMBOL COMPARISON (≥3 signals in at least one gate) ────────────────\n")
        sym_rows = []
        for sym in df["sym"].unique():
            s_lvl_sym = df[(df["sym"] == sym) & (df["gate_level"] == 1)]
            s_dp4_sym = df[(df["sym"] == sym) & (df["gate_dp4"]  == 1)]
            if len(s_lvl_sym) < 3 and len(s_dp4_sym) < 3:
                continue
            sym_rows.append({
                "sym": sym,
                "n_level": len(s_lvl_sym),
                "win10_level": (s_lvl_sym["ret_10"] > 0).mean() * 100 if len(s_lvl_sym) else np.nan,
                "avg10_level": s_lvl_sym["ret_10"].mean() if len(s_lvl_sym) else np.nan,
                "n_dp4":   len(s_dp4_sym),
                "win10_dp4": (s_dp4_sym["ret_10"] > 0).mean() * 100 if len(s_dp4_sym) else np.nan,
                "avg10_dp4": s_dp4_sym["ret_10"].mean() if len(s_dp4_sym) else np.nan,
            })
        if sym_rows:
            sdf = pd.DataFrame(sym_rows).sort_values("avg10_dp4", ascending=False)
            out.write(f"\n  {'Sym':<16} {'N_lvl':>6} {'Win_lvl%':>9} {'Avg10_lvl%':>11}  {'N_dp4':>6} {'Win_dp4%':>9} {'Avg10_dp4%':>11}\n")
            out.write("  " + "-" * 75 + "\n")
            for _, r in sdf.iterrows():
                def fmt(v): return f"{v:>+7.2f}%" if not np.isnan(v) else "     n/a"
                def fmtw(v): return f"{v:>7.1f}%" if not np.isnan(v) else "     n/a"
                out.write(
                    f"  {r['sym']:<16} {int(r['n_level']) if not np.isnan(r['n_level']) else 0:>6} "
                    f"{fmtw(r['win10_level']):>9} {fmt(r['avg10_level']):>11}  "
                    f"{int(r['n_dp4']) if not np.isnan(r['n_dp4']) else 0:>6} "
                    f"{fmtw(r['win10_dp4']):>9} {fmt(r['avg10_dp4']):>11}\n"
                )

    if skipped:
        out.write(f"\n── SKIPPED ({len(skipped)}) ──────────────────────────────────────────────────\n")
        for s in skipped:
            out.write(f"  {s}\n")

    report = out.getvalue()

    # Write to file
    with open(out_path, "w") as f:
        f.write(report)

    # Also print to console
    print("\n" + report)
    print(f"\n✓ Written to {out_path}")


if __name__ == "__main__":
    main()
