#!/usr/bin/env python
import argparse
import os
import sys
import json
import numpy as np
from pathlib import Path

# Add root folder to sys.path
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSExitConfig
from scripts.train_symbol_weights import label_candidate_bars_sim, simulate_single_trade
from scripts.walk_forward import get_watchlist_symbols

def suggest_params_for_symbol(sym):
    print(f"\n=== Analyzing Symbol: {sym} ===")
    try:
        # Load ledger data
        engine = DivergenceEngine(sym, start_date=None, end_date=None)
        res = engine.run()
        ledger = res.ledger
    except Exception as e:
        print(f"Error loading ledger for {sym}: {e}")
        return None

    signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()

    # Calculate volatility (average ATR percent)
    close_vals = ledger["close"].values
    atr_vals = ledger["atr_20"].values
    valid_atr_pct = [atr / close * 100.0 for close, atr in zip(close_vals, atr_vals) if not np.isnan(close) and not np.isnan(atr) and close > 0]
    avg_atr_pct = float(np.mean(valid_atr_pct)) if valid_atr_pct else 2.0
    print(f"Average ATR % (Volatility): {avg_atr_pct:.2f}%")

    # Perform a relaxed scan to capture baseline distribution of entries
    # Using high sl_mae_threshold (15.0%) and success_mult (1.0) to see the full potential of trades
    candidates, labels = label_candidate_bars_sim(
        sym, ledger, signal, exit_cfg, success_mult=1.0, dd_limit=10.0, sl_mae_threshold=15.0
    )

    candidate_count = len(candidates)
    print(f"Baseline Candidate Entries found: {candidate_count}")

    if candidate_count == 0:
        print("Warning: No baseline candidate entries found. Using global defaults.")
        return {
            "MIN_TRADES": 3,
            "MAX_SL_RATIO": 0.25,
            "TARGET_PNL": 5.0,
            "SL_MAE_THRESHOLD": 8.0,
            "NUM_BINS_CHOICES": [3, 4],
            "SUCCESS_MULT_CHOICES": [1.2, 1.5, 1.8],
            "DD_LIMIT_CHOICES": [5.0, 6.0, 7.0]
        }

    # Simulate all candidate trades to collect PnL and drawdown statistics
    pnls = []
    maes = []
    winning_maes = []

    for idx in candidates:
        pnl, mae = simulate_single_trade(sym, ledger, idx, signal, exit_cfg)
        pnls.append(pnl)
        maes.append(mae)
        if pnl > 0.0:
            winning_maes.append(mae)

    # 1. Trade Frequency Recommendation
    if candidate_count >= 80:
        min_trades = 8
    elif candidate_count >= 40:
        min_trades = 5
    else:
        min_trades = 3

    # 2. Stop-Loss MAE Threshold Recommendation
    # Find the 90th percentile of MAE for successful trades to avoid cutting winners prematurely
    if winning_maes:
        p90_win_mae = float(np.percentile(winning_maes, 90))
    else:
        p90_win_mae = 2.5 * avg_atr_pct

    # Bind SL threshold to a healthy range based on volatility
    suggested_sl_mae = max(5.0, min(10.0, max(2.8 * avg_atr_pct, p90_win_mae)))
    suggested_sl_mae = round(suggested_sl_mae, 1)

    # 3. Maximum SL Ratio Recommendation
    # If standard failure rate is high, we can allow slightly more leeway, but normally 20% or 25% is best
    if avg_atr_pct > 3.5:
        max_sl_ratio = 0.25
    else:
        max_sl_ratio = 0.20

    # 4. Target P&L Suggestion
    # Base it on 75th percentile of successful trade PnLs
    pos_pnls = [p for p in pnls if p > 0.0]
    if pos_pnls:
        p75_win_pnl = float(np.percentile(pos_pnls, 75))
        suggested_target_pnl = max(2.0, min(6.0, p75_win_pnl * 0.75))
    else:
        suggested_target_pnl = 3.0
    suggested_target_pnl = round(suggested_target_pnl, 1)

    # 5. Hyperparameter Choices
    # Volatility scales the drawdown limit choices
    base_dd = max(1.5 * avg_atr_pct, 4.0)
    dd_choices = [round(base_dd, 1), round(base_dd + 1.5, 1), round(base_dd + 3.0, 1)]
    # Cap dd choices at 12% max
    dd_choices = [min(x, 12.0) for x in dd_choices]

    if avg_atr_pct > 3.0:
        success_choices = [1.5, 1.8, 2.2]
    else:
        success_choices = [1.2, 1.5, 1.8]

    if candidate_count >= 100:
        bin_choices = [3, 4, 5]
    else:
        bin_choices = [3, 4]

    suggestions = {
        "MIN_TRADES": min_trades,
        "MAX_SL_RATIO": max_sl_ratio,
        "TARGET_PNL": suggested_target_pnl,
        "SL_MAE_THRESHOLD": suggested_sl_mae,
        "NUM_BINS_CHOICES": bin_choices,
        "SUCCESS_MULT_CHOICES": success_choices,
        "DD_LIMIT_CHOICES": dd_choices
    }

    print(f"--- Recommendations for {sym} ---")
    print(f"  MIN_TRADES:           {suggestions['MIN_TRADES']} (Based on candidate count of {candidate_count})")
    print(f"  MAX_SL_RATIO:         {suggestions['MAX_SL_RATIO']:.2f}")
    print(f"  TARGET_PNL:           {suggestions['TARGET_PNL']:.2f}% (Achievable average target P&L)")
    print(f"  SL_MAE_THRESHOLD:     {suggestions['SL_MAE_THRESHOLD']:.2f}% (Stop-loss distance based on volatility & MAE)")
    print(f"  NUM_BINS_CHOICES:     {suggestions['NUM_BINS_CHOICES']}")
    print(f"  SUCCESS_MULT_CHOICES: {suggestions['SUCCESS_MULT_CHOICES']}")
    print(f"  DD_LIMIT_CHOICES:     {suggestions['DD_LIMIT_CHOICES']}%")
    print("--------------------------------")

    return suggestions

