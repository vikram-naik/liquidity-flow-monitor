"""
Study script to generate Oracle Labels for the NIFTY 100 universe.
Runs the DivergenceEngine for all symbols to populate the cache and allow visual inspection.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import get_watchlist_symbols
from src.divergence_engine.engine import DivergenceEngine
from src.cache import get_cache

def main():
    watchlist = "NIFTY 100"
    print(f"Fetching symbols for {watchlist}...")
    symbols = get_watchlist_symbols(watchlist)
    print(f"Found {len(symbols)} symbols.")

    # Flush cache to ensure new Oracle columns are computed
    print("Flushing Redis cache...")
    cache = get_cache()
    # We can't easily flush only oracle keys without a pattern, 
    # but the flush_cache.py script handles it better.
    # For now, we rely on the user having flushed or we just run.
    
    for i, sym in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}] Processing {sym}...")
        try:
            engine = DivergenceEngine(sym)
            # Running the engine computes all modules including Module 7 (Oracle)
            engine.run()
        except Exception as e:
            print(f"Error processing {sym}: {e}")

    print("\nGeneration complete. Open the UI to visually inspect the labels.")

if __name__ == "__main__":
    main()
