#!/usr/bin/env python
import sqlite3
import pandas as pd
import numpy as np
import math
import sys
import copy
import os
import json
import argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

# Add root folder to sys.path
sys.path.insert(0, "/home/vn/python-projects/liquidity-flow-monitor")

from src.database import BW_CONFIGS_DIR, EXCLUDED_SYMBOLS_PATH
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.savgol_cts.symbol_configs import (
    get_symbol_entry_config,
    add_to_exclusion_list,
    remove_from_exclusion_list,
    is_symbol_excluded
)
from scripts.walk_forward import simulate_trades, get_watchlist_symbols
from scripts.train_symbol_weights_parallel import optimize_single_symbol

# Helper function to compute score
def calculate_bwo_score(trades_count, avg_pnl, sl_hits):
    sl_ratio = sl_hits / trades_count if trades_count > 0 else 0.0
    return -1000.0 * sl_ratio + 10.0 * min(trades_count, 15) + avg_pnl

def backtest_config(sym, ledger, entry_cfg, exit_cfg, signal):
    trades = simulate_trades(sym, ledger, entry_cfg, exit_cfg, signal)
    trades_2019 = [t for t in trades if t.entry_date >= "2019-01-01"]
    
    trades_count = len(trades_2019)
    if trades_count > 0:
        pnls = [t.pnl_pct for t in trades_2019]
        avg_pnl = np.mean(pnls)
        sl_hits = sum(1 for t in trades_2019 if t.mae_pct >= 8.0)
    else:
        avg_pnl = 0.0
        sl_hits = 0
        
    score = calculate_bwo_score(trades_count, avg_pnl, sl_hits)
    return {
        "trades": trades_count,
        "avg_pnl": avg_pnl,
        "sl_hits": sl_hits,
        "score": score
    }

