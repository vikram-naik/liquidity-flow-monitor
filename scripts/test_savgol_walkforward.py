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
    cwvap_calc = CompositeVWAP(cts_strategy='causal_savgol')
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
    
    # --- NEW ADAPTIVE SIGNAL LOGIC ---
    # 1. PSZ Extrema using Scipy
    from scipy.signal import argrelextrema
    psz_vals = df_plot['psz_v_wf'].values
    trough_indices = argrelextrema(psz_vals, np.less)[0]
    peak_indices = argrelextrema(psz_vals, np.greater)[0]
    
    df_plot['psz_trough'] = False
    df_plot.iloc[trough_indices, df_plot.columns.get_loc('psz_trough')] = True
    df_plot['psz_peak'] = False
    df_plot.iloc[peak_indices, df_plot.columns.get_loc('psz_peak')] = True
    
    # 2. Filter Extrema (ensure they are not near zero / sideways)
    v_top_thresh = df_plot['psz_v_wf'].quantile(0.80)
    v_bot_thresh = df_plot['psz_v_wf'].quantile(0.20)
    
    # Only keep troughs that are in the bottom 20% of V, and peaks in top 20%
    df_plot['psz_trough'] = df_plot['psz_trough'] & (df_plot['psz_v_wf'] <= v_bot_thresh)
    df_plot['psz_peak'] = df_plot['psz_peak'] & (df_plot['psz_v_wf'] >= v_top_thresh)

    # 3. Adaptive CTS Thresholds (10th / 90th Percentiles)
    top_cts_thresh = df_plot['cts_wf'].quantile(0.90)
    bot_cts_thresh = df_plot['cts_wf'].quantile(0.10)
    
    buy_sig = (df_plot['cts_wf'] <= bot_cts_thresh) & (df_plot['psz_trough'])
    sell_sig = (df_plot['cts_wf'] >= top_cts_thresh) & (df_plot['psz_peak'])
    
    # Plot Precise Signals
    ax1.scatter(df_plot[buy_sig].index, df_plot[buy_sig]['close'], marker='^', color='green', s=200, label=f'ADAPTIVE BUY (CTS <= P10 + PSZ_V Trough)', zorder=5)
    ax1.scatter(df_plot[sell_sig].index, df_plot[sell_sig]['close'], marker='v', color='red', s=200, label=f'ADAPTIVE SELL (CTS >= P90 + PSZ_V Peak)', zorder=5)
    
    ax1.set_title(f"Savgol + PSZ Adaptive Analysis (Velocity Extrema) - {ticker}")
    ax1.legend()
    
    # Panel 2: CTS with Thresholds
    ax2.plot(df_plot.index, df_plot['cts_wf'], color='red', label='WF CTS')
    ax2.axhline(top_cts_thresh, color='red', linestyle='--', alpha=0.5, label=f'P90 ({top_cts_thresh:.2f})')
    ax2.axhline(bot_cts_thresh, color='green', linestyle='--', alpha=0.5, label=f'P10 ({bot_cts_thresh:.2f})')
    ax2.axhline(0, color='black', alpha=0.2)
    ax2.set_title("Walk-Forward CTS with Adaptive Thresholds")
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
    ax4.plot(df_plot.index, df_plot['psz_v_wf'], color='blue', label='Velocity of Momentum (psz_v)')
    ax4.axhline(0, color='black', alpha=0.2)
    ax4.set_title("Velocity of Momentum (Normalized Acceleration)")
    ax4.legend()
    
    plt.tight_layout()
    plot_path = f"{ticker}_{window}_savgol_psz_analysis.png"
    plt.savefig(plot_path)
    plt.close(fig) # Close to free memory during loops
    print(f"[{ticker}] PSZ analysis plot saved to {plot_path}")

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
