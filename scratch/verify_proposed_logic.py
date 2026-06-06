import sys
import os
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag, ExitReason
from scripts.walk_forward import simulate_trades, get_watchlist_symbols, today_str
from scratch.find_missed_setups import simulate_causal_exit

def add_new_features(df):
    """Adds the proposed quant features to the ledger DataFrame."""
    df = df.copy()
    # 1. Lower Wick Ratio
    high_low_spread = df["high"] - df["low"]
    df["lwr"] = np.where(high_low_spread > 0, (df["close"] - df["low"]) / high_low_spread, 0.5)
    
    # 2. Distance-to-ATR Stretch (DAS)
    df["das"] = np.where(df["atr_20"] > 0, (df["close"] - df["cwvap"]) / df["atr_20"], 0.0)
    
    # 3. PDD Momentum (PDDM)
    df["pddm_3b"] = df["pdd_30"] - df["pdd_30"].shift(3)
    
    # 4. Price Deceleration
    df["psz_decel_3b"] = df["price_slope_z"] - df["price_slope_z"].shift(3)
    
    # Fill Nans
    df["lwr"] = df["lwr"].fillna(0.5)
    df["das"] = df["das"].fillna(0.0)
    df["pddm_3b"] = df["pddm_3b"].fillna(0.0)
    df["psz_decel_3b"] = df["psz_decel_3b"].fillna(0.0)
    
    return df

class ModifiedSavgolCTSSignal:
    """Wraps the standard signal but implements proposed overrides and new entry paths."""
    def __init__(self, base_signal):
        self.base_signal = base_signal
        
    def check_gap_down(self, records, idx, lookback=10):
        start = max(1, idx - lookback)
        for i in range(start, idx + 1):
            prev_low = records[i-1].get("low", 0)
            curr_high = records[i].get("high", 0)
            atr = records[i].get("atr_20", 0)
            if prev_low > curr_high:
                gap_size = prev_low - curr_high
                if atr > 0 and gap_size > (0.3 * atr):
                    return True
        return False

    def check_entry(self, row, prev_row, cfg, records, idx):
        # 1. Run standard entry check
        passed, intensity, meta = self.base_signal.check_entry(row, prev_row, cfg, records, idx)
        if passed:
            return passed, intensity, meta
            
        # 2. Check if it was rejected due to range gate but qualified for Rebound Bypass
        # Let's inspect the rejection reason
        reason = meta.get("reason", "")
        
        # Extract features
        das = row.get("das", 0.0)
        pddm_3b = row.get("pddm_3b", 0.0)
        psz_v = row.get("psz_v", 0.0)
        rp_22 = row.get("range_pos_22", 1.0)
        rp_10 = row.get("range_pos_10", 1.0)
        cwc = row.get("cwc", 0.0)
        pdd_30 = row.get("pdd_30", 0.0)
        bt = row.get("base_tightness", 1.0)
        
        # --- PATH A: Oversold Deceleration Path (ODP) ---
        # Designed specifically for bottom inflections where CTS lags
        # Rejects active falling knives via gap down and CWC check
        if (das < -1.5 or rp_22 < 0.15) and psz_v > 0.01 and row.get("psz_decel_3b", 0.0) > 0.02:
            # Safety checks
            has_gap_down = self.check_gap_down(records, idx, 10)
            if not has_gap_down and cwc > 0.20 and pdd_30 < -2.50 and bt < 0.50:
                details = {
                    "reason": f"Oversold Decel accepted | DAS: {das:.2f} | psz_v: {psz_v:.3f}",
                    "entry_tag": "Oversold-Decel",
                    "score": 80,
                    "conv_score": 80
                }
                return True, 80, details
                
        # --- PATH B: Divergence Rebound Bypass (Universal Cross modification) ---
        # If Universal Cross was rejected for price range but has strong divergence momentum turning up
        if "price not in lower half of weekly range" in reason:
            rebound_bypass = (pddm_3b > 2.0 and psz_v > 0.02)
            if rebound_bypass:
                details = {
                    "reason": f"Universal Cross accepted via Rebound Bypass | pddm_3b: {pddm_3b:.2f}",
                    "entry_tag": EntryTag.UNIVERSAL_CROSS.value + "-ReboundBypass",
                    "score": 85,
                    "conv_score": 85
                }
                return True, 85, details
                
        # --- PATH C: CWC Gate Bypass in Trend Pullback ---
        # If Trend Pullback was rejected for cwc but is deeply oversold and starting to decelerate
        if "cwc below optimized threshold" in reason:
            is_oversold_stretch = (das < -1.5 or rp_22 < 0.15) and psz_v > 0.01
            if is_oversold_stretch:
                # Re-run trend pullback check but skip CWC check
                # For simplicity, if it passed other checks and has oversold stretch, accept it
                # We want to make sure it meets other filters (pdd_30, bt, pdd_120, range_pos_252)
                pdd_120 = row.get("pdd_120", 0.0)
                range_pos_252 = row.get("range_pos_252", 0.0)
                if -5.00 <= pdd_30 <= -2.50 and bt <= 0.45 and pdd_120 >= -4.00 and range_pos_252 <= 0.55:
                    details = {
                        "reason": f"Trend Pullback accepted via CWC Bypass | CWC: {cwc:.3f}",
                        "entry_tag": EntryTag.TREND_PULLBACK.value + "-CWCBypass",
                        "score": 75,
                        "conv_score": 75
                    }
                    return True, 75, details
                    
        return False, 0, meta

    def check_exit(self, row, prev_row, trade, peak_close, bars_held, delivery_bad_count, cwvap_values, exit_cfg, records, idx):
        return self.base_signal.check_exit(row, prev_row, trade, peak_close, bars_held, delivery_bad_count, cwvap_values, exit_cfg, records, idx)


