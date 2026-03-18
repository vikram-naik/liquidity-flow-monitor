import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import argparse
import os
import sys

# Add project root to sys.path to resolve 'src' imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tqdm import tqdm

from src.divergence_engine.engine import DivergenceEngine
from src.divergence_engine.cwvap import CompositeVWAP
from src.divergence_engine.utils import load_symbol_data

from src.divergence_engine.engine import DivergenceEngine
from src.divergence_engine.cwvap import CompositeVWAP
from src.divergence_engine.utils import load_symbol_data
from src.divergence_engine.base_calc import BaseCalculator
from src.divergence_engine.dvl_ledger import DVLLedger
from src.divergence_engine.cwc import CrossWindowCoherence
from src.divergence_engine.mcs import MoneyCompositeScore
from src.divergence_engine.analysis import compute_trend_participation

def run_walkforward_test(ticker, window=200):
    print(f"--- Running Savgol + PSZ Walk-Forward Test for {ticker} ---")
    
    # 1. Load full history
    df_full = load_symbol_data(ticker)
    
    # Define calculators
    base_calc = BaseCalculator()
    dvl_ledger = DVLLedger()
    cwvap_calc = CompositeVWAP(cts_strategy='savgol')
    cwc_calc = CrossWindowCoherence()
    mcs_calc = MoneyCompositeScore()
    
    # 2. Get 'Omniscient' Data (Full history at once)
    df_omni = df_full.copy()
    df_omni = base_calc.compute_all(df_omni)
    df_omni = dvl_ledger.compute_all(df_omni)
    df_omni = cwvap_calc.compute_all(df_omni)
    df_omni = cwc_calc.compute_all(df_omni)
    df_omni = mcs_calc.compute_all(df_omni)
    df_omni = compute_trend_participation(df_omni)
    
    # 3. Perform Walk-Forward Simulation
    start_idx = len(df_full) - window
    walkforward_results = []
    
    print(f"Simulating {window} days of post-market analysis...")
    for i in tqdm(range(start_idx, len(df_full) + 1)):
        # Data available 'at the end of day i'
        current_slice = df_full.iloc[:i].copy()
        if len(current_slice) < 120: # Need at least 120 for windows
            continue
            
        # Run full pipeline for THIS slice
        df_s = base_calc.compute_all(current_slice)
        df_s = dvl_ledger.compute_all(df_s)
        df_s = cwvap_calc.compute_all(df_s)
        df_s = cwc_calc.compute_all(df_s)
        df_s = mcs_calc.compute_all(df_s)
        df_s = compute_trend_participation(df_s)
        
        # Record the LAST bar's state
        last_row = df_s.iloc[-1]
        walkforward_results.append({
            'date': last_row['date'],
            'close': last_row['close'],
            'cts_wf': last_row['cts'],
            'cts_slope_wf': last_row['cts_slope'],
            'threshold_wf': last_row['cts_slope_threshold'],
            'smoothed_wf': last_row['smoothed_cwvap'],
            'psz_wf': last_row['price_slope_z']
        })
        
    df_wf = pd.DataFrame(walkforward_results)
    df_wf.set_index('date', inplace=True)
    
    # 4. Compare and Plot
    df_plot = df_omni.set_index('date').tail(window).copy()
    df_plot = df_plot.join(df_wf[['cts_wf', 'cts_slope_wf', 'threshold_wf', 'smoothed_wf', 'psz_wf']], how='left')
    
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(15, 15), sharex=True, gridspec_kw={'height_ratios': [2, 1, 1]})
    
    # Top Panel: Price & Smoothing
    ax1.plot(df_plot.index, df_plot['close'], color='black', alpha=0.3, label='Price')
    ax1.plot(df_plot.index, df_plot['smoothed_cwvap'], color='blue', alpha=0.7, label='Omni Smoothed')
    ax1.plot(df_plot.index, df_plot['smoothed_wf'], color='red', linestyle='--', alpha=0.9, label='WF Smoothed')
    
    # Original Savgol Signals (WF)
    s_buy = (df_plot['cts_wf'] > 0) & (df_plot['cts_wf'].shift(1) <= 0) & (df_plot['cts_slope_wf'] > df_plot['threshold_wf'])
    s_sell = (df_plot['cts_wf'] < 0) & (df_plot['cts_wf'].shift(1) >= 0) & (df_plot['cts_slope_wf'] < -df_plot['threshold_wf'])
    
    # Savgol + PSZ Signals
    # Buy: psz between -0.3 and 0
    # Sell: psz >= 0.2
    psz_buy_cond = (df_plot['psz_wf'] >= -0.3) & (df_plot['psz_wf'] <= 0)
    psz_sell_cond = (df_plot['psz_wf'] >= 0.2)
    
    psz_buy = s_buy & psz_buy_cond
    psz_sell = s_sell & psz_sell_cond
    
    # Plot original signals with light markers
    ax1.scatter(df_plot[s_buy].index, df_plot[s_buy]['close'], marker='^', color='gray', s=50, label='Savgol Only Buy', alpha=0.3)
    ax1.scatter(df_plot[s_sell].index, df_plot[s_sell]['close'], marker='v', color='gray', s=50, label='Savgol Only Sell', alpha=0.3)
    
    # Plot PSZ-Confirmed Signals
    ax1.scatter(df_plot[psz_buy].index, df_plot[psz_buy]['close'], marker='^', color='green', s=150, label='Savgol + PSZ Buy', zorder=5)
    ax1.scatter(df_plot[psz_sell].index, df_plot[psz_sell]['close'], marker='v', color='red', s=150, label='Savgol + PSZ Sell', zorder=5)
    
    ax1.set_title(f"Savgol + PSZ Walk-Forward Analysis - {ticker}")
    ax1.legend()
    
    # Panel 2: CTS
    ax2.plot(df_plot.index, df_plot['cts_wf'], color='red', label='WF CTS')
    ax2.axhline(0, color='black', alpha=0.2)
    ax2.set_title("Walk-Forward CTS")
    ax2.legend()
    
    # Panel 3: PSX
    ax3.plot(df_plot.index, df_plot['psz_wf'], color='purple', label='WF Price Slope Z (PSZ)')
    ax3.axhline(0, color='black', alpha=0.2)
    ax3.axhline(-0.3, color='green', linestyle='--', alpha=0.5, label='Buy Zone Floor (-0.3)')
    ax3.axhline(0.2, color='red', linestyle='--', alpha=0.5, label='Sell Zone Floor (0.2)')
    ax3.set_title("Price Slope Z (PSZ) Confirmation Zones")
    ax3.legend()
    
    plt.tight_layout()
    plot_path = "savgol_psz_analysis.png"
    plt.savefig(plot_path)
    print(f"PSZ analysis plot saved to {plot_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="RELIANCE")
    parser.add_argument("--window", type=int, default=150)
    args = parser.parse_args()
    
    run_walkforward_test(args.ticker, args.window)
