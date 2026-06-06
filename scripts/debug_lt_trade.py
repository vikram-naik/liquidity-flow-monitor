#!/usr/bin/env python3
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine

def main():
    ticker = "LT"
    entry_date = "2026-01-27"
    
    print(f"Loading data for {ticker}...")
    engine = DivergenceEngine(ticker, start_date=None, end_date=None)
    result = engine.run()
    ledger = result.ledger
    
    # Filter ledger from entry_date onwards
    ledger["date_str"] = ledger["date"].astype(str).str[:10]
    trade_df = ledger[ledger["date_str"] >= entry_date].copy()
    
    print(f"Trade history for {ticker} starting from {entry_date}:")
    cols_to_print = [
        "date_str", "close", "open", "low", "high", "cwvap", "va_high",
        "cwc", "cwc_slope", "psz_v", "range_pos_10", "atr_20", "regime", "cts", "prt"
    ]
    # Ensure all columns exist
    for col in cols_to_print:
        if col not in trade_df.columns:
            trade_df[col] = np.nan
            
    print(trade_df[cols_to_print].head(35).to_string(index=False))

if __name__ == "__main__":
    main()
