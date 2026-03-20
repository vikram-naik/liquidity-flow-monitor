"""
PSZ Complement Study — find signals PSZ catches that CTS -1/+1 misses.

Compares:
  A) CTS -1/+1 (current best: CTS<=-1, coh<=0.3, pdd>-10, no uptrend)
  B) PSZ crossing -0.25 upward (from backtest_cts_psz findings)
  C) PSZ at psz_buy_threshold (P10) with coherence/pdd gates

Goal: quantify non-overlap and whether PSZ-only signals add value.

Usage:
    venv/bin/python3 scripts/psz_complement_study.py
    venv/bin/python3 scripts/psz_complement_study.py --watchlist "NIFTY 500"
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"

START = "2025-01-01"
END = "2026-03-15"
FWD_BARS = [5, 10, 20]  # forward-return horizons


def get_watchlist_symbols(name: str) -> list[str]:
    db = sqlite3.connect(str(DB_PATH))
    row = db.execute("SELECT id FROM watchlists WHERE name = ?", (name,)).fetchone()
    if not row:
        avail = [r[0] for r in db.execute("SELECT name FROM watchlists ORDER BY name").fetchall()]
        db.close()
        print(f"Watchlist '{name}' not found. Available: {avail}")
        sys.exit(1)
    symbols = [
        r[0] for r in db.execute(
            "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY display_order",
            (row[0],),
        ).fetchall()
    ]
    db.close()
    return symbols


def tag_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Tag each bar with signal types it qualifies for."""
    df = df.copy()

    cts = df["cts"].values if "cts" in df else np.full(len(df), np.nan)
    coh = df["coherence"].values if "coherence" in df else np.full(len(df), np.nan)
    pdd = df["pdd_120"].values if "pdd_120" in df else np.full(len(df), np.nan)
    regime = df["regime"].values if "regime" in df else np.full(len(df), "")
    psz = df["price_slope_z"].values if "price_slope_z" in df else np.full(len(df), np.nan)
    psz_bt = df["psz_buy_threshold"].values if "psz_buy_threshold" in df else np.full(len(df), np.nan)
    psz_v = df["psz_v"].values if "psz_v" in df else np.full(len(df), np.nan)
    close = df["close"].values

    n = len(df)

    # Forward returns
    for h in FWD_BARS:
        fwd = np.full(n, np.nan)
        for i in range(n - h):
            if close[i] > 0:
                fwd[i] = (close[i + h] / close[i] - 1) * 100
        df[f"fwd_{h}"] = fwd

    # Signal A: CTS -1/+1 (current best)
    sig_a = np.zeros(n, dtype=bool)
    for i in range(n):
        if np.isnan(cts[i]) or np.isnan(coh[i]):
            continue
        if cts[i] <= -1.0 and coh[i] <= 0.3:
            if not (not np.isnan(pdd[i]) and pdd[i] <= -10.0):
                if regime[i] != "uptrend":
                    sig_a[i] = True
    df["sig_cts"] = sig_a

    # Signal B: PSZ crosses psz_buy_threshold upward
    sig_b = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isnan(psz[i]) or np.isnan(psz_bt[i]) or np.isnan(psz[i-1]):
            continue
        # PSZ was below threshold, now at or above
        if psz[i-1] < psz_bt[i] and psz[i] >= psz_bt[i]:
            if regime[i] != "uptrend":
                sig_b[i] = True
    df["sig_psz_cross"] = sig_b

    # Signal C: PSZ at buy_threshold level (like CTS -1 but for PSZ)
    sig_c = np.zeros(n, dtype=bool)
    for i in range(n):
        if np.isnan(psz[i]) or np.isnan(psz_bt[i]):
            continue
        if psz[i] <= psz_bt[i] and coh[i] <= 0.3:
            if not (not np.isnan(pdd[i]) and pdd[i] <= -10.0):
                if regime[i] != "uptrend":
                    sig_c[i] = True
    df["sig_psz_level"] = sig_c

    # Signal D: PSZ at buy_threshold AND CTS NOT at -1 (pure complement)
    df["sig_psz_only"] = df["sig_psz_level"] & ~df["sig_cts"]

    # Signal E: PSZ crossing upward AND CTS NOT at -1
    df["sig_psz_cross_only"] = df["sig_psz_cross"] & ~df["sig_cts"]

    return df


