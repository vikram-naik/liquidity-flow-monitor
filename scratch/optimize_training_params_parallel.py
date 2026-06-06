import sys
import numpy as np
import pandas as pd
from pathlib import Path
import copy
import math
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, "/home/vn/python-projects/liquidity-flow-monitor")

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from scripts.walk_forward import simulate_trades
from scripts.train_symbol_weights import simulate_single_trade
from scratch.optimize_training_params import label_candidates_custom, train_bayesian_model_custom

FALLBACKS = ["HDFCBANK", "HINDUNILVR", "NESTLEIND", "ONGC", "TCS", "WIPRO"]

def sweep_single_symbol(sym):
    try:
        engine = DivergenceEngine(sym, start_date=None, end_date=None)
        res = engine.run()
        ledger = res.ledger
    except Exception as e:
        return {"symbol": sym, "success": False, "error": str(e)}
        
    signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()

    num_bins_choices = [3, 4]
    success_mult_choices = [1.2, 1.5, 1.8]
    dd_limit_choices = [5.0, 6.0, 7.0]

    best_run = None
    best_avg = -99.0

    for num_bins in num_bins_choices:
        for success_mult in success_mult_choices:
            for dd_limit in dd_limit_choices:
                candidates, labels, s_t = label_candidates_custom(
                    sym, ledger, signal, exit_cfg, success_mult, dd_limit
                )
                num_success = sum(1 for v in labels.values() if v == 1)
                num_failure = sum(1 for v in labels.values() if v == 0)
                
                if num_success == 0 or num_failure == 0:
                    continue
                    
                feature_bins, feature_weights = train_bayesian_model_custom(
                    ledger, candidates, labels, num_bins
                )
                
                cfg = SavgolCTSEntryConfig()
                cfg.cooldown_enabled = False
                cfg.cdvl_cts.enabled = False
                cfg.universal_cross.enabled = False
                cfg.trend_pullback_enabled = False
                cfg.flow_momentum.enabled = False
                cfg.coherent_pullback.enabled = False
                cfg.anchor_shock_pullback.enabled = False
                cfg.springboard.enabled = False
                cfg.oversold_decel.enabled = False
                
                cfg.custom_bayesian.enabled = True
                cfg.custom_bayesian.feature_bins = feature_bins
                cfg.custom_bayesian.feature_weights = feature_weights
                
                for th in np.arange(-4.0, 8.01, 0.2):
                    cfg.custom_bayesian.score_threshold = float(th)
                    trades = simulate_trades(sym, ledger, cfg, exit_cfg, signal)
                    trades_2019 = [t for t in trades if t.entry_date >= "2019-01-01"]
                    if trades_2019:
                        pnls = [t.pnl_pct for t in trades_2019]
                        avg_pnl = np.mean(pnls)
                        sl_hits = sum(1 for t in trades_2019 if t.mae_pct >= 8.0)
                        
                        if avg_pnl > best_avg:
                            best_avg = avg_pnl
                            best_run = {
                                "num_bins": num_bins,
                                "success_mult": success_mult,
                                "dd_limit": dd_limit,
                                "threshold": th,
                                "trades": len(trades_2019),
                                "sl_hits": sl_hits,
                                "avg_pnl": avg_pnl
                            }
                            
    if best_run:
        return {
            "symbol": sym,
            "success": True,
            "found": True,
            "best": best_run
        }
    else:
        return {
            "symbol": sym,
            "success": True,
            "found": False
        }

def main():
    print("Starting parallel training parameter search for the 6 fallback symbols...")
    with ProcessPoolExecutor() as executor:
        futures = {executor.submit(sweep_single_symbol, sym): sym for sym in FALLBACKS}
        
        for idx, fut in enumerate(as_completed(futures)):
            sym = futures[fut]
            try:
                res = fut.result()
                if res["success"]:
                    if res["found"]:
                        b = res["best"]
                        print(f"[{idx+1}/{len(FALLBACKS)}] Optimized {sym:<15} | Bins: {b['num_bins']} | Success Mult: {b['success_mult']} | DD Limit: {b['dd_limit']} | Th: {b['threshold']:.2f} | Trades: {b['trades']} | Avg P&L: {b['avg_pnl']:.2f}% | SL Hits: {b['sl_hits']}")
                    else:
                        print(f"[{idx+1}/{len(FALLBACKS)}] No configuration found for {sym}")
                else:
                    print(f"[{idx+1}/{len(FALLBACKS)}] Failed {sym}: {res['error']}")
            except Exception as e:
                print(f"[{idx+1}/{len(FALLBACKS)}] Error running {sym}: {e}")

if __name__ == "__main__":
    main()
