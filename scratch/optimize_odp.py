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
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from scripts.walk_forward import simulate_trades, get_watchlist_symbols
from scratch.verify_proposed_logic import add_new_features

class CustomSweepSignal:
    """Mock signal to sweep ODP entry and exit parameters."""
    def __init__(self, base_signal, das_thresh, decel_thresh, exit_mode, hard_stop_pct):
        self.base_signal = base_signal
        self.das_thresh = das_thresh
        self.decel_thresh = decel_thresh
        self.exit_mode = exit_mode  # "standard" or "mean_reversion"
        self.hard_stop_pct = hard_stop_pct
        
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
        # We ONLY want to trade the Oversold-Decel path for this sweep to isolate its performance
        das = row.get("das", 0.0)
        psz_v = row.get("psz_v", 0.0)
        psz_decel = row.get("psz_decel_3b", 0.0)
        rp_22 = row.get("range_pos_22", 1.0)
        cwc = row.get("cwc", 0.0)
        pdd_30 = row.get("pdd_30", 0.0)
        bt = row.get("base_tightness", 1.0)
        
        # Apply sweep thresholds
        if (das < self.das_thresh or rp_22 < 0.15) and psz_v > 0.01 and psz_decel > self.decel_thresh:
            has_gap_down = self.check_gap_down(records, idx, 10)
            if not has_gap_down and cwc > 0.20 and pdd_30 < -2.50 and bt < 0.50:
                return True, 80, {
                    "reason": "Oversold Decel",
                    "entry_tag": "Oversold-Decel",
                    "score": 80,
                    "conv_score": 80
                }
        return False, 0, {}

    def check_exit(self, row, prev_row, trade, peak_close, bars_held, delivery_bad_count, cwvap_values, exit_cfg, records, idx):
        close = row.get("close", np.nan)
        cwvap = row.get("cwvap", np.nan)
        
        # Mode 1: Mean Reversion Target Exit (hold until price crosses CWVAP or hits hard stop)
        if self.exit_mode == "mean_reversion":
            # 1. Hard Stop
            pnl_pct = (close / trade.entry_price - 1.0) * 100.0
            if pnl_pct <= -self.hard_stop_pct:
                return "Hard Stop", delivery_bad_count
            
            # 2. Reversion Target: Close crosses above CWVAP
            if close >= cwvap:
                return "Mean Reversion Target", delivery_bad_count
                
            # 3. Maximum hold time stop (e.g. 60 bars) to avoid capital lockup
            if bars_held >= 60:
                return "Max Hold Time Stop", delivery_bad_count
                
            return None, delivery_bad_count
            
        # Mode 2: Standard Exit
        return self.base_signal.check_exit(row, prev_row, trade, peak_close, bars_held, delivery_bad_count, cwvap_values, exit_cfg, records, idx)

def main():
    watchlist = "NIFTY 50"
    start_date = "2019-01-01"
    
    symbols = get_watchlist_symbols(watchlist)
    print("Loading data and pre-calculating ledgers...")
    
    cached_ledgers = {}
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            df = res.ledger.copy()
            df = add_new_features(df)
            cached_ledgers[sym] = df
        except Exception as e:
            pass
            
    print(f"Loaded {len(cached_ledgers)} ledgers.")
    
    base_signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()
    
    # Define Sweep Parameters
    das_options = [-1.5, -1.8, -2.0, -2.2]
    decel_options = [0.02, 0.04, 0.06]
    exit_modes = ["standard", "mean_reversion"]
    hard_stops = [8.0, 10.0]
    
    results = []
    
    print("\nStarting Parameter Sweep for ODP Path...")
    for exit_mode in exit_modes:
        stops_to_test = hard_stops if exit_mode == "mean_reversion" else [10.0]
        for hard_stop in stops_to_test:
            for das_thresh in das_options:
                for decel_thresh in decel_options:
                    sweep_signal = CustomSweepSignal(base_signal, das_thresh, decel_thresh, exit_mode, hard_stop)
                    
                    trades = []
                    for sym, df in cached_ledgers.items():
                        try:
                            sym_trades = simulate_trades(sym, df, None, exit_cfg, sweep_signal)
                            period_trades = [t for t in sym_trades if start_date <= str(t.entry_date)]
                            trades.extend(period_trades)
                        except Exception:
                            pass
                            
                    total_trades = len(trades)
                    if total_trades >= 10:  # Require statistical significance (at least 10 trades)
                        pnls = [t.pnl_pct for t in trades]
                        avg_pnl = np.mean(pnls)
                        win_rate = sum(1 for p in pnls if p > 0) / total_trades * 100
                        avg_dur = np.mean([t.duration for t in trades])
                        
                        gross_win = sum(p for p in pnls if p > 0)
                        gross_loss = abs(sum(p for p in pnls if p <= 0))
                        pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
                        
                        results.append({
                            "Exit Mode": exit_mode,
                            "Hard Stop%": hard_stop,
                            "DAS Ceiling": das_thresh,
                            "Decel Floor": decel_thresh,
                            "Trades": total_trades,
                            "Win Rate": f"{win_rate:.1f}%",
                            "Avg P&L": f"{avg_pnl:+.2f}%",
                            "Profit Factor": f"{pf:.2f}x",
                            "Avg Duration": f"{avg_dur:.1f}",
                            "_avg_pnl_raw": avg_pnl
                        })
                        
    # Sort results by average P&L descending
    df_results = pd.DataFrame(results).sort_values(by="_avg_pnl_raw", ascending=False)
    print("\n--- PARAMETER SWEEP RESULTS (Sorted by Avg P&L) ---")
    print(tabulate(df_results.drop(columns=["_avg_pnl_raw"]), headers="keys", tablefmt="grid", showindex=False))

if __name__ == "__main__":
    main()
