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
    
    print(f"[{ticker}] Simulating {window} days of post-market analysis...")
    for i in tqdm(range(start_idx, len(df_full) + 1), desc=f"WF {ticker}"):
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
            'psz_wf': last_row['price_slope_z'],
            'psz_smooth_wf': last_row.get('psz_smooth', np.nan),
            'psz_v_wf': last_row.get('psz_v', np.nan),
            'psz_buy_thresh_wf': last_row['psz_buy_threshold'],
            'psz_sell_thresh_wf': last_row['psz_sell_threshold']
        })
        
    if not walkforward_results:
        print(f"No walk-forward results for {ticker}")
        return

    df_wf = pd.DataFrame(walkforward_results)
    df_wf.set_index('date', inplace=True)
    
    # 4. Compare and Plot
    df_plot = df_omni.set_index('date').tail(window).copy()
    df_plot = df_plot.join(df_wf[['cts_wf', 'cts_slope_wf', 'threshold_wf', 'smoothed_wf',
                                   'psz_wf', 'psz_smooth_wf', 'psz_v_wf', 
                                   'psz_buy_thresh_wf', 'psz_sell_thresh_wf']], how='left')
    
    fig, (ax1, ax2, ax3, ax4) = plt.subplots(4, 1, figsize=(15, 18), sharex=True, 
                                            gridspec_kw={'height_ratios': [2, 1, 1, 1]})
    
    # Top Panel: Price & Smoothing
    ax1.plot(df_plot.index, df_plot['close'], color='black', alpha=0.3, label='Price')
    ax1.plot(df_plot.index, df_plot['smoothed_cwvap'], color='blue', alpha=0.7, label='Omni Smoothed')
    ax1.plot(df_plot.index, df_plot['smoothed_wf'], color='red', linestyle='--', alpha=0.9, label='WF Smoothed')
    
    # --- OLD SIGNAL LOGIC (Commented out as requested) ---
    # s_buy = (df_plot['cts_wf'] > 0) & (df_plot['cts_wf'].shift(1) <= 0) & (df_plot['cts_slope_wf'] > df_plot['threshold_wf'])
    # s_sell = (df_plot['cts_wf'] < 0) & (df_plot['cts_wf'].shift(1) >= 0) & (df_plot['cts_slope_wf'] < -df_plot['threshold_wf'])
    # psz_buy_cond = (df_plot['psz_wf'] >= df_plot['psz_buy_thresh_wf']) & (df_plot['psz_wf'] <= 0)
    # psz_sell_cond = (df_plot['psz_wf'] >= df_plot['psz_sell_thresh_wf'])
    # psz_buy = s_buy & psz_buy_cond
    # psz_sell = s_sell & psz_sell_cond
    # ax1.scatter(df_plot[s_buy].index, df_plot[s_buy]['close'], marker='^', color='gray', s=50, label='Savgol Only Buy', alpha=0.3)
    # ax1.scatter(df_plot[s_sell].index, df_plot[s_sell]['close'], marker='v', color='gray', s=50, label='Savgol Only Sell', alpha=0.3)
    
    # --- NEW THRESHOLD DIRECTIONAL LOGIC ---
    # 1. Percentile Thresholds
    top_cts_thresh = df_plot['cts_wf'].quantile(0.90)
    bot_cts_thresh = df_plot['cts_wf'].quantile(0.10)
    
    # Use series for thresholds to compare per-bar
    top_psz_thresh = df_plot['psz_sell_thresh_wf']
    bot_psz_thresh = df_plot['psz_buy_thresh_wf']
    # 2. Refined Directional Signals
    # CTS logic: Trigger when moving away from clips (-1 or 1)
    # Use a small tolerance for the clip check
    cts_series = df_plot['cts_wf']
    psz_series = df_plot['psz_smooth_wf']
    
    v_threshold = 0.025
    v_lead_threshold = 0.040

    # Combined Buy: both CTS and PSZ below or touching their bottom thresholds
    cts_buy = (cts_series <= bot_cts_thresh) & (psz_series <= bot_psz_thresh)
    # Combined Sell: both CTS and PSZ above or touching their top thresholds
    cts_sell = (cts_series >= top_cts_thresh) & (psz_series >= top_psz_thresh)
    
    # PSZ-Only Leading Signals (Neutral CTS)
    psz_lead_buy = (psz_series <= bot_psz_thresh) & (cts_series > bot_cts_thresh) & (df_plot['psz_v_wf'] > v_lead_threshold)
    psz_lead_sell = (psz_series >= top_psz_thresh) & (cts_series < top_cts_thresh) & (df_plot['psz_v_wf'] < -v_lead_threshold)

    # Signals to be marked on price chart (Consensus signals must meet v_threshold)
    cts_buy_marks = cts_buy & (df_plot['psz_v_wf'].abs() > v_threshold)
    cts_sell_marks = cts_sell & (df_plot['psz_v_wf'].abs() > v_threshold)

    # PSZ logic commented out as requested
    # Reference to original logic:
    # psz_buy = (psz_series <= bot_psz_thresh) & (psz_series > psz_prev) & (v_series > v_strength)
    # psz_sell = (psz_series >= top_psz_thresh) & (psz_series < psz_prev) & (v_series < -v_strength)
    psz_buy = pd.Series(False, index=df_plot.index)
    psz_sell = pd.Series(False, index=df_plot.index)

    # Plot Signals
    print(f"[{ticker}] Signals Found - CTS Buys: {cts_buy.sum()}, PSZ Buys: {psz_buy.sum()}, CTS Sells: {cts_sell.sum()}, PSZ Sells: {psz_sell.sum()}")
    
    # CTS Buy (Green ^) - Only if velocity is strong
    ax1.scatter(df_plot[cts_buy_marks].index, df_plot[cts_buy_marks]['close'], marker='^', color='green', s=150, alpha=0.7, label='Consensus Buy', zorder=5)
    # PSZ Lead Buy (Cyan ^) - No CTS check, but higher velocity filter
    ax1.scatter(df_plot[psz_lead_buy].index, df_plot[psz_lead_buy]['close'], marker='^', color='cyan', s=100, alpha=0.8, label='PSZ Lead Buy', zorder=5)

    # CTS Sell (Red v) - Only if velocity is strong
    ax1.scatter(df_plot[cts_sell_marks].index, df_plot[cts_sell_marks]['close'], marker='v', color='red', s=150, alpha=0.7, label='Consensus Sell', zorder=5)
    # PSZ Lead Sell (Magenta v) - No CTS check, but higher velocity filter
    ax1.scatter(df_plot[psz_lead_sell].index, df_plot[psz_lead_sell]['close'], marker='v', color='magenta', s=100, alpha=0.8, label='PSZ Lead Sell', zorder=5)
    
    ax1.set_title(f"Savgol + PSZ Directional Signal Analysis - {ticker}")
    ax1.legend()
    
    # Panel 2: CTS with Thresholds
    ax2.plot(df_plot.index, df_plot['cts_wf'], color='red', label='WF CTS')
    ax2.axhline(top_cts_thresh, color='red', linestyle='--', alpha=0.5, label=f'P90 ({top_cts_thresh:.2f})')
    ax2.axhline(bot_cts_thresh, color='green', linestyle='--', alpha=0.5, label=f'P10 ({bot_cts_thresh:.2f})')
    ax2.axhline(0, color='black', alpha=0.2)
    ax2.set_title("Walk-Forward CTS with Directional Threshold Guards")
    ax2.legend()
    
    # Panel 3: PSZ with Adaptive Thresholds
    ax3.plot(df_plot.index, df_plot['psz_wf'], color='purple', alpha=0.3, label='WF PSZ (Raw)')
    ax3.plot(df_plot.index, df_plot['psz_smooth_wf'], color='purple', linewidth=2, label='WF PSZ (SG-Smooth)')
    ax3.plot(df_plot.index, df_plot['psz_buy_thresh_wf'], color='green', linestyle='--', alpha=0.7, label=f'Adaptive Buy Floor (P20)')
    ax3.plot(df_plot.index, df_plot['psz_sell_thresh_wf'], color='red', linestyle='--', alpha=0.7, label=f'Adaptive Sell Floor (P80)')
    ax3.axhline(0, color='black', alpha=0.2)
    ax3.fill_between(df_plot.index, df_plot['psz_buy_thresh_wf'], 0,
                     alpha=0.08, color='green', label='Buy Zone')
    ax3.set_title("PSZ with Adaptive Thresholds")
    ax3.legend()

    # Panel 4: Velocity of Momentum (psz_v)
    ax4.plot(df_plot.index, df_plot['psz_v_wf'], color='blue', alpha=0.6, label='Velocity of Momentum (psz_v)')
    # Add dots for signals on Velocity chart
    ax4.scatter(df_plot[cts_buy].index, df_plot[cts_buy]['psz_v_wf'], color='green', s=100, label='Consensus Buy', zorder=5)
    ax4.scatter(df_plot[psz_lead_buy].index, df_plot[psz_lead_buy]['psz_v_wf'], color='cyan', s=60, label='PSZ Lead Buy', zorder=5)
    ax4.scatter(df_plot[cts_sell].index, df_plot[cts_sell]['psz_v_wf'], color='red', s=100, label='Consensus Sell', zorder=5)
    ax4.scatter(df_plot[psz_lead_sell].index, df_plot[psz_lead_sell]['psz_v_wf'], color='magenta', s=60, label='PSZ Lead Sell', zorder=5)
    ax4.axhline(0, color='black', alpha=0.2)
    ax4.axhline(v_threshold, color='black', linestyle=':', alpha=0.3, label=f'Ref {v_threshold}')
    ax4.axhline(-v_threshold, color='black', linestyle=':', alpha=0.3, label=f'Ref {-v_threshold}')
    ax4.set_title("Velocity of Momentum (Normalized Acceleration)")
    ax4.legend()
    
    plt.tight_layout()
    plot_path = f"{ticker}_{window}_savgol_psz_analysis.png"
    plt.savefig(plot_path)
    plt.close(fig) # Close to free memory during loops
    print(f"[{ticker}] PSZ analysis plot saved to {plot_path}")

    # Debug Combined Signals
    print(f"\n--- Debugging Combined Signals for {ticker} ---")
    print(f"CTS P10 (Bot Thresh): {bot_cts_thresh:.4f}")
    print(f"CTS P90 (Top Thresh): {top_cts_thresh:.4f}")
    
    potential_buys = df_plot[cts_buy | psz_lead_buy].tail(10)
    if not potential_buys.empty:
        print("\nRecent Buy signals (Consensus or Lead):")
        for idx, row in potential_buys.iterrows():
            is_lead = psz_lead_buy.loc[idx]
            tag = "[LEAD]" if is_lead else "[CONS]"
            marked = cts_buy_marks.loc[idx] or is_lead
            marker_str = " (Price-Marked)" if marked else " (Velocity-Filtered)"
            print(f"{idx} | {tag} | CTS: {row['cts_wf']:>7.4f} | PSZ: {row['psz_smooth_wf']:>7.4f} | V: {row['psz_v_wf']:>7.4f}{marker_str}")
    else:
        print("No Buy signals found.")
        
    potential_sells = df_plot[cts_sell | psz_lead_sell].tail(10)
    if not potential_sells.empty:
        print("\nRecent Sell signals (Consensus or Lead):")
        for idx, row in potential_sells.iterrows():
            is_lead = psz_lead_sell.loc[idx]
            tag = "[LEAD]" if is_lead else "[CONS]"
            marked = cts_sell_marks.loc[idx] or is_lead
            marker_str = " (Price-Marked)" if marked else " (Velocity-Filtered)"
            print(f"{idx} | {tag} | CTS: {row['cts_wf']:>7.4f} | PSZ: {row['psz_smooth_wf']:>7.4f} | V: {row['psz_v_wf']:>7.4f}{marker_str}")
    else:
        print("No Sell signals found.")