def serialize_and_save_config(sym, details, output_dir):
    config_dict = {
        "trend_pullback_enabled": False,
        "cdvl_cts": {"enabled": False},
        "universal_cross": {"enabled": False},
        "flow_momentum": {"enabled": False},
        "coherent_pullback": {"enabled": False},
        "anchor_shock_pullback": {"enabled": False},
        "springboard": {"enabled": False},
        "oversold_decel": {"enabled": False},
        "custom_bayesian": {
            "enabled": True,
            "score_threshold": float(details["score_threshold"]),
            "feature_bins": {},
            "feature_weights": {}
        }
    }
    
    # Serialize feature bins (convert inf to strings for valid JSON)
    for feat, bins in details["feature_bins"].items():
        bins_list = []
        for b in bins:
            if math.isinf(b):
                bins_list.append("-inf" if b < 0 else "inf")
            else:
                bins_list.append(float(b))
        config_dict["custom_bayesian"]["feature_bins"][feat] = bins_list
        
    # Serialize feature weights (convert inf to strings for valid JSON)
    for feat, weights in details["feature_weights"].items():
        weights_list = []
        for left, right, w in weights:
            l_val = "-inf" if math.isinf(left) and left < 0 else float(left)
            r_val = "inf" if math.isinf(right) and right > 0 else float(right)
            weights_list.append([l_val, r_val, float(w)])
        config_dict["custom_bayesian"]["feature_weights"][feat] = weights_list
        
    # Write to JSON file
    out_path = Path(output_dir) / f"{sym}.json"
    os.makedirs(output_dir, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(config_dict, f, indent=4)
    return out_path

def evaluate_and_sync_symbol(sym):
    try:
        # Load ledger data
        engine = DivergenceEngine(sym, start_date=None, end_date=None)
        res = engine.run()
        ledger = res.ledger
    except Exception as e:
        return {"symbol": sym, "success": False, "error": f"Failed to load engine/ledger: {e}"}

    signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()
    
    # Target P&L
    target_pnl = 0.0 if sym in ["ETERNAL", "INFY", "JIOFIN"] else 5.0

    # 1. Backtest existing Champion configuration (if exists)
    champ_exists = False
    champ_passed = False
    champ_metrics = {"trades": 0, "avg_pnl": 0.0, "sl_hits": 0, "score": -9999.0}
    
    json_path = os.path.join(BW_CONFIGS_DIR, f"{sym}.json")
    # Note: If it is currently excluded, we don't treat Champion as active
    if os.path.exists(json_path) and not is_symbol_excluded(sym):
        champ_exists = True
        try:
            champ_cfg = get_symbol_entry_config(sym)
            champ_metrics = backtest_config(sym, ledger, champ_cfg, exit_cfg, signal)
            
            # Check if Champion still passes gates
            champ_sl_ratio = champ_metrics["sl_hits"] / champ_metrics["trades"] if champ_metrics["trades"] > 0 else 0.0
            if (champ_metrics["trades"] >= 3 and 
                champ_metrics["avg_pnl"] >= target_pnl and 
                champ_sl_ratio <= 0.25):
                champ_passed = True
        except Exception as e:
            print(f"[{sym}] Error backtesting Champion: {e}")

    # 2. Run BWO optimizer to find candidate Challenger
    challenger_opt = optimize_single_symbol(sym)
    chal_passed = False
    chal_metrics = {"trades": 0, "avg_pnl": 0.0, "sl_hits": 0, "score": -9999.0}
    
    if challenger_opt and challenger_opt.get("success"):
        chal_metrics = {
            "trades": challenger_opt["trades"],
            "avg_pnl": challenger_opt["avg_pnl"],
            "sl_hits": challenger_opt["sl_hits"],
            "score": calculate_bwo_score(
                challenger_opt["trades"],
                challenger_opt["avg_pnl"],
                challenger_opt["sl_hits"]
            )
        }
        
        # Check safety gates on Challenger
        chal_sl_ratio = chal_metrics["sl_hits"] / chal_metrics["trades"] if chal_metrics["trades"] > 0 else 0.0
        if (chal_metrics["trades"] >= 3 and 
            chal_metrics["avg_pnl"] >= target_pnl and 
            chal_sl_ratio <= 0.25):
            chal_passed = True

    # 3. Decision Matrix
    status = "RETAINED"
    reason = "Champion is optimal."
    
    if chal_passed:
        # If Challenger passes, check if we should promote it
        # Promote if Challenger score is strictly better than Champion by a delta of 0.5, or if Champion does not exist/failed gates
        if not champ_exists or not champ_passed or (chal_metrics["score"] > champ_metrics["score"] + 0.5):
            status = "PROMOTED"
            reason = f"Challenger score ({chal_metrics['score']:.2f}) beats Champion score ({champ_metrics['score']:.2f})."
            # Write to JSON and remove from exclusion
            serialize_and_save_config(sym, challenger_opt, BW_CONFIGS_DIR)
            remove_from_exclusion_list(sym)
            
            # If the bak file exists, clean it up
            bak_path = json_path + ".bak"
            if os.path.exists(bak_path):
                try:
                    os.remove(bak_path)
                except Exception:
                    pass
        else:
            status = "RETAINED"
            reason = f"Champion score ({champ_metrics['score']:.2f}) is superior or Challenger ({chal_metrics['score']:.2f}) did not beat delta."
    else:
        # Challenger failed safety gates
        if champ_exists and champ_passed:
            status = "RETAINED"
            reason = f"Challenger failed safety gates. Active Champion (Score: {champ_metrics['score']:.2f}) retained."
        else:
            status = "EXCLUDED"
            reason = f"Both Champion and Challenger failed safety gates (Champ: {champ_metrics['score']:.2f}, Chal: {chal_metrics['score']:.2f})."
            # Exclude symbol
            add_to_exclusion_list(sym)
            if os.path.exists(json_path):
                bak_path = json_path + ".bak"
                try:
                    if os.path.exists(bak_path):
                        os.remove(bak_path)
                    os.rename(json_path, bak_path)
                except Exception as e:
                    print(f"[{sym}] Error backing up config: {e}")

    return {
        "symbol": sym,
        "success": True,
        "status": status,
        "reason": reason,
        "champ_trades": champ_metrics["trades"],
        "champ_pnl": champ_metrics["avg_pnl"],
        "champ_sl": champ_metrics["sl_hits"],
        "champ_score": champ_metrics["score"],
        "chal_trades": chal_metrics["trades"],
        "chal_pnl": chal_metrics["avg_pnl"],
        "chal_sl": chal_metrics["sl_hits"],
        "chal_score": chal_metrics["score"],
    }

def main():
    # Setup argparse
    parser = argparse.ArgumentParser(description="Weekly BWO Sync & Verification System")
    parser.add_argument("--symbol", type=str, help="Sync/evaluate a single symbol")
    parser.add_argument("--watchlist", type=str, help="Tuning targets in watchlist (default: all configured symbols)")
    args = parser.parse_args()

    # Find symbols to evaluate
    if args.symbol:
        symbols = [args.symbol.upper()]
        print(f"Weekly Sync: Evaluating single symbol {symbols[0]}...")
    elif args.watchlist:
        symbols = get_watchlist_symbols(args.watchlist)
        print(f"Weekly Sync: Loaded {len(symbols)} symbols from watchlist '{args.watchlist}'.")
    else:
        # Default: Detect all symbols currently configured with a .json (or .json.bak) file in the bw_configs directory
        symbols = []
        if os.path.exists(BW_CONFIGS_DIR):
            for file in os.listdir(BW_CONFIGS_DIR):
                if file.endswith(".json") or file.endswith(".json.bak"):
                    sym = file.split(".")[0].upper()
                    if sym not in symbols:
                        symbols.append(sym)
        
        # Also union with NIFTY 50 to see if we can find new symbols
        try:
            n50_symbols = get_watchlist_symbols("NIFTY 50")
            for sym in n50_symbols:
                if sym not in symbols:
                    symbols.append(sym)
        except Exception:
            pass
            
        print(f"Weekly Sync: Detected {len(symbols)} symbols from bw_configs/ & NIFTY 50 watchlist.")

    if not symbols:
        print("No symbols found to optimize. Exit.")
        return

    os.makedirs(BW_CONFIGS_DIR, exist_ok=True)
    results = []
    updated_any = False

    print(f"Starting parallel Champion-Challenger validation across {len(symbols)} symbols...\n")
    
    with ProcessPoolExecutor() as executor:
        futures = {executor.submit(evaluate_and_sync_symbol, sym): sym for sym in symbols}
        
        for idx, fut in enumerate(as_completed(futures)):
            sym = futures[fut]
            try:
                res = fut.result()
                results.append(res)
                if not res["success"]:
                    print(f"[{idx+1}/{len(symbols)}] Failed {sym}: {res['error']}")
                else:
                    status = res["status"]
                    reason = res["reason"]
                    print(f"[{idx+1}/{len(symbols)}] Evaluated {sym:<15} | Status: {status:<10} | Reason: {reason}")
                    if status in ["PROMOTED", "EXCLUDED"]:
                        updated_any = True
            except Exception as e:
                print(f"[{idx+1}/{len(symbols)}] Future error for {sym}: {e}")

    # Output formatted summary table
    print("\n" + "="*80)
    print(f"{'WEEKLY BWO SYNC SUMMARY':^80}")
    print("="*80)
    print(f"{'Symbol':<12} | {'Status':<10} | {'Champ Score':<12} | {'Chal Score':<12} | {'Trades (Ch)':<12} | {'Avg P&L (Ch)':<12}")
    print("-"*80)
    
    for r in sorted(results, key=lambda x: x["symbol"]):
        if not r["success"]:
            continue
        champ_score_str = f"{r['champ_score']:.2f}" if r["champ_score"] > -9000 else "N/A"
        chal_score_str = f"{r['chal_score']:.2f}" if r["chal_score"] > -9000 else "N/A"
        trades_str = f"{r['chal_trades']}" if r["chal_score"] > -9000 else "N/A"
        pnl_str = f"{r['chal_pnl']:.2f}%" if r["chal_score"] > -9000 else "N/A"
        
        print(f"{r['symbol']:<12} | {r['status']:<10} | {champ_score_str:<12} | {chal_score_str:<12} | {trades_str:<12} | {pnl_str:<12}")
        
    print("="*80)

    # Flush Redis cache if any promotions or exclusions occurred
    if updated_any:
        print("\nChanges detected! Flushing the engine cache to ensure all live/screener calculations use updated configs...")
        os.system("python scripts/flush_cache.py --all")
    else:
        print("\nNo changes made. Cache is clean.")

if __name__ == "__main__":
    main()
