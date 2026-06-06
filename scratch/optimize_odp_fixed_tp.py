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

class FixedTPSignal:
    """Signal that only trades ODP and exits on fixed TP, SL, or breakeven stops."""
    def __init__(self, base_signal, tp_pct, sl_pct, be_activation_pct):
        self.base_signal = base_signal
        # Best ODP entry parameters from previous sweeps
        self.das_thresh = -2.0
        self.decel_thresh = 0.04
        self.dv_shock_min = 1.0
        self.rdv_min = 1.2
        
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.be_activation_pct = be_activation_pct
        
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
        pnl_pct = (close / trade.entry_price - 1.0) * 100.0
        peak_pnl = (peak_close / trade.entry_price - 1.0) * 100.0
        
        # 1. Take Profit
        if pnl_pct >= self.tp_pct:
            return f"Take Profit (+{self.tp_pct}%)", delivery_bad_count
            
        # 2. Stop Loss
        if pnl_pct <= -self.sl_pct:
            return f"Stop Loss (-{self.sl_pct}%)", delivery_bad_count
            
        # 3. Breakeven Stop
        if self.be_activation_pct > 0 and peak_pnl >= self.be_activation_pct:
            if pnl_pct <= 0.5:  # Close at +0.5% to cover friction/slippage
                return "Breakeven Shield", delivery_bad_count
                
        # 4. Time stop
        if bars_held >= 60:
            return "Time Stop", delivery_bad_count
            
        return None, delivery_bad_count

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
    
    # Sweep fixed risk parameters
    tp_levels = [5.0, 6.0, 8.0, 10.0, 12.0]
    sl_levels = [3.0, 4.0, 5.0, 6.0, 8.0]
    be_levels = [0.0, 3.0, 4.0]  # 0.0 means disabled
    
    results = []
    
    print("\nStarting Risk-Reward parameter sweep...")
    for tp in tp_levels:
        for sl in sl_levels:
            for be in be_levels:
                sweep_signal = FixedTPSignal(base_signal, tp, sl, be)
                
                trades = []
                for sym, df in cached_ledgers.items():
                    try:
                        sym_trades = simulate_trades(sym, df, None, exit_cfg, sweep_signal)
                        period_trades = [t for t in sym_trades if start_date <= str(t.entry_date)]
                        trades.extend(period_trades)
                    except Exception:
                        pass
                        
                total_trades = len(trades)
                if total_trades >= 5:  # Small threshold is fine to see what is possible
                    pnls = [t.pnl_pct for t in trades]
                    avg_pnl = np.mean(pnls)
                    win_rate = sum(1 for p in pnls if p > 0) / total_trades * 100
                    avg_dur = np.mean([t.duration for t in trades])
                    
                    gross_win = sum(p for p in pnls if p > 0)
                    gross_loss = abs(sum(p for p in pnls if p <= 0))
                    pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
                    
                    results.append({
                        "TP": f"+{tp}%",
                        "SL": f"-{sl}%",
                        "BE Trigger": f"+{be}%" if be > 0 else "None",
                        "Trades": total_trades,
                        "Win Rate": f"{win_rate:.1f}%",
                        "Avg P&L": f"{avg_pnl:+.2f}%",
                        "Profit Factor": f"{pf:.2f}x",
                        "Avg Duration": f"{avg_dur:.1f}",
                        "_avg_pnl_raw": avg_pnl
                    })
                    
    # Sort
    df_results = pd.DataFrame(results).sort_values(by="_avg_pnl_raw", ascending=False)
    print("\n--- RISK-REWARD SWEEP RESULTS (Sorted by Avg P&L) ---")
    print(tabulate(df_results.head(40).drop(columns=["_avg_pnl_raw"]), headers="keys", tablefmt="grid", showindex=False))

if __name__ == "__main__":
    main()