def get_watchlist_symbols(watchlist_name):
    """Retrieve symbols from a named watchlist in the database."""
    from src.database import get_db_connection
    conn = get_db_connection()
    query = """
        SELECT symbol FROM watchlist_items 
        JOIN watchlists ON watchlists.id = watchlist_items.watchlist_id 
        WHERE watchlists.name = ? 
        ORDER BY watchlist_items.display_order;
    """
    try:
        df_items = pd.read_sql_query(query, conn, params=[watchlist_name])
        return df_items['symbol'].tolist()
    finally:
        conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default=None)
    parser.add_argument("--watchlist", default=None)
    parser.add_argument("--window", type=int, default=150)
    args = parser.parse_args()
    
    if args.ticker:
        run_walkforward_test(args.ticker, args.window)
    elif args.watchlist:
        symbols = get_watchlist_symbols(args.watchlist)
        if not symbols:
            print(f"No symbols found in watchlist: {args.watchlist}")
        else:
            print(f"Looping through {len(symbols)} symbols in watchlist '{args.watchlist}'...")
            for symbol in symbols:
                try:
                    run_walkforward_test(symbol, args.window)
                except Exception as e:
                    print(f"Error processing {symbol}: {e}")
    else:
        # Default to RELIANCE if nothing provided
        run_walkforward_test("RELIANCE", args.window)
