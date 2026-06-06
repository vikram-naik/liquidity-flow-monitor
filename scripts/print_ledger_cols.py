#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine

def main():
    ticker = "ADANIENT"
    engine = DivergenceEngine(ticker, start_date=None, end_date=None)
    result = engine.run()
    ledger = result.ledger
    print("Available ledger columns:")
    print(list(ledger.columns))

if __name__ == "__main__":
    main()
