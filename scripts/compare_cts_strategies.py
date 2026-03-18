#!/usr/bin/env python3
"""
scripts/compare_cts_strategies.py

Runs the DivergenceEngine with real data and compares the responsiveness
and noise levels of the default_ema, dema, and kama CTS strategies side-by-side.
Outputs a summary to the console and displays a matplotlib chart of the results.

Usage:
    python scripts/compare_cts_strategies.py --ticker RELIANCE
"""

import argparse
import sys
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from typing import Dict, Tuple

# Adding project root to path so it can find 'src' regardless of where it is run from.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.divergence_engine.engine import DivergenceEngine
from src.divergence_engine.cwvap import CompositeVWAP

def compute_strategy_metrics(df: pd.DataFrame, strategy: str) -> Tuple[pd.Series, pd.Series, dict]:
    """Run a single CTS strategy and return its metrics."""
    cwvap_calc = CompositeVWAP(cts_strategy=strategy)
    # create a clean copy for this run
    run_df = df.copy()
    try:
        run_df = cwvap_calc.compute_all(run_df)
    except Exception as e:
        print(f"Failed to run {strategy}: {e}")
        return pd.Series(dtype=float), pd.Series(dtype=float), {}

    cts = run_df["cts"]
    cts_slope = run_df["cts_slope"]

    # Compute comparative metrics
    # Noise = sum of absolute changes in slope (jaggedness) over last N bars
    # Responsiveness = absolute max slope during trends (higher = faster to react)
    valid_slope = cts_slope.dropna()
    noise = np.sum(np.abs(np.diff(valid_slope))) if len(valid_slope) > 1 else 0
    responsiveness = np.max(np.abs(valid_slope)) if len(valid_slope) > 0 else 0

    metrics = {
        "noise": noise,
        "responsiveness": responsiveness,
        "mean_slope_abs": np.mean(np.abs(valid_slope)) if len(valid_slope) > 0 else 0,
    }
    return cts, cts_slope, metrics

