#!/usr/bin/env python3
"""
Study: Evaluate various exit-logic modifications to improve the
performance of the SavgolCTS Custom-Bayesian 'Momentum' sub-model.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.enums import EntryTag, ExitReason
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from scripts.walk_forward import simulate_trades, get_watchlist_symbols, compute_profit_factor, compute_expectancy

# Import the original exit and entry functions to allow variations
import src.trading.signals.savgol_cts.signal as sig_mod
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross
from src.trading.signals.savgol_cts.entries.custom_bayesian import entry_custom_bayesian
from src.trading.signals.savgol_cts.state import SavgolCTSExitState

def run_simulation_with_patch(symbols, variant_name, patch_func, patch_entry_func=None):
    """Run simulated trades across Nifty 50 with patched exit/entry functions."""
    # Temporarily monkeypatch the module
    orig_func = sig_mod.exit_universal_cross
    sig_mod.exit_universal_cross = patch_func
    
    orig_cb = sig_mod.entry_custom_bayesian
    if patch_entry_func is not None:
        sig_mod.entry_custom_bayesian = patch_entry_func
    
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    all_trades = []
    for sym in symbols:
        try:
            # We can use cache here for speed since the engine ledger doesn't change
            engine = DivergenceEngine(sym)
            result = engine.run()
            
            from src.trading.signals.savgol_cts import get_symbol_entry_config, get_symbol_exit_config
            sym_entry_cfg = get_symbol_entry_config(sym, entry_cfg)
            sym_exit_cfg = get_symbol_exit_config(sym, exit_cfg)
            
            trades = simulate_trades(sym, result.ledger, sym_entry_cfg, sym_exit_cfg, signal)
            
            # Filter trades to test period (2024-01-01 to 2026-07-14)
            period_trades = [
                t for t in trades 
                if "2024-01-01" <= str(t.entry_date) <= "2026-07-14"
            ]
            all_trades.extend(period_trades)
        except Exception:
            pass

    # Restore original functions
    sig_mod.exit_universal_cross = orig_func
    sig_mod.entry_custom_bayesian = orig_cb

    # Filter to Custom-Bayesian Momentum trades only
    momentum_trades = [
        t for t in all_trades 
        if t.entry_tag == "SavgolCTS Custom-Bayesian" 
        and t.regime_at_entry not in ["downtrend", "notrend"]
    ]
    
    return momentum_trades

def get_stats(trades):
    if not trades:
        return {"trades": 0, "win_rate": 0, "avg_pnl": 0, "profit_factor": 0, "expectancy": 0}
    df = pd.DataFrame([{"pnl": t.pnl_pct} for t in trades])
    winners = (df["pnl"] > 0).sum()
    win_rate = winners / len(trades) * 100
    avg_pnl = df["pnl"].mean()
    profit_factor = compute_profit_factor(trades)
    expectancy = compute_expectancy(trades)
    return {
        "trades": len(trades),
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "profit_factor": profit_factor,
        "expectancy": expectancy
    }

def main():
    parser = argparse.ArgumentParser(description="Study exit/entry variations for Momentum model")
    parser.add_argument("--watchlist", default="NIFTY 50")
    args = parser.parse_args()

    symbols = get_watchlist_symbols(args.watchlist)
    if not symbols:
        print("No symbols found.")
        return

    print(f"Analyzing exit and entry variations for Custom-Bayesian Momentum trades...")
    
    # 1. Baseline
    print("Running Baseline...")
    def patch_baseline(row, prev_row, trade, peak_close, bars_held, state_val, cfg, records, idx):
        return exit_universal_cross(row, prev_row, trade, peak_close, bars_held, state_val, cfg, records, idx)
    
    trades_base = run_simulation_with_patch(symbols, "Baseline", patch_baseline)
    stats_base = get_stats(trades_base)
    print(f"  Base trades: {stats_base['trades']}, Win Rate: {stats_base['win_rate']:.1f}%, Avg P&L: {stats_base['avg_pnl']:.2f}%, PF: {stats_base['profit_factor']:.2f}")

    # 2. Variant A: Disable PRT Crossover Exits for Momentum Trades
    print("Running Variant A (Disable PRT crossover)...")
    def patch_no_prt(row, prev_row, trade, peak_close, bars_held, state_val, cfg, records, idx):
        is_momentum = False
        if trade and trade.entry_tag == "SavgolCTS Custom-Bayesian":
            if trade.regime_at_entry not in ["downtrend", "notrend"]:
                is_momentum = True
        class TempCfg:
            def __init__(self, c):
                self.__dict__.update(c.__dict__)
        temp_cfg = TempCfg(cfg)
        if is_momentum:
            temp_cfg.prt_st_cross_enabled = False
        return exit_universal_cross(row, prev_row, trade, peak_close, bars_held, state_val, temp_cfg, records, idx)
        
    trades_no_prt = run_simulation_with_patch(symbols, "No PRT", patch_no_prt)
    stats_no_prt = get_stats(trades_no_prt)

    # 3. Variant B: Extend CTS ST suppression to 2 bars (bars_held <= 2)
    print("Running Variant B (Extend CTS ST suppression to 2 bars)...")
    def patch_suppress_2(row, prev_row, trade, peak_close, bars_held, state_val, cfg, records, idx):
        reason, next_state = exit_universal_cross(row, prev_row, trade, peak_close, bars_held, state_val, cfg, records, idx)
        is_momentum = False
        if trade and trade.entry_tag == "SavgolCTS Custom-Bayesian":
            if trade.regime_at_entry not in ["downtrend", "notrend"]:
                is_momentum = True
        if reason == ExitReason.ST_CROSS and is_momentum and bars_held <= 2:
            reason = None
        return reason, next_state

    trades_supp_2 = run_simulation_with_patch(symbols, "Suppress 2 Bars", patch_suppress_2)
    stats_supp_2 = get_stats(trades_supp_2)

    # 4. Variant C: Disable Near-Miss Rollover Exits for Momentum Trades
    print("Running Variant C (Disable Near-Miss Rollover exits)...")
    def patch_no_near_miss(row, prev_row, trade, peak_close, bars_held, state_val, cfg, records, idx):
        is_momentum = False
        if trade and trade.entry_tag == "SavgolCTS Custom-Bayesian":
            if trade.regime_at_entry not in ["downtrend", "notrend"]:
                is_momentum = True
        class TempCfg:
            def __init__(self, c):
                self.__dict__.update(c.__dict__)
        temp_cfg = TempCfg(cfg)
        if is_momentum:
            temp_cfg.cts_near_miss_exit_enabled = False
        return exit_universal_cross(row, prev_row, trade, peak_close, bars_held, state_val, temp_cfg, records, idx)

    trades_no_nm = run_simulation_with_patch(symbols, "No Near-Miss", patch_no_near_miss)
    stats_no_nm = get_stats(trades_no_nm)

    # 5. Variant D: Disable both PRT Crossover and Near-Miss Rollover
    print("Running Variant D (Disable PRT + Near-Miss)...")
    def patch_no_prt_no_nm(row, prev_row, trade, peak_close, bars_held, state_val, cfg, records, idx):
        is_momentum = False
        if trade and trade.entry_tag == "SavgolCTS Custom-Bayesian":
            if trade.regime_at_entry not in ["downtrend", "notrend"]:
                is_momentum = True
        class TempCfg:
            def __init__(self, c):
                self.__dict__.update(c.__dict__)
        temp_cfg = TempCfg(cfg)
        if is_momentum:
            temp_cfg.prt_st_cross_enabled = False
            temp_cfg.cts_near_miss_exit_enabled = False
        return exit_universal_cross(row, prev_row, trade, peak_close, bars_held, state_val, temp_cfg, records, idx)

    trades_combo = run_simulation_with_patch(symbols, "No PRT + No Near-Miss", patch_no_prt_no_nm)
    stats_combo = get_stats(trades_combo)

    # 6. Variant E: Shift threshold +0.2
    print("Running Variant E (Threshold Shift +0.2)...")
    def patch_entry_02(row, prev_row, cfg, records, idx):
        cb = getattr(cfg, "custom_bayesian", None)
        if cb:
            if hasattr(cb, "momentum") and cb.momentum is not None:
                orig_t = cb.momentum.score_threshold
                cb.momentum.score_threshold = orig_t + 0.2
                try:
                    return entry_custom_bayesian(row, prev_row, cfg, records, idx)
                finally:
                    cb.momentum.score_threshold = orig_t
            else:
                orig_t = cb.score_threshold
                cb.score_threshold = orig_t + 0.2
                try:
                    return entry_custom_bayesian(row, prev_row, cfg, records, idx)
                finally:
                    cb.score_threshold = orig_t
        return entry_custom_bayesian(row, prev_row, cfg, records, idx)

    trades_t02 = run_simulation_with_patch(symbols, "Thresh +0.2", patch_baseline, patch_entry_02)
    stats_t02 = get_stats(trades_t02)

    # 7. Variant F: Shift threshold +0.5
    print("Running Variant F (Threshold Shift +0.5)...")
    def patch_entry_05(row, prev_row, cfg, records, idx):
        cb = getattr(cfg, "custom_bayesian", None)
        if cb:
            if hasattr(cb, "momentum") and cb.momentum is not None:
                orig_t = cb.momentum.score_threshold
                cb.momentum.score_threshold = orig_t + 0.5
                try:
                    return entry_custom_bayesian(row, prev_row, cfg, records, idx)
                finally:
                    cb.momentum.score_threshold = orig_t
            else:
                orig_t = cb.score_threshold
                cb.score_threshold = orig_t + 0.5
                try:
                    return entry_custom_bayesian(row, prev_row, cfg, records, idx)
                finally:
                    cb.score_threshold = orig_t
        return entry_custom_bayesian(row, prev_row, cfg, records, idx)

    trades_t05 = run_simulation_with_patch(symbols, "Thresh +0.5", patch_baseline, patch_entry_05)
    stats_t05 = get_stats(trades_t05)

    # 8. Variant G: Shift threshold +1.0
    print("Running Variant G (Threshold Shift +1.0)...")
    def patch_entry_10(row, prev_row, cfg, records, idx):
        cb = getattr(cfg, "custom_bayesian", None)
        if cb:
            if hasattr(cb, "momentum") and cb.momentum is not None:
                orig_t = cb.momentum.score_threshold
                cb.momentum.score_threshold = orig_t + 1.0
                try:
                    return entry_custom_bayesian(row, prev_row, cfg, records, idx)
                finally:
                    cb.momentum.score_threshold = orig_t
            else:
                orig_t = cb.score_threshold
                cb.score_threshold = orig_t + 1.0
                try:
                    return entry_custom_bayesian(row, prev_row, cfg, records, idx)
                finally:
                    cb.score_threshold = orig_t
        return entry_custom_bayesian(row, prev_row, cfg, records, idx)

    trades_t10 = run_simulation_with_patch(symbols, "Thresh +1.0", patch_baseline, patch_entry_10)
    stats_t10 = get_stats(trades_t10)

    # 9. Variant H: Shift threshold +1.5
    print("Running Variant H (Threshold Shift +1.5)...")
    def patch_entry_15(row, prev_row, cfg, records, idx):
        cb = getattr(cfg, "custom_bayesian", None)
        if cb:
            if hasattr(cb, "momentum") and cb.momentum is not None:
                orig_t = cb.momentum.score_threshold
                cb.momentum.score_threshold = orig_t + 1.5
                try:
                    return entry_custom_bayesian(row, prev_row, cfg, records, idx)
                finally:
                    cb.momentum.score_threshold = orig_t
            else:
                orig_t = cb.score_threshold
                cb.score_threshold = orig_t + 1.5
                try:
                    return entry_custom_bayesian(row, prev_row, cfg, records, idx)
                finally:
                    cb.score_threshold = orig_t
        return entry_custom_bayesian(row, prev_row, cfg, records, idx)

    trades_t15 = run_simulation_with_patch(symbols, "Thresh +1.5", patch_baseline, patch_entry_15)
    stats_t15 = get_stats(trades_t15)

    # Format output comparison table
    report = f"""========================================================================
  MOMENTUM SUB-MODEL IMPROVEMENT STUDY REPORT
  Watchlist: {args.watchlist} | 2024-01-01 to 2026-07-14
