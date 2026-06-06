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

class OptimizedODPSignal:
    """Mock signal to run advanced ODP parameters and trailing exits."""
    def __init__(self, base_signal, das_thresh, decel_thresh, dv_shock_min, rdv_min, exit_mode, hard_stop_pct, trailing_start_pct, trailing_dist_pct):
        self.base_signal = base_signal
        self.das_thresh = das_thresh
        self.decel_thresh = decel_thresh
        self.dv_shock_min = dv_shock_min
        self.rdv_min = rdv_min
        self.exit_mode = exit_mode
        self.hard_stop_pct = hard_stop_pct
        self.trailing_start_pct = trailing_start_pct
        self.trailing_dist_pct = trailing_dist_pct
        
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
        
        # New volume climax features
        dv_shock = row.get("dv_shock", 0.0)
        rdv = row.get("rdv", 0.0)
        
        if (das < self.das_thresh or rp_22 < 0.15) and psz_v > 0.01 and psz_decel > self.decel_thresh:
            if dv_shock >= self.dv_shock_min and rdv >= self.rdv_min:
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
        
        pnl_pct = (close / trade.entry_price - 1.0) * 100.0
        peak_pnl = (peak_close / trade.entry_price - 1.0) * 100.0
        
        # 1. Hard Stop
        if pnl_pct <= -self.hard_stop_pct:
            return "Hard Stop", delivery_bad_count
            
        # 2. Breakeven Stop: if price went up by trailing_start_pct, move stop to entry (exit if pnl drops below +0.5% for transaction costs)
        if self.trailing_start_pct > 0 and peak_pnl >= self.trailing_start_pct:
            if pnl_pct <= 0.5:
                return "Breakeven Shield", delivery_bad_count
                
        # 3. Trailing Stop: if price went up by trailing_start_pct, trailing stop activates at peak_pnl - trailing_dist_pct
        if self.trailing_start_pct > 0 and peak_pnl >= self.trailing_start_pct:
            if pnl_pct <= (peak_pnl - self.trailing_dist_pct):
                return f"Trailing Stop (Peak: {peak_pnl:.1f}%)", delivery_bad_count
                
        # 4. Mode Exits
        if self.exit_mode == "mean_reversion":
            if close >= cwvap:
                return "Mean Reversion Target", delivery_bad_count
            if bars_held >= 60:
                return "Max Hold Time Stop", delivery_bad_count
            return None, delivery_bad_count
            
        # Standard fallback
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
        except Exception:
            pass
            
    print(f"Loaded {len(cached_ledgers)} ledgers.")
    
    base_signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()
    
    # Define Sweep Parameters targeting PnL maximization
    das_options = [-1.8, -2.0, -2.2]
    decel_options = [0.02, 0.04]
    
    # Volume shock constraints (stricter entries)
    vol_options = [
        (0.0, 0.0),   # No constraint
        (0.5, 1.0),   # Mild climax
        (1.0, 1.2),   # Solid institutional absorption
        (1.5, 1.5)    # Extreme institutional absorption
    ]
    
    # Stop & Trailing combinations
    exit_options = [
        # (exit_mode, hard_stop_pct, trailing_start_pct, trailing_dist_pct)
        ("mean_reversion", 10.0, 0.0, 0.0),    # MR Baseline
        ("mean_reversion", 8.0, 0.0, 0.0),     # MR Tight Stop
        ("mean_reversion", 6.0, 0.0, 0.0),     # MR Very Tight Stop
        ("mean_reversion", 10.0, 4.0, 3.0),    # MR + Breakeven + Trail
        ("mean_reversion", 8.0, 5.0, 4.0),     # MR + Breakeven + Trail
        ("standard", 10.0, 4.0, 3.0),          # Std + Breakeven + Trail
        ("standard", 8.0, 5.0, 4.0),           # Std + Breakeven + Trail
    ]
    
    results = []
    
    print("\nStarting Advanced Parameter Sweep...")
    for exit_mode, hard_stop, trail_start, trail_dist in exit_options:
        for das_thresh in das_options:
            for decel_thresh in decel_options:
                for dv_min, rdv_min in vol_options:
                    sweep_signal = OptimizedODPSignal(
                        base_signal, das_thresh, decel_thresh, dv_min, rdv_min,
                        exit_mode, hard_stop, trail_start, trail_dist
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
                    if total_trades >= 8:  # Require statistical significance
                        pnls = [t.pnl_pct for t in trades]
                        avg_pnl = np.mean(pnls)
                        win_rate = sum(1 for p in pnls if p > 0) / total_trades * 100
                        avg_dur = np.mean([t.duration for t in trades])
                        
                        gross_win = sum(p for p in pnls if p > 0)
                        gross_loss = abs(sum(p for p in pnls if p <= 0))
                        pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
                        
                        results.append({
                            "Exit Mode": exit_mode,
                            "Hard Stop": f"-{hard_stop}%",
                            "Trail (St/Dist)": f"{trail_start}/{trail_dist}" if trail_start > 0 else "None",
                            "DAS Limit": das_thresh,
                            "Decel Floor": decel_thresh,
                            "Min DV Shock/RDV": f"{dv_min}/{rdv_min}",
                            "Trades": total_trades,
                            "Win Rate": f"{win_rate:.1f}%",
                            "Avg P&L": f"{avg_pnl:+.2f}%",
                            "Profit Factor": f"{pf:.2f}x",
                            "Avg Duration": f"{avg_dur:.1f}",
                            "_avg_pnl_raw": avg_pnl
                        })
                        
    # Sort results
    df_results = pd.DataFrame(results).sort_values(by="_avg_pnl_raw", ascending=False)
    print("\n--- OPTIMIZATION RESULTS (Sorted by Avg P&L) ---")
    print(tabulate(df_results.head(40).drop(columns=["_avg_pnl_raw"]), headers="keys", tablefmt="grid", showindex=False))

if __name__ == "__main__":
    main()
