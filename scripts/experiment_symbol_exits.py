#!/usr/bin/env python3
import sys
import os
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from tabulate import tabulate
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade, SignalFactory
from src.trading.signals.savgol_cts.symbol_configs import get_symbol_entry_config, get_symbol_exit_config
from scripts.test_reclaim_logic import get_watchlist_symbols
from scripts.test_expert_v5 import simulate_trades_expert_v5

# Grid parameters to search per symbol on Train data
TRIGGER_OPTIONS = [6.0, 8.0, 10.0, 12.0]
UP_ATR_OPTIONS = [2.25, 2.5, 2.75, 3.0, 3.25]
NORM_ATR_OPTIONS = [1.75, 2.0, 2.25]

def optimize_single_symbol(sym):
    """Finds the best exit parameters for a single symbol on Train period (2019-2023)."""
    try:
        engine = DivergenceEngine(sym, start_date=None, end_date=None)
        result = engine.run()
        ledger = result.ledger
        
        entry_cfg = get_symbol_entry_config(sym)
        exit_cfg = get_symbol_exit_config(sym)
        signal = SignalFactory.get_signal("savgol_cts")
        
        best_pnl = -999.0
        best_pf = 0.0
        best_params = {
            "peak_pnl_trigger": 10.0,
            "uptrend_atr_mult": 3.0,
            "normal_atr_mult": 2.0
        }
        
        # Grid search
        for trig in TRIGGER_OPTIONS:
            for up_atr in UP_ATR_OPTIONS:
                for norm_atr in NORM_ATR_OPTIONS:
                    p = {
                        "peak_pnl_trigger": trig,
                        "uptrend_atr_mult": up_atr,
                        "normal_atr_mult": norm_atr,
                        "uptrend_cwc_min": 0.10,
                        "uptrend_cwc_slope_min": -0.06,
                        "normal_cwc_min": 0.25,
                        "normal_cwc_slope_min": -0.04,
                        "normal_psz_v_min": -0.2,
                        "overextended_rp_threshold": 0.90,
                        "uptrend_low_break_buffer_atr": 0.25 if up_atr >= 2.75 else 0.20,
                        "normal_rp_reversion": 0.70
                    }
                    
                    trades = simulate_trades_expert_v5(sym, ledger, entry_cfg, exit_cfg, signal, p)
                    train_trades = [t for t in trades if "2019-01-01" <= t["entry_date"] <= "2023-12-31"]
                    
                    if not train_trades:
                        continue
                        
                    # Calculate metrics
                    df = pd.DataFrame(train_trades)
                    avg_pnl = df["pnl_pct"].mean()
                    
                    gross_win = sum(t["pnl_pct"] for t in train_trades if t["pnl_pct"] > 0)
                    gross_loss = abs(sum(t["pnl_pct"] for t in train_trades if t["pnl_pct"] <= 0))
                    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
                    
                    # Selection rule: Maximize Avg PnL but ensure we have at least 1 trade and PF > 1.0
                    # If PF is inf or high, we favor Avg PnL
                    score = avg_pnl
                    # We penalize configurations with very high drawdowns or negative PnL
                    if profit_factor < 1.0:
                        score -= 5.0
                        
                    if score > best_pnl:
                        best_pnl = score
                        best_pf = profit_factor
                        best_params = {
                            "peak_pnl_trigger": trig,
                            "uptrend_atr_mult": up_atr,
                            "normal_atr_mult": norm_atr,
                            "uptrend_low_break_buffer_atr": 0.25 if up_atr >= 2.75 else 0.20
                        }
                        
        return sym, best_params, best_pnl, best_pf
    except Exception as e:
        return sym, None, 0.0, 0.0

def evaluate_portfolio(symbols, start_date, end_date, symbol_params_map, default_params):
    signal = SignalFactory.get_signal("savgol_cts")
    all_trades = []
    
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            result = engine.run()
            
            entry_cfg = get_symbol_entry_config(sym)
            exit_cfg = get_symbol_exit_config(sym)
            
            # Retrieve parameters (symbol-specific or default)
            p_custom = symbol_params_map.get(sym)
            if p_custom:
                p = {
                    "peak_pnl_trigger": p_custom["peak_pnl_trigger"],
                    "uptrend_atr_mult": p_custom["uptrend_atr_mult"],
                    "normal_atr_mult": p_custom["normal_atr_mult"],
                    "uptrend_cwc_min": 0.10,
                    "uptrend_cwc_slope_min": -0.06,
                    "normal_cwc_min": 0.25,
                    "normal_cwc_slope_min": -0.04,
                    "normal_psz_v_min": -0.2,
                    "overextended_rp_threshold": 0.90,
                    "uptrend_low_break_buffer_atr": p_custom.get("uptrend_low_break_buffer_atr", 0.20),
                    "normal_rp_reversion": 0.70
                }
            else:
                p = default_params
                
            trades = simulate_trades_expert_v5(sym, result.ledger, entry_cfg, exit_cfg, signal, p)
            period_trades = [t for t in trades if start_date <= t["entry_date"] <= end_date]
            all_trades.extend(period_trades)
        except Exception:
            pass
            
    if not all_trades:
        return {}
        
    df = pd.DataFrame(all_trades)
    total = len(df)
    winners = (df["pnl_pct"] > 0).sum()
    win_rate = winners / total * 100
    avg_pnl = df["pnl_pct"].mean()
    median_pnl = df["pnl_pct"].median()
    avg_left = (df["mfe_pct"] - df["pnl_pct"]).mean()
    
    gross_win = sum(t["pnl_pct"] for t in all_trades if t["pnl_pct"] > 0)
    gross_loss = abs(sum(t["pnl_pct"] for t in all_trades if t["pnl_pct"] <= 0))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
    
    return {
        "trades": total,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "median_pnl": median_pnl,
        "profit_factor": profit_factor,
        "avg_left": avg_left
    }

