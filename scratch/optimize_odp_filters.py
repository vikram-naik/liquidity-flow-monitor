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

class FilteredODPSignal:
    """ODP Signal with customizable strict filters."""
    def __init__(self, base_signal, das_thresh, decel_thresh, filter_regime, fas_min, cwc_min, bt_max, exit_mode, hard_stop):
        self.base_signal = base_signal
        self.das_thresh = das_thresh
        self.decel_thresh = decel_thresh
        self.filter_regime = filter_regime  # True/False
        self.fas_min = fas_min
        self.cwc_min = cwc_min
        self.bt_max = bt_max
        self.exit_mode = exit_mode
        self.hard_stop = hard_stop
        
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
        das = row.get("das", 0.0)
        psz_v = row.get("psz_v", 0.0)
        psz_decel = row.get("psz_decel_3b", 0.0)
        rp_22 = row.get("range_pos_22", 1.0)
        cwc = row.get("cwc", 0.0)
        pdd_30 = row.get("pdd_30", 0.0)
        bt = row.get("base_tightness", 1.0)
        fas = row.get("fas", 0.0)
        regime = row.get("regime", "notrend")
        
        # 1. Base ODP checks
        if (das < self.das_thresh or rp_22 < 0.15) and psz_v > 0.01 and psz_decel > self.decel_thresh:
            
            # 2. Strict Filters
            if self.filter_regime and regime == "downtrend":
                return False, 0, {}
            if fas < self.fas_min:
                return False, 0, {}
            if cwc < self.cwc_min:
                return False, 0, {}
            if bt > self.bt_max:
                return False, 0, {}
                
            # Gap down check
            has_gap_down = self.check_gap_down(records, idx, 10)
            if not has_gap_down and pdd_30 < -2.50:
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
        pnl_pct = (close / trade.entry_price - 1.0) * 100.0
        
        # 1. Hard Stop
        if pnl_pct <= -self.hard_stop:
            return "Hard Stop", delivery_bad_count
            
        # 2. Reversion Target
        if self.exit_mode == "mean_reversion":
            if close >= cwvap:
                return "Mean Reversion Target", delivery_bad_count
            if bars_held >= 60:
                return "Time Stop", delivery_bad_count
            return None, delivery_bad_count
            
        return self.base_signal.check_exit(row, prev_row, trade, peak_close, bars_held, delivery_bad_count, cwvap_values, exit_cfg, records, idx)

def main():
    watchlist = "NIFTY 50"
    start_date = "2019-01-01"
    
    symbols = get_watchlist_symbols(watchlist)
    print("Loading data...")
    
    cached_ledgers = {}
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            df = res.ledger.copy()
            df = add_new_features(df)
            cached_ledgers[sym] = df
        except Exception:
            pass
            
    print(f"Loaded {len(cached_ledgers)} ledgers.")
    
    base_signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()
    
    # Define Filter Sweep Options
    regime_options = [False, True]
    fas_options = [-99.0, -0.5, -0.3]  # -99.0 means no filter
    cwc_options = [0.20, 0.40, 0.45]
    bt_options = [99.0, 0.45, 0.38]     # 99.0 means no filter
    
    results = []
    
    print("\nStarting filter sweep...")
    for filter_regime in regime_options:
        for fas_min in fas_options:
            for cwc_min in cwc_options:
                for bt_max in bt_options:
                    # Let's test standard and mean reversion exits with a 10% hard stop
                    for exit_mode in ["standard", "mean_reversion"]:
                        sweep_signal = FilteredODPSignal(
                            base_signal, -2.0, 0.04, filter_regime, fas_min, cwc_min, bt_max, exit_mode, 10.0
                        )
                        
                        trades = []
                        for sym, df in cached_ledgers.items():
                            try:
                                sym_trades = simulate_trades(sym, df, None, exit_cfg, sweep_signal)
                                period_trades = [t for t in sym_trades if start_date <= str(t.entry_date)]
                                trades.extend(period_trades)
                            except Exception:
                                pass
                                
                        total_trades = len(trades)
                        # We want at least 15 trades over 7.5 years to ensure it's not a tiny sample size (e.g. 2 trades)
                        if total_trades >= 15:
                            pnls = [t.pnl_pct for t in trades]
                            avg_pnl = np.mean(pnls)
                            win_rate = sum(1 for p in pnls if p > 0) / total_trades * 100
                            avg_dur = np.mean([t.duration for t in trades])
                            
                            gross_win = sum(p for p in pnls if p > 0)
                            gross_loss = abs(sum(p for p in pnls if p <= 0))
                            pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
                            
                            results.append({
                                "Exit": exit_mode,
                                "NoDowntrend": "Yes" if filter_regime else "No",
                                "Min FAS": fas_min if fas_min > -90 else "None",
                                "Min CWC": cwc_min,
                                "Max BT": bt_max if bt_max < 90 else "None",
                                "Trades": total_trades,
                                "Win Rate": f"{win_rate:.1f}%",
                                "Avg P&L": f"{avg_pnl:+.2f}%",
                                "Profit Factor": f"{pf:.2f}x",
                                "Avg Duration": f"{avg_dur:.1f}",
                                "_avg_pnl_raw": avg_pnl
                            })
                            
    # Sort
    df_results = pd.DataFrame(results).sort_values(by="_avg_pnl_raw", ascending=False)
    print("\n--- FILTER OPTIMIZATION RESULTS (Sorted by Avg P&L) ---")
    print(tabulate(df_results.head(40).drop(columns=["_avg_pnl_raw"]), headers="keys", tablefmt="grid", showindex=False))

if __name__ == "__main__":
    main()