========================================================================

  Exit/Entry Logic Variant            Trades    Win Rate    Avg P&L%    Profit Factor
  ----------------------------------  --------  ----------  ----------  -------------
  Baseline (Current)                       {stats_base['trades']:>3}       {stats_base['win_rate']:>5.1f}%       {stats_base['avg_pnl']:>+7.2f}%         {stats_base['profit_factor']:>5.2f}
  Variant A (Disable PRT Cross)            {stats_no_prt['trades']:>3}       {stats_no_prt['win_rate']:>5.1f}%       {stats_no_prt['avg_pnl']:>+7.2f}%         {stats_no_prt['profit_factor']:>5.2f}
  Variant B (Suppress CTS ST <= 2b)        {stats_supp_2['trades']:>3}       {stats_supp_2['win_rate']:>5.1f}%       {stats_supp_2['avg_pnl']:>+7.2f}%         {stats_supp_2['profit_factor']:>5.2f}
  Variant C (Disable Near-Miss)            {stats_no_nm['trades']:>3}       {stats_no_nm['win_rate']:>5.1f}%       {stats_no_nm['avg_pnl']:>+7.2f}%         {stats_no_nm['profit_factor']:>5.2f}
  Variant D (Disable PRT + Near-Miss)      {stats_combo['trades']:>3}       {stats_combo['win_rate']:>5.1f}%       {stats_combo['avg_pnl']:>+7.2f}%         {stats_combo['profit_factor']:>5.2f}
  Variant E (Threshold Shift +0.2)         {stats_t02['trades']:>3}       {stats_t02['win_rate']:>5.1f}%       {stats_t02['avg_pnl']:>+7.2f}%         {stats_t02['profit_factor']:>5.2f}
  Variant F (Threshold Shift +0.5)         {stats_t05['trades']:>3}       {stats_t05['win_rate']:>5.1f}%       {stats_t05['avg_pnl']:>+7.2f}%         {stats_t05['profit_factor']:>5.2f}
  Variant G (Threshold Shift +1.0)         {stats_t10['trades']:>3}       {stats_t10['win_rate']:>5.1f}%       {stats_t10['avg_pnl']:>+7.2f}%         {stats_t10['profit_factor']:>5.2f}
  Variant H (Threshold Shift +1.5)         {stats_t15['trades']:>3}       {stats_t15['win_rate']:>5.1f}%       {stats_t15['avg_pnl']:>+7.2f}%         {stats_t15['profit_factor']:>5.2f}

========================================================================
"""
    print(report)
    
    # Save report
    out_path = Path(__file__).resolve().parent.parent / "output" / "momentum_improvement_study.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        f.write(report)
    print(f"Report saved to {out_path}")

if __name__ == "__main__":
    main()
