#!/usr/bin/env python3
"""
Debug script for CTS Accel Cross entry scoring telemetry.

Usage:
    python scripts/debug_cts_accel_scoring.py --symbol BEL --date 2025-12-22
"""

import argparse
import sys
import os
import pandas as pd

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.config import CtsAccelCrossEntryConfig
from src.trading.signals.savgol_cts.entries.cts_accel_cross import check_cts_accel_cross

def main():
    parser = argparse.ArgumentParser(description="Debug CTS Accel Cross Scoring")
    parser.add_argument("--symbol", type=str, required=True, help="Stock symbol (e.g., BEL)")
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
    if idx < 20:
        print("Error: Not enough history (need at least 20 bars).")
        return

    # Convert ledger to list of dicts as expected by the signal logic
    records = result.ledger.to_dict('records')
    row = records[idx]
    prev_row = records[idx - 1]

    # Configure the entry check with telemetry enabled
    cfg = CtsAccelCrossEntryConfig()
    cfg.telemetry_enabled = True

    print(f"\nEvaluating CTS Accel Cross Entry for {symbol} on {target_date}...")
    print("-" * 60)
    
    # Call the logic
    passed, intensity, meta = check_cts_accel_cross(
        row=row,
        prev_row=prev_row,
        cfg=cfg,
        records=records,
        idx=idx
    )

    print("\n[FINAL RESULT]")
    print(f"Passed Gates: {passed}")
    if not passed:
        print(f"Failure Reason: {meta.get('reason', 'Unknown')}")
    else:
        print(f"Intensity: {intensity}")
        print(f"Meta (Sanitized): {{k: v for k, v in meta.items() if k != 'tracker'}}")

if __name__ == "__main__":
    main()