def main():
    watchlist_name = "NIFTY 50"
    symbols = get_watchlist_symbols(watchlist_name)
    
    print(f"Starting symbol-specific optimization on TRAIN period (2019-2023) for {len(symbols)} symbols...")
    
    # Run optimization in parallel to save time
    symbol_params = {}
    with ProcessPoolExecutor() as executor:
        results = list(executor.map(optimize_single_symbol, symbols))
        
    for sym, params, pnl, pf in results:
        if params:
            symbol_params[sym] = params
            print(f"  {sym}: Optimized -> Trigger={params['peak_pnl_trigger']}%, UpATR={params['uptrend_atr_mult']}, NormATR={params['normal_atr_mult']} (Train PnL={pnl:+.2f}%, PF={pf:.2f})")
            
    # Default generic parameters (Expert 5 Default optimized in sweep)
    default_params = {
        "peak_pnl_trigger": 10.0,
        "uptrend_atr_mult": 3.0,
        "normal_atr_mult": 2.0,
        "uptrend_cwc_min": 0.10,
        "uptrend_cwc_slope_min": -0.06,
        "normal_cwc_min": 0.25,
        "normal_cwc_slope_min": -0.04,
        "normal_psz_v_min": -0.2,
        "overextended_rp_threshold": 0.90,
        "uptrend_low_break_buffer_atr": 0.30,
        "normal_rp_reversion": 0.70
    }
    
    # Run backtests
    print("\nRunning Backtest 1: Generic Expert 5 (Test Period 2024-2026)...")
    res_generic_test = evaluate_portfolio(symbols, "2024-01-01", "2026-06-06", {}, default_params)
    
    print("Running Backtest 2: Symbol-Specific Optimized Expert 5 (Test Period 2024-2026)...")
    res_symbol_test = evaluate_portfolio(symbols, "2024-01-01", "2026-06-06", symbol_params, default_params)
    
    print("\nRunning Backtest 3: Generic Expert 5 (Train Period 2019-2023)...")
    res_generic_train = evaluate_portfolio(symbols, "2019-01-01", "2023-12-31", {}, default_params)
    
    print("Running Backtest 4: Symbol-Specific Optimized Expert 5 (Train Period 2019-2023)...")
    res_symbol_train = evaluate_portfolio(symbols, "2019-01-01", "2023-12-31", symbol_params, default_params)
    
    # Compare
    print("\n" + "=" * 120)
    print("                                  SYMBOL-SPECIFIC VS GENERIC EXIT COMPARISON")
    print("=" * 120)
    
    headers = ["Period", "Strategy Type", "Trades", "Win Rate%", "Avg PnL%", "Profit Factor", "Avg Left%"]
    rows = [
        [
            "TRAIN Period (2019-2023) - In Sample",
            "Generic Expert 5 (10% Tr, 3.0/2.0)",
            res_generic_train.get("trades"),
            f"{res_generic_train.get('win_rate'):.2f}%",
            f"{res_generic_train.get('avg_pnl'):+.2f}%",
            f"{res_generic_train.get('profit_factor'):.2f}",
            f"{res_generic_train.get('avg_left'):.2f}%"
        ],
        [
            "",
            "Symbol-Specific Optimized",
            res_symbol_train.get("trades"),
            f"{res_symbol_train.get('win_rate'):.2f}%",
            f"{res_symbol_train.get('avg_pnl'):+.2f}%",
            f"{res_symbol_train.get('profit_factor'):.2f}",
            f"{res_symbol_train.get('avg_left'):.2f}%"
        ],
        ["-" * 35, "-" * 30, "-" * 6, "-" * 9, "-" * 8, "-" * 13, "-" * 9],
        [
            "TEST Period (2024-2026) - Out of Sample",
            "Generic Expert 5 (10% Tr, 3.0/2.0)",
            res_generic_test.get("trades"),
            f"{res_generic_test.get('win_rate'):.2f}%",
            f"{res_generic_test.get('avg_pnl'):+.2f}%",
            f"{res_generic_test.get('profit_factor'):.2f}",
            f"{res_generic_test.get('avg_left'):.2f}%"
        ],
        [
            "",
            "Symbol-Specific Optimized",
            res_symbol_test.get("trades"),
            f"{res_symbol_test.get('win_rate'):.2f}%",
            f"{res_symbol_test.get('avg_pnl'):+.2f}%",
            f"{res_symbol_test.get('profit_factor'):.2f}",
            f"{res_symbol_test.get('avg_left'):.2f}%"
        ]
    ]
    
    print(tabulate(rows, headers=headers, tablefmt="simple"))
    print("=" * 120)

if __name__ == "__main__":
    main()
