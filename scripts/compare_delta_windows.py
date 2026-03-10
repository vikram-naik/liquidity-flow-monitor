#!/usr/bin/env python3
"""
Compare signal quality across different delta window configurations.
Runs the engine on a set of symbols with different delta windows and
compares hit rates, returns, and signal counts.

Usage:
    python scripts/compare_delta_windows.py
"""

import os
import sys
import time
import sqlite3
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.database import DB_PATH
from src.divergence_engine.base_calc import BaseCalculator
from src.divergence_engine.dvl_ledger import DVLLedger
from src.divergence_engine.cwvap import CompositeVWAP
from src.divergence_engine.cwc import CrossWindowCoherence
from src.divergence_engine.mcs import MoneyCompositeScore
from src.divergence_engine.analysis import compute_trend_participation
from src.divergence_engine.analysis_integrated import apply_integrated_matrix
from src.divergence_engine.regime import classify_market_regime
from src.divergence_engine.utils import load_symbol_data, validate_dataframe
from src.divergence_engine.aggregator import resample_ohlc_delivery

HORIZONS = [3, 5, 10]


def get_nifty50_symbols():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT wi.symbol FROM watchlist_items wi
        JOIN watchlists w ON w.id = wi.watchlist_id
        WHERE w.name = 'NIFTY 50' ORDER BY wi.symbol
    """).fetchall()
    conn.close()
    return [r[0] for r in rows]


def run_engine_with_windows(symbol, psz_w, rsz_w, mcs_w):
    """Run the full pipeline with custom delta windows. Returns ledger DataFrame."""
    df = load_symbol_data(symbol)
    df = resample_ohlc_delivery(df, "daily").reset_index(drop=True)

    df = BaseCalculator().compute_all(df)
    df["regime"] = classify_market_regime(df)
    df = DVLLedger().compute_all(df)
    df = CompositeVWAP().compute_all(df)
    df = CrossWindowCoherence().compute_all(df)
    df = MoneyCompositeScore(delta_window=mcs_w).compute_all(df)
    df = compute_trend_participation(df, psz_delta_window=psz_w, rsz_delta_window=rsz_w)
    df = apply_integrated_matrix(df)
    return df


def extract_signals_with_forward(ledger):
    """Extract signals and compute forward returns."""
    mask = ledger["integrated_state"].isin(["Demand", "Supply"])
    closes = ledger["close"].values
    n = len(closes)
    signals = []

    for idx in ledger.index[mask]:
        row = ledger.loc[idx]
        entry = float(row["close"])
        is_demand = row["integrated_state"] == "Demand"
        sig = {
            "signal_type": row["integrated_state"],
            "signal_strength": float(row["signal_strength"]) if pd.notna(row.get("signal_strength")) else None,
            "price_slope_z": float(row.get("price_slope_z", 0)),
        }

        # Forward returns
        all_valid = True
        for h in HORIZONS:
            end_idx = min(idx + h, n - 1)
            if idx >= n - max(HORIZONS):
                all_valid = False
                break
            fwd_close = closes[end_idx]
            ret = ((fwd_close / entry) - 1) * 100
            if not is_demand:
                ret = -ret
            sig[f"ret_{h}d"] = round(ret, 4)
            sig[f"hit_{h}d"] = 1 if ret > 0 else 0

        if all_valid:
            signals.append(sig)

    return signals


def print_comparison(results):
    """Print comparison table across configurations."""
    print(f"\n{'=' * 90}")
    print(f"  DELTA WINDOW COMPARISON — NIFTY 50")
    print(f"{'=' * 90}")

    header = f"  {'Config':<25s} {'Signals':>8s} {'5d Hit':>8s} {'5d Ret':>8s} {'3d Hit':>8s} {'10d Hit':>8s}"
    print(header)
    print(f"  {'─' * 85}")

    for label, sigs in sorted(results.items()):
        df = pd.DataFrame(sigs)
        if df.empty:
            print(f"  {label:<25s} {'0':>8s}")
            continue

        n = len(df)
        h5 = df["hit_5d"].mean() * 100
        r5 = df["ret_5d"].mean()
        h3 = df["hit_3d"].mean() * 100
        h10 = df["hit_10d"].mean() * 100
        print(f"  {label:<25s} {n:>8,d} {h5:>7.2f}% {r5:>+7.3f}% {h3:>7.2f}% {h10:>7.2f}%")

    # Breakdown by direction
    for direction in ["Demand", "Supply"]:
        print(f"\n  --- {direction} ---")
        print(f"  {'Config':<25s} {'Signals':>8s} {'5d Hit':>8s} {'5d Ret':>8s} {'3d Hit':>8s} {'10d Hit':>8s}")
        print(f"  {'─' * 85}")
        for label, sigs in sorted(results.items()):
            df = pd.DataFrame(sigs)
            df = df[df["signal_type"] == direction]
            if df.empty:
                print(f"  {label:<25s} {'0':>8s}")
                continue
            n = len(df)
            h5 = df["hit_5d"].mean() * 100
            r5 = df["ret_5d"].mean()
            h3 = df["hit_3d"].mean() * 100
            h10 = df["hit_10d"].mean() * 100
            print(f"  {label:<25s} {n:>8,d} {h5:>7.2f}% {r5:>+7.3f}% {h3:>7.2f}% {h10:>7.2f}%")

    # Strength-filtered comparison
    print(f"\n  --- High Strength (>= 50) ---")
    print(f"  {'Config':<25s} {'Signals':>8s} {'5d Hit':>8s} {'5d Ret':>8s}")
    print(f"  {'─' * 55}")
    for label, sigs in sorted(results.items()):
        df = pd.DataFrame(sigs)
        df = df[df["signal_strength"] >= 50]
        if df.empty:
            print(f"  {label:<25s} {'0':>8s}")
            continue
        n = len(df)
        h5 = df["hit_5d"].mean() * 100
        r5 = df["ret_5d"].mean()
        print(f"  {label:<25s} {n:>8,d} {h5:>7.2f}% {r5:>+7.3f}%")


def main():
    symbols = get_nifty50_symbols()
    print(f"Running comparison on {len(symbols)} NIFTY 50 symbols")

    # Configurations to test: (label, psz_window, rsz_window, mcs_window)
    configs = [
        ("PSZ3 RSZ3 MCS5 (curr)", 3, 3, 5),
        ("PSZ3 RSZ3 MCS3",        3, 3, 3),
        ("PSZ5 RSZ5 MCS5",        5, 5, 5),
        ("PSZ5 RSZ3 MCS3",        5, 3, 3),
        ("PSZ3 RSZ5 MCS5",        3, 5, 5),
    ]

    results = defaultdict(list)
    t_start = time.perf_counter()

    for i, sym in enumerate(symbols, 1):
        try:
            # Load base data once, then re-run only the variable parts
            df_base = load_symbol_data(sym)
            df_base = resample_ohlc_delivery(df_base, "daily").reset_index(drop=True)
            df_base = BaseCalculator().compute_all(df_base)
            df_base["regime"] = classify_market_regime(df_base)
            df_base = DVLLedger().compute_all(df_base)
            df_base = CompositeVWAP().compute_all(df_base)
            df_base = CrossWindowCoherence().compute_all(df_base)

            for label, psz_w, rsz_w, mcs_w in configs:
                df = df_base.copy()
                df = MoneyCompositeScore(delta_window=mcs_w).compute_all(df)
                df = compute_trend_participation(df, psz_delta_window=psz_w, rsz_delta_window=rsz_w)
                df = apply_integrated_matrix(df)

                sigs = extract_signals_with_forward(df)
                results[label].extend(sigs)

            elapsed = time.perf_counter() - t_start
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(symbols) - i) / rate if rate > 0 else 0
            n_sigs = len(results[configs[0][0]])
            print(f"\r  [{i}/{len(symbols)}] {sym:<20s} ({rate:.1f}/s, ETA {eta:.0f}s, {n_sigs:,} sigs so far)", end="", flush=True)

        except Exception as e:
            print(f"\r  [{i}/{len(symbols)}] {sym:<20s} ERROR: {e}", flush=True)

    elapsed = time.perf_counter() - t_start
    print(f"\n\n  Done in {elapsed:.1f}s")

    print_comparison(results)


if __name__ == "__main__":
    main()