def main():
    parser = argparse.ArgumentParser(description="BWO Settings Suggestion System")
    parser.add_argument("--symbol", type=str, help="Recommend parameters for a single symbol")
    parser.add_argument("--watchlist", type=str, help="Recommend parameters for all symbols in a watchlist")
    parser.add_argument("--write", action="store_true", help="Auto-write recommended parameters to bwo_symbol_params.json")
    args = parser.parse_args()

    if not args.symbol and not args.watchlist:
        parser.print_help()
        return

    symbols = []
    if args.symbol:
        symbols = [args.symbol.upper()]
    elif args.watchlist:
        symbols = get_watchlist_symbols(args.watchlist)

    from src.trading.signals.savgol_cts.bwo_settings import BWO_SYMBOL_PARAMS_PATH

    # Load existing overrides
    all_overrides = {}
    if os.path.exists(BWO_SYMBOL_PARAMS_PATH):
        try:
            with open(BWO_SYMBOL_PARAMS_PATH, "r") as f:
                all_overrides = json.load(f)
        except Exception as e:
            print(f"Error reading existing overrides file: {e}")

    updated_any = False
    for sym in symbols:
        suggestions = suggest_params_for_symbol(sym)
        if suggestions and args.write:
            all_overrides[sym] = suggestions
            updated_any = True

    if args.write and updated_any:
        try:
            os.makedirs(os.path.dirname(BWO_SYMBOL_PARAMS_PATH), exist_ok=True)
            with open(BWO_SYMBOL_PARAMS_PATH, "w") as f:
                json.dump(all_overrides, f, indent=4)
            print(f"\nSuccessfully wrote recommended parameters to {BWO_SYMBOL_PARAMS_PATH}!")
        except Exception as e:
            print(f"Error writing to overrides file: {e}")

if __name__ == "__main__":
    main()
