#!/usr/bin/env python3
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.symbol_configs import get_symbol_entry_config, get_symbol_exit_config
from scripts.test_expert_v5 import simulate_trades_expert_v5

def main():
    ticker = "LT"
    start_date, end_date = "2024-01-01", "2026-06-06"
    
    engine = DivergenceEngine(ticker, start_date=None, end_date=None)
    result = engine.run()
    
    entry_cfg = get_symbol_entry_config(ticker)
    exit_cfg = get_symbol_exit_config(ticker)
    signal = SignalFactory.get_signal("savgol_cts")
    
    params_base = {
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
    
    params_exp5 = {
        "peak_pnl_trigger": 6.0,
        "uptrend_atr_mult": 2.5,
        "normal_atr_mult": 2.0,
        "uptrend_cwc_min": 0.10,
        "uptrend_cwc_slope_min": -0.06,
        "normal_cwc_min": 0.25,
        "normal_cwc_slope_min": -0.04,
        "normal_psz_v_min": -0.2,
        "overextended_rp_threshold": 0.90,
        "uptrend_low_break_buffer_atr": 0.20,
        "normal_rp_reversion": 0.70
    }
    
    trades_base = simulate_trades_expert_v5(ticker, result.ledger, entry_cfg, exit_cfg, signal, params_base)
    trades_base = [t for t in trades_base if start_date <= t["entry_date"] <= end_date]
    
    trades_exp5 = simulate_trades_expert_v5(ticker, result.ledger, entry_cfg, exit_cfg, signal, params_exp5)
    trades_exp5 = [t for t in trades_exp5 if start_date <= t["entry_date"] <= end_date]
    
    print("LT Trades under Baseline Refined:")
    print(pd.DataFrame(trades_base).to_string(index=False))
    print("\nLT Trades under Expert 5 Default:")
    print(pd.DataFrame(trades_exp5).to_string(index=False))

if __name__ == "__main__":
    main()