def main():
    parser = argparse.ArgumentParser(description="Compare CTS Strategies")
    parser.add_argument("--ticker", type=str, required=True, help="NSE Symbol to analyze (e.g. RELIANCE)")
    parser.add_argument("--days", type=int, default=250, help="Number of recent trading days to plot")
    args = parser.parse_args()

    print(f"Loading data for {args.ticker} using DivergenceEngine...")
    engine = DivergenceEngine(ticker=args.ticker)
    
    # Normally engine.run() executes everything. 
    # To do a fair comparison with isolated strategies from a single baseline,
    # we simulate the Engine's pipeline up to module 2 (DVL Ledger).
    
    try:
        from src.divergence_engine.utils import load_symbol_data
        from src.divergence_engine.base_calc import BaseCalculator
        from src.divergence_engine.dvl_ledger import DVLLedger
    except ImportError as e:
        print(f"ImportError: {e}. Are you running from project root with PYTHONPATH=src?")
        sys.exit(1)

    df_base = load_symbol_data(args.ticker, None, None)
    if df_base is None or df_base.empty:
        print(f"No data found for {args.ticker}")
        sys.exit(1)

    # Calculate prerequisites
    base = BaseCalculator()
    df_base = base.compute_all(df_base)
    dvl = DVLLedger()
    df_base = dvl.compute_all(df_base)

    print("Pre-processing complete. Running strategy comparisons...")
    
    strategies = ["default_ema", "dema", "kama", "savgol"]
    results_cts = {}
    results_slope = {}
    stats = {}

    for strat in strategies:
        print(f"  -> Processing {strat}...")
        cts, slope, metrics = compute_strategy_metrics(df_base, strat)
        results_cts[strat] = cts
        results_slope[strat] = slope
        stats[strat] = metrics

    # --- Print Summary ---
    print("\n" + "="*50)
    print("CTS STRATEGY COMPARISON SUMMARY")
    print("="*50)
    print(f"Ticker: {args.ticker} | Data points: {len(df_base)}")
    print("-"*50)
    print(f"{'Strategy':<15} | {'Responsiveness':<15} | {'Noise (Jaggedness)':<15}")
    print("-"*50)
    
    for strat in strategies:
        m = stats[strat]
        print(f"{strat:<15} | {m.get('responsiveness', 0):<15.6f} | {m.get('noise', 0):<15.6f}")
    
    print("="*50)
    print("Note: ")
    print(" - Responsiveness: Max absolute slope. Higher means it reacts faster to turns.")
    print(" - Noise: Sum of slope fluctuations. Lower means a smoother, more reliable signal.")
    print("="*50)

    # --- Plotting ---
    # Ensure all results share the same index (date)
    date_index = pd.to_datetime(df_base["date"])
    results_smoothed = {}
    for strat in strategies:
        results_cts[strat].index = date_index
        results_slope[strat].index = date_index
        
        # Capture smoothed_cwvap if it exists (specific to Savgol)
        # We run the calc again or just expect it in the compute_strategy_metrics return?
        # Let's adjust compute_strategy_metrics to return the full df or just the columns we need.
        pass

    plot_df = pd.DataFrame(results_cts)
    plot_df["close"] = df_base["close"].values
    
    # We want to capture the smoothed line and thresholds for Savgol specifically
    cwvap_calc_savgol = CompositeVWAP(cts_strategy="savgol")
    df_savgol = cwvap_calc_savgol.compute_all(df_base.copy())
    plot_df["savgol_smooth"] = df_savgol["smoothed_cwvap"].values
    plot_df["savgol_slope"] = df_savgol["cts_slope"].values
    plot_df["savgol_threshold"] = df_savgol["cts_slope_threshold"].values
    
    plot_df["cwvap"] = CompositeVWAP(cts_strategy="default_ema").compute_all(df_base.copy())["cwvap"].values
    plot_df.index = date_index

    # Trim to last N days for readability
    plot_df = plot_df.tail(args.days)
    if plot_df.empty:
        print("Not enough data to plot.")
        return

    # Create figure with 3 subplots
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 12), sharex=True, gridspec_kw={'height_ratios': [2, 1, 1]})
    fig.suptitle(f"CTS Strategy Comparison: {args.ticker} (Last {args.days} bars)", fontsize=16)

    # Top panel: Price and CWVAP
    ax1.plot(plot_df.index, plot_df["close"], label="Close Price", color="black", alpha=0.4, linewidth=1)
    ax1.plot(plot_df.index, plot_df["cwvap"], label="Raw CWVAP", color="blue", alpha=0.3, linewidth=1)
    if "savgol_smooth" in plot_df.columns:
        ax1.plot(plot_df.index, plot_df["savgol_smooth"], label="Savgol Smooth (deriv=0)", color="purple", linewidth=2, linestyle='--')
    
    # --- Signal Visuals (Filtered Zero Cross for Savgol) ---
    savgol_cts = plot_df["savgol"]
    savgol_slope = plot_df["savgol_slope"]
    savgol_thresh = plot_df["savgol_threshold"]
    
    # Buy signal: cross from negative to positive AND slope > threshold
    buys = plot_df[
        (savgol_cts > 0) & 
        (savgol_cts.shift(1) <= 0) & 
        (savgol_slope > savgol_thresh)
    ]
    
    # Sell signal: cross from positive to negative AND slope < -threshold
    sells = plot_df[
        (savgol_cts < 0) & 
        (savgol_cts.shift(1) >= 0) & 
        (savgol_slope < -savgol_thresh)
    ]
    
    # Plot signals on Price Chart (ax1)
    ax1.scatter(buys.index, buys["close"], marker="^", color="lime", s=100, label="Savgol Buy (Filtered)", zorder=5)
    ax1.scatter(sells.index, sells["close"], marker="v", color="maroon", s=100, label="Savgol Sell (Filtered)", zorder=5)

    ax1.set_title("Price vs CWVAP & Savgol Smoothing")
    ax1.set_ylabel("Price")
    ax1.legend(loc='upper left', fontsize='small') 
    ax1.grid(True, alpha=0.3)

    # Middle panel: CTS scores overlay
    colors = {"default_ema": "gray", "dema": "red", "kama": "green", "savgol": "purple"}
    for strat in strategies:
        ax2.plot(plot_df.index, plot_df[strat], label=f"CTS ({strat})", color=colors[strat], linewidth=1.5)
    
    ax2.axhline(0, color='black', linewidth=1, linestyle='--')
    ax2.set_title("Trend Score (CTS) Extent")
    ax2.set_ylabel("CTS Normalized")
    ax2.legend(loc='upper left', fontsize='small', ncol=2)
    ax2.grid(True, alpha=0.3)

    # Bottom panel: CTS Slopes
    for strat in strategies:
        slope_series = results_slope[strat].loc[plot_df.index]
        ax3.plot(plot_df.index, slope_series, label=f"Slope ({strat})", color=colors[strat], linewidth=1.2, alpha=0.8)
    
    # Plot empirical threshold for Savgol
    savgol_thresh_val = df_savgol["cts_slope_threshold"].iloc[-1]
    ax3.axhline(savgol_thresh_val, color="purple", linestyle=":", alpha=0.7, label=f"Savgol Threshold ({savgol_thresh_val:.4f})")
    ax3.axhline(-savgol_thresh_val, color="purple", linestyle=":", alpha=0.7)

    ax3.axhline(0, color='black', linewidth=1, linestyle='--')
    ax3.set_title("Trend Score Slope (Momentum / Responsiveness)")
    ax3.set_ylabel("Slope / Accel")
    ax3.legend(loc='upper left', fontsize='small', ncol=2)
    ax3.grid(True, alpha=0.3)

    plt.tight_layout()
    output_path = "cts_comparison.png"
    plt.savefig(output_path)
    print(f"\nPlot saved to {output_path}")

if __name__ == "__main__":
    main()