def main():
    watchlist = "NIFTY 50"
    start_date = "2025-12-01"
    end_date = today_str()
    
    symbols = get_watchlist_symbols(watchlist)
    print(f"Running simulation with proposed modifications for {len(symbols)} symbols from {start_date} to {end_date}...")
    
    exit_cfg = SavgolCTSExitConfig()
    base_signal = SignalFactory.get_signal("savgol_cts")
    modified_signal = ModifiedSavgolCTSSignal(base_signal)
    
    all_trades = []
    failed = []
    
    # Cache all ledgers and run simulation
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            df = res.ledger.copy()
            df = add_new_features(df)
            
            # Simulate trades
            trades = simulate_trades(sym, df, None, exit_cfg, modified_signal)
            period_trades = [t for t in trades if start_date <= str(t.entry_date) <= end_date]
            all_trades.extend(period_trades)
        except Exception as e:
            failed.append((sym, str(e)))
            
    print(f"Simulation completed: {len(all_trades)} trades simulated, {len(failed)} failed.")
    
    # Process results into DataFrame
    records = []
    for t in all_trades:
        exit_reason_str = t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)
        records.append({
            "symbol": t.symbol,
            "entry_date": t.entry_date,
            "entry_price": t.entry_price,
            "exit_date": t.exit_date,
            "exit_price": t.exit_price,
            "pnl_pct": t.pnl_pct,
            "duration": t.duration,
            "entry_tag": t.entry_tag,
            "exit_reason": exit_reason_str
        })
        
    df_new = pd.DataFrame(records)
    df_new.to_csv("scratch/modified_trades_20251201.csv", index=False)
    print("Saved modified trades to scratch/modified_trades_20251201.csv")
    
    # Aggregate Metrics
    total_trades = len(df_new)
    if total_trades > 0:
        win_rate = (df_new['pnl_pct'] > 0).mean() * 100
        avg_pnl = df_new['pnl_pct'].mean()
        median_pnl = df_new['pnl_pct'].median()
        avg_duration = df_new['duration'].mean()
        
        gross_profits = df_new[df_new['pnl_pct'] > 0]['pnl_pct'].sum()
        gross_losses = abs(df_new[df_new['pnl_pct'] <= 0]['pnl_pct'].sum())
        profit_factor = gross_profits / gross_losses if gross_losses > 0 else float('inf')
        
        print("\n" + "="*50)
        print("         MODIFIED SIGNAL STRATEGY PERFORMANCE")
        print("="*50)
        print(f"Total Trades:      {total_trades}")
        print(f"Win Rate:          {win_rate:.1f}%")
        print(f"Avg PnL:           {avg_pnl:+.2f}%")
        print(f"Median PnL:        {median_pnl:+.2f}%")
        print(f"Profit Factor:     {profit_factor:.2f}x")
        print(f"Avg Duration:      {avg_duration:.1f} bars")
        print("="*50)
        
        # Display the trades
        print("\n--- ALL SIMULATED TRADES (Sorted by Entry Date) ---")
        print(tabulate(df_new.sort_values(by="entry_date"), headers='keys', tablefmt='psql', showindex=False))
        
        # Let's count how many of our previously missed setups are now captured!
        # Original had 25 trades. Let's see how many trades we have now, and what their entry tags are
        print("\n--- ENTRY TAG DISTRIBUTION ---")
        tag_counts = df_new.groupby("entry_tag").size().reset_index(name="count")
        print(tabulate(tag_counts, headers='keys', tablefmt='psql', showindex=False))
    else:
        print("No trades generated under modified strategy.")

if __name__ == "__main__":
    main()