def analyze_signal(df: pd.DataFrame, col: str, label: str):
    """Analyze forward returns for a signal column."""
    mask = df[col]
    n = mask.sum()
    if n == 0:
        print(f"\n{label}: No signals.")
        return

    print(f"\n{'='*60}")
    print(f"  {label} (N={n})")
    print(f"{'='*60}")

    rows = []
    for h in FWD_BARS:
        fwd_col = f"fwd_{h}"
        vals = df.loc[mask, fwd_col].dropna()
        if len(vals) == 0:
            continue
        win_pct = (vals > 0).mean() * 100
        avg = vals.mean()
        med = vals.median()
        rows.append({
            "Horizon": f"{h}-bar",
            "N": len(vals),
            "Win%": f"{win_pct:.1f}%",
            "Avg PnL%": f"{avg:+.2f}%",
            "Med PnL%": f"{med:+.2f}%",
            "Avg Win%": f"{vals[vals > 0].mean():+.2f}%" if (vals > 0).any() else "—",
            "Avg Loss%": f"{vals[vals <= 0].mean():+.2f}%" if (vals <= 0).any() else "—",
        })

    print(tabulate(rows, headers="keys", tablefmt="simple"))

    # Regime breakdown at 10-bar
    if "fwd_10" in df.columns and "regime" in df.columns:
        regime_data = df.loc[mask, ["regime", "fwd_10"]].dropna(subset=["fwd_10"])
        if len(regime_data) > 0:
            regime_agg = (
                regime_data.groupby("regime")
                .agg(n=("fwd_10", "size"), avg=("fwd_10", "mean"),
                     win=("fwd_10", lambda x: f"{(x > 0).mean() * 100:.1f}%"))
                .reset_index()
                .sort_values("n", ascending=False)
            )
            regime_agg["avg"] = regime_agg["avg"].apply(lambda x: f"{x:+.2f}%")
            print(f"\n  Regime breakdown (10-bar fwd):")
            print(tabulate(regime_agg.values, headers=["Regime", "N", "Avg PnL%", "Win%"],
                           tablefmt="simple"))

    # CTS distribution at signal time
    if "cts" in df.columns:
        cts_vals = df.loc[mask, "cts"].dropna()
        if len(cts_vals) > 0:
            print(f"\n  CTS at signal: mean={cts_vals.mean():.3f}, "
                  f"median={cts_vals.median():.3f}, "
                  f"min={cts_vals.min():.3f}, max={cts_vals.max():.3f}")

    # PSZ distribution at signal time
    if "price_slope_z" in df.columns:
        psz_vals = df.loc[mask, "price_slope_z"].dropna()
        if len(psz_vals) > 0:
            print(f"  PSZ at signal: mean={psz_vals.mean():.3f}, "
                  f"median={psz_vals.median():.3f}, "
                  f"min={psz_vals.min():.3f}, max={psz_vals.max():.3f}")


def main():
    parser = argparse.ArgumentParser(description="PSZ complement study")
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--start", default=START)
    parser.add_argument("--end", default=END)
    args = parser.parse_args()

    symbols = get_watchlist_symbols(args.watchlist)

    print(f"PSZ Complement Study")
    print(f"Watchlist: {args.watchlist} ({len(symbols)} symbols)")
    print(f"Period: {args.start} to {args.end}")
    print(f"Forward return horizons: {FWD_BARS}")

    all_dfs = []
    for i, sym in enumerate(symbols, 1):
        print(f"  [{i}/{len(symbols)}] {sym}...", end=" ", flush=True)
        try:
            engine = DivergenceEngine(sym, start_date=args.start, end_date=args.end)
            result = engine.run()
            df = tag_signals(result.ledger)
            df["symbol"] = sym
            all_dfs.append(df)
            a = df["sig_cts"].sum()
            b = df["sig_psz_only"].sum()
            print(f"CTS={a}, PSZ-only={b}")
        except Exception as e:
            print(f"SKIP — {e}")

    if not all_dfs:
        print("No data.")
        return

    combined = pd.concat(all_dfs, ignore_index=True)

    # Overlap analysis
    both = (combined["sig_cts"] & combined["sig_psz_level"]).sum()
    cts_only = (combined["sig_cts"] & ~combined["sig_psz_level"]).sum()
    psz_only = combined["sig_psz_only"].sum()
    total_cts = combined["sig_cts"].sum()
    total_psz = combined["sig_psz_level"].sum()

    print(f"\n{'='*60}")
    print(f"  OVERLAP ANALYSIS")
    print(f"{'='*60}")
    print(f"  CTS -1 signals:     {total_cts}")
    print(f"  PSZ level signals:  {total_psz}")
    print(f"  Both:               {both}")
    print(f"  CTS-only:           {cts_only}")
    print(f"  PSZ-only:           {psz_only}")
    print(f"  Overlap rate:       {both / max(1, total_cts) * 100:.1f}% of CTS signals also PSZ")

    analyze_signal(combined, "sig_cts", "A: CTS -1/+1 (current best)")
    analyze_signal(combined, "sig_psz_cross", "B: PSZ crosses buy_threshold upward (all)")
    analyze_signal(combined, "sig_psz_level", "C: PSZ at buy_threshold level + coh<=0.3 + pdd>-10")
    analyze_signal(combined, "sig_psz_only", "D: PSZ-level-only (complement — PSZ fires, CTS doesn't)")
    analyze_signal(combined, "sig_psz_cross_only", "E: PSZ-cross-only (complement — PSZ cross, CTS doesn't)")


if __name__ == "__main__":
    main()
