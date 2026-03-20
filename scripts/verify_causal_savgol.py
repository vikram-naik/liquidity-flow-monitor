#!/usr/bin/env python3
import argparse
import sys
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import savgol_filter

# Adding project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.divergence_engine.utils import load_symbol_data
from src.divergence_engine.base_calc import BaseCalculator
from src.divergence_engine.dvl_ledger import DVLLedger
from src.divergence_engine.cwvap import CompositeVWAP

def main():
    parser = argparse.ArgumentParser(description="Verify Causal Savitzky-Golay Implementation")
    parser.add_argument("--ticker", type=str, required=True, help="NSE Symbol (e.g. RELIANCE)")
    parser.add_argument("--days", type=int, default=150, help="Number of recent days to plot")
    args = parser.parse_args()

    print(f"Loading data for {args.ticker}...")
    df = load_symbol_data(args.ticker, None, None)
    if df is None or df.empty:
        print(f"No data found for {args.ticker}")
        return

    # Prerequisites
    base = BaseCalculator()
    df = base.compute_all(df)
    dvl = DVLLedger()
    df = dvl.compute_all(df)

    print("Running CTS strategies...")
    
    from src.divergence_engine.cts import CTSFactory
    
    # Configure strategies
    # Use same window and polyorder for fair comparison
    params = {"window_length": 15, "polyorder": 2}
    
    strat_centered = CTSFactory.get_strategy("savgol", **params)
    strat_causal = CTSFactory.get_strategy("causal_savgol", **params)
    
    cwvap_centered = CompositeVWAP(cts_strategy="savgol")
    cwvap_centered.cts_strategy = strat_centered
    
    cwvap_causal = CompositeVWAP(cts_strategy="causal_savgol")
    cwvap_causal.cts_strategy = strat_causal
    
    df_centered = cwvap_centered.compute_all(df.copy())
    df_causal = cwvap_causal.compute_all(df.copy())
    
    # 1. Verification of Causality
    # Pick a random point i, compute its value.
    # Then take data up to i, compute centered savgol, and check the LAST point.
    # They should match if our causal implementation is correct.
    
    cwvap_vals = df_causal["cwvap"].values.astype(float)
    idx = len(df) - 10 # Check a point near the end
    
    # Causal Implementation Result at idx
    causal_val = df_causal["smoothed_cwvap"].iloc[idx]
    
    # Centered Implementation (scipy) evaluated at the right edge of a slice up to idx
    subset = cwvap_vals[:idx+1]
    scipy_causal_eval = savgol_filter(subset, params["window_length"], params["polyorder"])[-1]
    
    print(f"\nCausality Check at index {idx}:")
    print(f"  CausalSavgolStrategy result: {causal_val:.6f}")
    print(f"  scipy.savgol_filter(subset)[-1]: {scipy_causal_eval:.6f}")
    diff = abs(causal_val - scipy_causal_eval)
    print(f"  Difference: {diff:.2e}")
    
    if diff < 1e-10:
        print("  [SUCCESS] Causal implementation matches one-sided scipy evaluation.")
    else:
        print("  [WARNING] Causal implementation differs from one-sided scipy evaluation.")

    # 2. Plotting Comparison
    plot_df = pd.DataFrame(index=pd.to_datetime(df["date"]))
    plot_df["close"] = df["close"].values
    plot_df["cwvap"] = df_causal["cwvap"].values
    plot_df["centered_smooth"] = df_centered["smoothed_cwvap"].values
    plot_df["causal_smooth"] = df_causal["smoothed_cwvap"].values
    plot_df["centered_cts"] = df_centered["cts"].values
    plot_df["causal_cts"] = df_causal["cts"].values
    plot_df["centered_slope"] = df_centered["cts_slope"].values
    plot_df["causal_slope"] = df_causal["cts_slope"].values

    plot_df = plot_df.tail(args.days)
    
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 12), sharex=True)
    fig.suptitle(f"Savgol vs Causal Savgol Comparison: {args.ticker}", fontsize=16)
    
    # Price panel
    ax1.plot(plot_df.index, plot_df["cwvap"], label="Raw CWVAP", color="blue", alpha=0.3)
    ax1.plot(plot_df.index, plot_df["centered_smooth"], label="Centered Savgol (Lookahead)", color="purple")
    ax1.plot(plot_df.index, plot_df["causal_smooth"], label="Causal Savgol (Lagging)", color="green", linewidth=2)
    ax1.set_ylabel("Price / CWVAP")
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    
    # CTS panel
    ax2.plot(plot_df.index, plot_df["centered_cts"], label="Centered CTS", color="purple", alpha=0.5)
    ax2.plot(plot_df.index, plot_df["causal_cts"], label="Causal CTS", color="green")
    ax2.axhline(0, color='black', alpha=0.3)
    ax2.set_ylabel("CTS Score")
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    
    # Slope panel
    ax3.plot(plot_df.index, plot_df["centered_slope"], label="Centered Slope", color="purple", alpha=0.5)
    ax3.plot(plot_df.index, plot_df["causal_slope"], label="Causal Slope", color="green")
    ax3.axhline(0, color='black', alpha=0.3)
    ax3.set_ylabel("CTS Slope")
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    
    plt.tight_layout()
    output_path = "causal_savgol_verification.png"
    plt.savefig(output_path)
    print(f"\nPlot saved to {output_path}")

if __name__ == "__main__":
    main()
