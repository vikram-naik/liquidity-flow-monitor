#!/usr/bin/env python3
"""
Test script for evaluating the new Integrated State Matrix logic.
This script fetches the data exactly like interactive_analysis.py, 
computes the core divergence engine ledger, and then applies the new logic.
"""

import sys
import argparse
import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate

# Ensure project root is in PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.divergence_engine.engine import DivergenceEngine
from src.divergence_engine.analysis_integrated import apply_integrated_matrix
def main():
    parser = argparse.ArgumentParser(description="Test Integrated State Matrix")
    parser.add_argument("symbol", help="NSE Ticker (e.g., EDELWEISS)")
    parser.add_argument("--days", type=int, default=60, help="Days to output")
    args = parser.parse_args()

    print(f"Running Engine for {args.symbol.upper()}...")
    engine = DivergenceEngine(ticker=args.symbol.upper())
    result = engine.run()
    
    df = result.ledger.copy()
    
    print("Applying Integrated Matrix Mathematics...")
    df = apply_integrated_matrix(df)
    
    print(f"\nLast {args.days} days of Activity:\n")
    
    table_data = []
    for _, row in df.tail(args.days).iterrows():
        table_data.append({
            "Date": str(row['date'])[:10],
            "Integrated State": row.get('integrated_state', 'N/A'),
            "P Z": f"{row.get('price_slope_z', 0.0):+.3f}",
            "P ∠": f"{row.get('price_slope_angle', 0.0):+.3f}",
            "RDV Z": f"{row.get('rdv_slope_z', 0.0):+.3f}",
            "RDV ∠": f"{row.get('rdv_slope_angle', 0.0):+.3f}",
            "Value Zone": row.get('value_zone', 'N/A'),
            "Coh": f"{row.get('coherence', 0.0):.3f}"
        })
        
    print(tabulate(table_data, headers="keys", tablefmt="rounded_grid"))

if __name__ == "__main__":
    main()
