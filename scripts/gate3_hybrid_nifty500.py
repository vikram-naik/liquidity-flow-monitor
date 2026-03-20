"""
Gate 3 hybrid prototype — vel_dp4 + velocity floor.

Tests multiple Gate 3 variants that combine:
  - vel_dp4: velocity_60_norm delta positive on 4+ of last 5 bars (directional)
  - velocity floor: velocity_60_norm above some threshold (level filter)

Floors tested:
  A  level          — current gate (cdvl > 0 OR vel > 0)
  B  dp4_only       — vel_dp4 alone (no floor)
  C  dp4 + vel>-0.3 — dp4 AND velocity above -0.3 (loose floor)
  D  dp4 + vel>-0.2 — dp4 AND velocity above -0.2
  E  dp4 + vel>-0.1 — dp4 AND velocity above -0.1
  F  dp4 + vel>p30  — dp4 AND velocity above rolling 30th percentile (adaptive)
  G  dp4 + vel>p50  — dp4 AND velocity above rolling 50th percentile (adaptive)
  H  both_agree     — current level gate AND vel_dp4 (intersection)

Gates 1, 2, 4 unchanged from NextGen signal.

Output: output/gate3_hybrid_nifty500.txt

Usage:
    venv/bin/python3 scripts/gate3_hybrid_nifty500.py
    venv/bin/python3 scripts/gate3_hybrid_nifty500.py --watchlist "NIFTY 500"
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
OUT_DIR  = Path(__file__).resolve().parent.parent / "output"
OUT_DIR.mkdir(exist_ok=True)

VEL_PCTILE_WINDOW = 60


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
    df = df.copy()
    df["vel_delta"] = df["velocity_60_norm"].diff()
    pos = df["vel_delta"].apply(lambda x: 1 if x > 0 else 0)
    df["vel_dp5"] = pos.rolling(5, min_periods=5).sum()
    # Adaptive velocity floors
    vel = df["velocity_60_norm"]
    df["vel_p30"] = vel.rolling(VEL_PCTILE_WINDOW, min_periods=30).quantile(0.30)
    df["vel_p50"] = vel.rolling(VEL_PCTILE_WINDOW, min_periods=30).quantile(0.50)
    return df


def gates_124_pass(row: dict) -> bool:
    cts_accel = row.get("cts_accel", np.nan)
    accel_t   = row.get("cts_accel_threshold", np.nan)
    cts       = row.get("cts", np.nan)
    cts_buy_t = row.get("cts_buy_threshold", np.nan)
    pdd       = row.get("pdd_120", np.nan)
    pdd_t     = row.get("pdd_120_threshold", np.nan)

    if any(np.isnan(v) for v in [cts_accel, accel_t, cts, cts_buy_t, pdd, pdd_t]):
        return False

    regime = str(row.get("regime", ""))
    is_bear = "bear" in regime.lower() or "down" in regime.lower()
    eff_t = accel_t * 0.8 if is_bear else accel_t

    return (cts_accel > eff_t) and (cts >= cts_buy_t) and (pdd < pdd_t)


# ── Gate 3 variants ──────────────────────────────────────────────────────────

VARIANT_NAMES = [
    "A  level (current)",
    "B  dp4 only",
    "C  dp4 + vel>-0.30",
    "D  dp4 + vel>-0.20",
    "E  dp4 + vel>-0.10",
    "F  dp4 + vel>p30",
    "G  dp4 + vel>p50",
    "H  level AND dp4",
]

def eval_variants(row: dict) -> list[bool]:
    """Returns a bool per variant in VARIANT_NAMES order."""
    cdvl = row.get("cdvl", np.nan)
    vel  = row.get("velocity_60_norm", np.nan)
    dp5  = row.get("vel_dp5", 0)
    vp30 = row.get("vel_p30", np.nan)
    vp50 = row.get("vel_p50", np.nan)

    level = (not np.isnan(cdvl) and cdvl > 0) or (not np.isnan(vel) and vel > 0)
    dp4   = dp5 >= 4
    vel_ok = not np.isnan(vel)

    return [
        level,                                              # A
        dp4,                                                # B
        dp4 and vel_ok and vel > -0.30,                     # C
        dp4 and vel_ok and vel > -0.20,                     # D
        dp4 and vel_ok and vel > -0.10,                     # E
        dp4 and vel_ok and not np.isnan(vp30) and vel > vp30,  # F
        dp4 and vel_ok and not np.isnan(vp50) and vel > vp50,  # G
        level and dp4,                                      # H
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
        if not gates_124_pass(row):
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
            "vel_p30": round(row.get("vel_p30", np.nan), 4) if not np.isnan(row.get("vel_p30", np.nan)) else np.nan,
            "vel_p50": round(row.get("vel_p50", np.nan), 4) if not np.isnan(row.get("vel_p50", np.nan)) else np.nan,
            "pdd_120": round(row.get("pdd_120", np.nan), 3),
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
    qsumm = valid.groupby("q", observed=False).agg(
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

    out_path = OUT_DIR / "gate3_hybrid_nifty500.txt"
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
    out.write(f"  GATE 3 HYBRID PROTOTYPE — {args.watchlist}\n")
    out.write(f"  Period: {args.start} -> {args.end}\n")
    out.write(f"  Symbols scanned: {len(symbols)}   Skipped: {len(skipped)}\n")
    out.write(f"  Total qualifying bars (gates 1+2+4 pass, any Gate 3 fires): {len(df)}\n")
    out.write(f"  Velocity percentile window: {VEL_PCTILE_WINDOW} bars\n")
    out.write("=" * W + "\n\n")

    if df.empty:
        out.write("No data.\n")
    else:
        # ── Overall summary table ─────────────────────────────────────────────
        out.write("── OVERALL SUMMARY ─────────────────────────────────────────────────────────────────\n\n")
        hdr = f"  {'Variant':<25} {'N':>6}  {'Win5d':>7}  {'Win10d':>7}  {'Win20d':>7}  {'Avg5d':>7}  {'Avg10d':>7}  {'Avg20d':>7}  {'Med10d':>7}\n"
        out.write(hdr)
        out.write("  " + "-" * (W - 2) + "\n")

        variant_dfs = {}
        for vi, vname in enumerate(VARIANT_NAMES):
            vdf = df[df[f"v{vi}"] == 1]
            variant_dfs[vi] = vdf
            s = summary(vdf, vname)
            if s:
                out.write(
                    f"  {s['label']:<25} {s['n']:>6}  "
                    f"{s['win5']:>6.1f}%  {s['win10']:>6.1f}%  {s['win20']:>6.1f}%  "
                    f"{s['avg5']:>+6.2f}%  {s['avg10']:>+6.2f}%  {s['avg20']:>+6.2f}%  {s['med10']:>+6.2f}%\n"
                )

        # ── Delta from baseline (A) ──────────────────────────────────────────
        out.write("\n── DELTA FROM BASELINE (A level) ───────────────────────────────────────────────────\n\n")
        s_base = summary(variant_dfs[0], "baseline")
        if s_base:
            out.write(f"  {'Variant':<25} {'dN':>6}  {'dWin10':>8}  {'dAvg10':>8}  {'dMed10':>8}\n")
            out.write("  " + "-" * 60 + "\n")
            for vi, vname in enumerate(VARIANT_NAMES):
                s = summary(variant_dfs[vi], vname)
                if s:
                    dn = s["n"] - s_base["n"]
                    dw = s["win10"] - s_base["win10"]
                    da = s["avg10"] - s_base["avg10"]
                    dm = s["med10"] - s_base["med10"]
                    out.write(f"  {vname:<25} {dn:>+6}  {dw:>+7.1f}%  {da:>+7.2f}%  {dm:>+7.2f}%\n")

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

        # ── Quintile analysis for best hybrid variant ─────────────────────────
        # Find the variant with best avg10 (excluding A baseline)
        best_vi = max(range(1, len(VARIANT_NAMES)), key=lambda vi: summary(variant_dfs[vi], "").get("avg10", -999))
        best_name = VARIANT_NAMES[best_vi]
        best_df = variant_dfs[best_vi]

        out.write(f"\n── QUINTILE ANALYSIS (best variant: {best_name}) ─────────────────────────────────\n")
        quintile_breakdown(best_df, "vel",     "velocity_60_norm level", out)
        quintile_breakdown(best_df, "cts",     "CTS level", out)
        quintile_breakdown(best_df, "pdd_120", "PDD-120", out)
        quintile_breakdown(best_df, "cdvl",    "CDVL level", out)

        # ── Signals unique to best hybrid vs level (early catches) ────────────
        level_set = set(zip(variant_dfs[0]["sym"], variant_dfs[0]["date"]))
        best_only = best_df[~best_df.apply(lambda r: (r["sym"], r["date"]) in level_set, axis=1)]
        level_only = variant_dfs[0][~variant_dfs[0].apply(lambda r: (r["sym"], r["date"]) in set(zip(best_df["sym"], best_df["date"])), axis=1)]

        out.write(f"\n── EARLY CATCHES: {best_name} fires, level doesn't ───────────────────────────────\n")
        out.write(f"  Count: {len(best_only)}\n")
        if not best_only.empty:
            out.write(f"  Win 10d:  {(best_only['ret_10'] > 0).mean()*100:.1f}%\n")
            out.write(f"  Avg 10d:  {best_only['ret_10'].mean():+.2f}%\n")
            out.write(f"  Avg 20d:  {best_only['ret_20'].mean():+.2f}%\n\n")
            pd.set_option("display.max_rows", 200, "display.width", W,
                          "display.float_format", "{:.3f}".format)
            show_cols = ["sym","date","close","regime","cts","cdvl","vel","vel_dp5","pdd_120","ret_5","ret_10","ret_20"]
            out.write(best_only[show_cols].to_string(index=False))
            out.write("\n")

        out.write(f"\n── DROPPED: level fires, {best_name} doesn't ─────────────────────────────────────\n")
        out.write(f"  Count: {len(level_only)}\n")
        if not level_only.empty:
            out.write(f"  Win 10d:  {(level_only['ret_10'] > 0).mean()*100:.1f}%\n")
            out.write(f"  Avg 10d:  {level_only['ret_10'].mean():+.2f}%\n")
            out.write(f"  Avg 20d:  {level_only['ret_20'].mean():+.2f}%\n")

        # ── Per-symbol comparison: best hybrid vs level ───────────────────────
        out.write(f"\n── PER-SYMBOL: level vs {best_name} (>=3 signals either side) ─────────────────────\n\n")
        sym_rows = []
        for sym in df["sym"].unique():
            s_l = variant_dfs[0][variant_dfs[0]["sym"] == sym]
            s_b = best_df[best_df["sym"] == sym]
            if len(s_l) < 3 and len(s_b) < 3:
                continue
            sym_rows.append({
                "sym": sym,
                "n_lvl": len(s_l),
                "w10_lvl": (s_l["ret_10"] > 0).mean() * 100 if len(s_l) else np.nan,
                "a10_lvl": s_l["ret_10"].mean() if len(s_l) else np.nan,
                "n_hyb": len(s_b),
                "w10_hyb": (s_b["ret_10"] > 0).mean() * 100 if len(s_b) else np.nan,
                "a10_hyb": s_b["ret_10"].mean() if len(s_b) else np.nan,
            })
        if sym_rows:
            sdf = pd.DataFrame(sym_rows).sort_values("a10_hyb", ascending=False)
            out.write(f"  {'Sym':<16} {'N_lvl':>6} {'Win_lvl%':>9} {'Avg10_lvl%':>11}  {'N_hyb':>6} {'Win_hyb%':>9} {'Avg10_hyb%':>11}\n")
            out.write("  " + "-" * 80 + "\n")
            for _, r in sdf.iterrows():
                def f(v): return f"{v:>+7.2f}%" if not np.isnan(v) else "     n/a"
                def fw(v): return f"{v:>7.1f}%" if not np.isnan(v) else "     n/a"
                nl = int(r["n_lvl"]) if not np.isnan(r["n_lvl"]) else 0
                nh = int(r["n_hyb"]) if not np.isnan(r["n_hyb"]) else 0
                out.write(
                    f"  {r['sym']:<16} {nl:>6} {fw(r['w10_lvl']):>9} {f(r['a10_lvl']):>11}  "
                    f"{nh:>6} {fw(r['w10_hyb']):>9} {f(r['a10_hyb']):>11}\n"
                )

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
                                     "level", "dp4", "vel>", "Period", "Symbols", "Total", "Velocity"]):
            print(line)
    print(f"\nFull report -> {out_path}")


if __name__ == "__main__":
    main()
