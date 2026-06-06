import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate
import multiprocessing

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from scripts.walk_forward import get_watchlist_symbols
from scratch.verify_proposed_logic import add_new_features

# Global dict to hold cached data in worker processes
_worker_ledgers = None

def init_worker(ledgers):
    global _worker_ledgers
    _worker_ledgers = ledgers

def evaluate_config(params):
    # Retrieve base signal and exit config inside the worker process
    from src.trading.signals import SignalFactory
    from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
    from scripts.walk_forward import simulate_trades
    from scratch.optimize_odp_combined import SweeperODPSignal
    import numpy as np

    base_signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()
    
    sweep_signal = SweeperODPSignal(base_signal, params)
    
    trades = []
    for sym, df in _worker_ledgers.items():
        try:
            sym_trades = simulate_trades(sym, df, None, exit_cfg, sweep_signal)
            # Filter by date since 2019-01-01
            period_trades = [t for t in sym_trades if "2019-01-01" <= str(t.entry_date)]
            trades.extend(period_trades)
        except Exception:
            pass
            
    total_trades = len(trades)
    if total_trades >= 12:  # require at least 12 trades over 7.5 years
        pnls = [t.pnl_pct for t in trades]
        avg_pnl = np.mean(pnls)
        win_rate = sum(1 for p in pnls if p > 0) / total_trades * 100
        avg_dur = np.mean([t.duration for t in trades])
        
        gross_win = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p <= 0))
        pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
        
        return {
            "DAS": params["das_thresh"],
            "CWC": params["cwc_min"],
            "FAS": params["fas_min"],
            "BT": params["bt_max"],
            "Vol": f"{params['dv_shock_min']}/{params['rdv_min']}",
            "SL": f"-{params['hard_stop_pct']}%",
            "TP": f"+{params['tp_pct']}%" if params["tp_pct"] > 0 else "None",
            "BE": f"+{params['be_activation_pct']}%" if params["be_activation_pct"] > 0 else "None",
            "Trades": total_trades,
            "Win Rate": f"{win_rate:.1f}%",
            "Avg P&L": f"{avg_pnl:+.2f}%",
            "Profit Factor": f"{pf:.2f}x",
            "Avg Duration": f"{avg_dur:.1f}",
            "_avg_pnl_raw": avg_pnl
        }
    return None

def main():
    watchlist = "NIFTY 50"
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
    
    # 1,620 combinations grid search
    das_vals = [-1.8, -2.0, -2.2]
    cwc_vals = [0.35, 0.40, 0.45, 0.50]
    fas_vals = [-0.3, -0.2, -0.1, 0.0, 0.1]
    bt_vals = [0.38, 0.35, 0.32]
    vol_vals = [(0.0, 0.0), (0.5, 1.0), (1.0, 1.2)]
    exit_opts = [
        # (tp, be)
        (0.0, 0.0),
        (12.0, 4.0),
        (15.0, 5.0)
    ]
    
    grid = []
    for das in das_vals:
        for cwc in cwc_vals:
            for fas in fas_vals:
                for bt in bt_vals:
                    for vol in vol_vals:
                        for tp, be in exit_opts:
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
                                "tp_pct": tp,
                                "be_activation_pct": be
                            })
                            
    num_configs = len(grid)
    print(f"Total grid size: {num_configs} configurations. Parallelizing using multiprocessing...")
    
    # Determine the number of processes (use user-suggested cores, leaving 2 for system headroom)
    num_cores = min(10, multiprocessing.cpu_count())
    print(f"Using {num_cores} CPU cores for parallel execution.")
    
    # Run the grid in parallel
    with multiprocessing.Pool(processes=num_cores, initializer=init_worker, initargs=(cached_ledgers,)) as pool:
        raw_results = pool.map(evaluate_config, grid)
        
    # Filter out None results and sort
    results = [r for r in raw_results if r is not None]
    
    df_results = pd.DataFrame(results)
    if not df_results.empty:
        df_results = df_results.sort_values(by="_avg_pnl_raw", ascending=False)
        print("\n--- PARALLEL TARGETED SWEEP TOP RESULTS (Sorted by Avg P&L) ---")
        print(tabulate(df_results.head(45).drop(columns=["_avg_pnl_raw"]), headers="keys", tablefmt="grid", showindex=False))
        
        # Save results to a CSV
        df_results.to_csv("scratch/parallel_sweep_results.csv", index=False)
        print("\nSaved all sweep results to scratch/parallel_sweep_results.csv")
        
        # Check if any configurations reached > 5.0%
        best_setups = df_results[df_results["_avg_pnl_raw"] >= 5.0]
        if not best_setups.empty:
            print("\n--- SETUPS ACHIEVING AVG P&L >= 5.0% ---")
            print(tabulate(best_setups.head(20).drop(columns=["_avg_pnl_raw"]), headers="keys", tablefmt="grid", showindex=False))
        else:
            print("\nNo setups achieved Avg P&L >= 5.0%. Top performer:")
            print(df_results.iloc[0])
    else:
        print("No configurations generated >= 12 trades.")

if __name__ == "__main__":
    main()
