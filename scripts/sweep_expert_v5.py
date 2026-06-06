#!/usr/bin/env python3
import sys
import os
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade, SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState
from src.trading.signals.savgol_cts.symbol_configs import get_symbol_entry_config, get_symbol_exit_config
from src.trading.signals.enums import ExitReason, EntryTag
from scripts.test_reclaim_logic import get_watchlist_symbols
from scripts.test_expert_v5 import simulate_trades_expert_v5, evaluate_expert_v5

def main():
    watchlist_name = "NIFTY 50"
    symbols = get_watchlist_symbols(watchlist_name)
    
    # Define parameters to sweep
    # We want to check:
    # - peak_pnl_trigger: 6.0, 8.0, 10.0, 12.0
    # - uptrend_atr_mult: 2.5, 2.75, 3.0, 3.25
    # - normal_atr_mult: 1.75, 2.0, 2.25
    
    sweeps = [
        {"peak_pnl_trigger": 6.0, "uptrend_atr_mult": 2.5, "normal_atr_mult": 2.0},
        {"peak_pnl_trigger": 8.0, "uptrend_atr_mult": 2.5, "normal_atr_mult": 2.0},
        {"peak_pnl_trigger": 8.0, "uptrend_atr_mult": 2.75, "normal_atr_mult": 2.0},
        {"peak_pnl_trigger": 10.0, "uptrend_atr_mult": 2.75, "normal_atr_mult": 2.0},
        {"peak_pnl_trigger": 10.0, "uptrend_atr_mult": 3.0, "normal_atr_mult": 2.0},
        {"peak_pnl_trigger": 12.0, "uptrend_atr_mult": 3.0, "normal_atr_mult": 2.0},
        {"peak_pnl_trigger": 12.0, "uptrend_atr_mult": 3.25, "normal_atr_mult": 2.0},
        # Check dynamic buffer values
        {"peak_pnl_trigger": 8.0, "uptrend_atr_mult": 2.75, "normal_atr_mult": 2.0, "uptrend_low_break_buffer_atr": 0.25},
        {"peak_pnl_trigger": 10.0, "uptrend_atr_mult": 3.0, "normal_atr_mult": 2.0, "uptrend_low_break_buffer_atr": 0.30},
    ]
    
    # Baseline refined parameters (mimics reclaim_prt + 2.0*ATR)
    baseline_params = {
        "peak_pnl_trigger": 4.0,
        "uptrend_atr_mult": 2.0,
        "normal_atr_mult": 2.0,
        "uptrend_cwc_min": -99.0,
        "uptrend_cwc_slope_min": -99.0,
        "normal_cwc_min": -99.0,
        "normal_cwc_slope_min": -99.0,
        "normal_psz_v_min": -99.0,
        "overextended_rp_threshold": 9.0,
        "uptrend_low_break_buffer_atr": 0.0,
        "normal_rp_reversion": -99.0
    }
    
    print("Evaluating Baseline Refined...")
    train_base = evaluate_expert_v5(symbols, "2019-01-01", "2023-12-31", baseline_params)
    test_base = evaluate_expert_v5(symbols, "2024-01-01", "2026-06-06", baseline_params)
    
    results = []
    
    for i, sw in enumerate(sweeps):
        # Fill in other defaults
        p = {
            "peak_pnl_trigger": sw["peak_pnl_trigger"],
            "uptrend_atr_mult": sw["uptrend_atr_mult"],
            "normal_atr_mult": sw["normal_atr_mult"],
            "uptrend_cwc_min": 0.10,
            "uptrend_cwc_slope_min": -0.06,
            "normal_cwc_min": 0.25,
            "normal_cwc_slope_min": -0.04,
            "normal_psz_v_min": -0.2,
            "overextended_rp_threshold": 0.90,
            "uptrend_low_break_buffer_atr": sw.get("uptrend_low_break_buffer_atr", 0.20),
            "normal_rp_reversion": 0.70
        }
        
        desc = f"Trigger={p['peak_pnl_trigger']}%, UpATR={p['uptrend_atr_mult']}, NormATR={p['normal_atr_mult']}, Buff={p['uptrend_low_break_buffer_atr']}"
        print(f"[{i+1}/{len(sweeps)}] Evaluating: {desc}...")
        
        train_res = evaluate_expert_v5(symbols, "2019-01-01", "2023-12-31", p)
        test_res = evaluate_expert_v5(symbols, "2024-01-01", "2026-06-06", p)
        
        results.append({
            "config": desc,
            "train_wr": train_res.get("win_rate", 0),
            "train_avg": train_res.get("avg_pnl", 0),
            "train_pf": train_res.get("profit_factor", 0),
            "train_left": train_res.get("avg_left", 0),
            "test_wr": test_res.get("win_rate", 0),
            "test_avg": test_res.get("avg_pnl", 0),
            "test_pf": test_res.get("profit_factor", 0),
            "test_left": test_res.get("avg_left", 0),
        })
        
    print("\n" + "=" * 145)
    print("                                                 EXPERT 5 PARAMETER SWEEP STUDY")
    print("=" * 145)
    print(f"Baseline:   TRAIN [WR={train_base['win_rate']:.1f}%, PnL={train_base['avg_pnl']:+.2f}%, PF={train_base['profit_factor']:.2f}, Left={train_base['avg_left']:.2f}%] | TEST [WR={test_base['win_rate']:.1f}%, PnL={test_base['avg_pnl']:+.2f}%, PF={test_base['profit_factor']:.2f}, Left={test_base['avg_left']:.2f}%]\n")
    
    headers = ["Config Description", "Train WR", "Train PnL", "Train PF", "Train Left", "Test WR", "Test PnL", "Test PF", "Test Left"]
    rows = []
    for r in results:
        rows.append([
            r["config"],
            f"{r['train_wr']:.2f}%",
            f"{r['train_avg']:+.2f}%",
            f"{r['train_pf']:.2f}",
            f"{r['train_left']:.2f}%",
            f"{r['test_wr']:.2f}%",
            f"{r['test_avg']:+.2f}%",
            f"{r['test_pf']:.2f}",
            f"{r['test_left']:.2f}%"
        ])
        
    print(tabulate(rows, headers=headers, tablefmt="simple"))
    print("=" * 145)

if __name__ == "__main__":
    main()
