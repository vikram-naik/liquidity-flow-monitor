import sqlite3
import sys
import logging
import argparse
import multiprocessing
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.database import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def warm_symbol(symbol: str) -> bool:
    try:
        # Use lookback=252 to match UI and Screener
        engine = DivergenceEngine(symbol)
        engine.run()
        return True
    except Exception as e:
        logger.warning(f"Failed warming {symbol}: {e}")
    return False

def main():
    parser = argparse.ArgumentParser(description="DivergenceEngine Cache Warmer")
    parser.add_argument("--watchlist", type=str, default="NIFTY 500", help="Watchlist to warm up")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only log errors")
    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.ERROR)
        import os
        sys.stdout = open(os.devnull, 'w')
    
    conn = sqlite3.connect(str(DB_PATH))
    
    # Get watchlist stocks
    row = conn.execute("SELECT id FROM watchlists WHERE name = ?", (args.watchlist,)).fetchone()
    if not row:
        logger.error(f"Watchlist '{args.watchlist}' not found.")
        conn.close()
        sys.exit(1)
        
    symbols = [
        r[0] for r in conn.execute(
            "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY display_order",
            (row[0],)
        ).fetchall()
    ]
    conn.close()

    cores = multiprocessing.cpu_count()
    logger.info(f"Warming cache for {len(symbols)} symbols in '{args.watchlist}' using {cores} workers...")
    
    success_count = 0
    with ProcessPoolExecutor(max_workers=cores) as executor:
        futures = {executor.submit(warm_symbol, sym): sym for sym in symbols}
        
        for i, future in enumerate(as_completed(futures)):
            if i > 0 and i % 50 == 0:
                logger.info(f"Warmed {i}/{len(symbols)} symbols...")
                
            if future.result():
                success_count += 1

    logger.info(f"Cache warmup complete. Successfully warmed {success_count}/{len(symbols)} symbols.")

if __name__ == "__main__":
    main()
