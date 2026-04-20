#!/usr/bin/env python3
"""
Debug script for FAS Zero Cross entry scoring telemetry.

Usage:
    python scripts/debug_fas_scoring.py --symbol TRENT --date 2025-04-01
"""

import argparse
import sys
import os
import pandas as pd

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.config import FasZeroCrossEntryConfig
from src.trading.signals.savgol_cts.entries.fas_zero_cross import check_fas_zero_cross

def main():
    parser = argparse.ArgumentParser(description="Debug FAS Zero Cross Scoring")
    parser.add_argument("--symbol", type=str, required=True, help="Stock symbol (e.g., TRENT)")
    parser.add_argument("--date", type=str, required=True, help="Target date (YYYY-MM-DD)")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    target_date = args.date

    print(f"Loading DivergenceEngine for {symbol}...")
    engine = DivergenceEngine(ticker=symbol)
    result = engine.run()
    
    if result.ledger is None or result.ledger.empty:
        print(f"Error: No data returned for {symbol}")
        return

    # Ensure date column is datetime for accurate matching
    result.ledger['date'] = pd.to_datetime(result.ledger['date'])
    target_dt = pd.to_datetime(target_date)
    
    matches = result.ledger[result.ledger['date'] == target_dt]
    if matches.empty:
        print(f"Error: Date {target_date} not found in ledger for {symbol}.")
        print(f"Available date range: {result.ledger['date'].min()} to {result.ledger['date'].max()}")
        return

    idx = matches.index[0]
    if idx < 10:
        print("Error: Not enough history (need at least 10 bars).")
        return

    # Convert ledger to list of dicts as expected by the signal logic
    records = result.ledger.to_dict('records')
    row = records[idx]
    prev_row = records[idx - 1]

    # Configure the entry check with telemetry enabled
    cfg = FasZeroCrossEntryConfig()
    cfg.telemetry_enabled = True

    print(f"\nEvaluating FAS Zero Cross Entry for {symbol} on {target_date}...")
    print("-" * 60)
    
    # Call the logic
    passed, intensity, meta = check_fas_zero_cross(
        row=row,
        prev_row=prev_row,
        cfg=cfg,
        records=records,
        idx=idx
    )

    print("[FINAL RESULT]")
    print(f"Passed Gates: {passed}")
    if not passed:
        print(f"Failure Reason: {meta.get('reason', 'Unknown')}")
    else:
        print(f"Intensity: {intensity}")
        print(f"Meta: {meta}")

if __name__ == "__main__":
    main()
