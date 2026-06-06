import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from scripts.walk_forward import simulate_trades, get_watchlist_symbols
from scratch.verify_proposed_logic import add_new_features
from scratch.optimize_odp_combined import SweeperODPSignal

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
    
    # 108 combinations exhaustive targeted grid
    das_vals = [-1.8, -2.0, -2.2]
    cwc_vals = [0.40, 0.45, 0.50]
    fas_vals = [-0.3, -0.2, -0.1]
    bt_vals = [0.38, 0.35]
    vol_vals = [(0.0, 0.0), (0.5, 1.0)]
    
    grid = []
    for das in das_vals:
        for cwc in cwc_vals:
            for fas in fas_vals:
                for bt in bt_vals:
                    for vol in vol_vals:
                        grid.append({
                            "das_thresh": das,
                            "decel_thresh": 0.04,
                            "cwc_min": cwc,
                            "fas_min": fas,
                            "bt_max": bt,
                            "dv_shock_min": vol[0],
                            "rdv_min": vol[1],
                            "filter_regime": False,
                            "exit_mode": "standard",
                            "hard_stop_pct": 10.0,
                            "tp_pct": 0.0,
                            "be_activation_pct": 0.0
                        })
                                    
    print(f"Total targeted grid size: {len(grid)} configurations. Starting exhaustive sweep...")
    
    results = []
    for idx, params in enumerate(grid):
        if idx > 0 and idx % 10 == 0:
            print(f"Processed {idx} / {len(grid)} configs...")
            
        sweep_signal = SweeperODPSignal(base_signal, params)
        
        trades = []
        for sym, df in cached_ledgers.items():
            try:
                sym_trades = simulate_trades(sym, df, None, exit_cfg, sweep_signal)
                period_trades = [t for t in sym_trades if start_date <= str(t.entry_date)]
                trades.extend(period_trades)
            except Exception:
                pass
                
        total_trades = len(trades)
        if total_trades >= 10:  # Require statistical significance
            pnls = [t.pnl_pct for t in trades]
            avg_pnl = np.mean(pnls)
            win_rate = sum(1 for p in pnls if p > 0) / total_trades * 100
            avg_dur = np.mean([t.duration for t in trades])
            
            gross_win = sum(p for p in pnls if p > 0)
            gross_loss = abs(sum(p for p in pnls if p <= 0))
            pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
            
            results.append({
                "DAS": params["das_thresh"],
                "CWC": params["cwc_min"],
                "FAS": params["fas_min"],
                "BT": params["bt_max"],
                "Vol": f"{params['dv_shock_min']}/{params['rdv_min']}",
                "Trades": total_trades,
                "Win Rate": f"{win_rate:.1f}%",
                "Avg P&L": f"{avg_pnl:+.2f}%",
                "Profit Factor": f"{pf:.2f}x",
                "Avg Duration": f"{avg_dur:.1f}",
                "_avg_pnl_raw": avg_pnl
            })
            
    # Convert and sort
    df_results = pd.DataFrame(results)
    if not df_results.empty:
        df_results = df_results.sort_values(by="_avg_pnl_raw", ascending=False)
        print("\n--- TARGETED SWEEP TOP RESULTS (Sorted by Avg P&L) ---")
        print(tabulate(df_results.head(45).drop(columns=["_avg_pnl_raw"]), headers="keys", tablefmt="grid", showindex=False))
        
        df_results.to_csv("scratch/targeted_sweep_results.csv", index=False)
        print("\nSaved all sweep results to scratch/targeted_sweep_results.csv")
    else:
        print("No configurations generated >= 10 trades.")

if __name__ == "__main__":
    main()
