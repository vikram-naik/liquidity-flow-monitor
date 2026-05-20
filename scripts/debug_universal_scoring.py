#!/usr/bin/env python3
"""
Debug script for Universal Cross entry scoring telemetry.

Usage:
    python scripts/debug_universal_scoring.py --symbol ADANIPORTS --date 2026-03-24
"""

import argparse
import sys
import os
import pandas as pd

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.entries.universal_cross import entry_universal_cross


def main():
    parser = argparse.ArgumentParser(description="Debug Universal Cross Scoring")
    parser.add_argument("--symbol", type=str, required=True, help="Stock symbol (e.g., ADANIPORTS)")
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
    if idx < 1:
        print("Error: Not enough history.")
        return

    # Convert ledger to list of dicts as expected by the signal logic
    records = result.ledger.to_dict('records')
    row = records[idx]
    prev_row = records[idx - 1]

    # Configure the entry check
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.min_ml_score = 85.0

    print(f"\nEvaluating Universal Cross Entry for {symbol} on {target_date}...")
    print("-" * 60)
    
    # Call the logic
    passed, intensity, meta = entry_universal_cross(
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
        print(f"Meta: {meta}")
    else:
        print(f"Intensity: {intensity}")
        print(f"Meta: {meta}")

if __name__ == "__main__":
    main()
