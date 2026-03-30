import sqlite3
import pandas as pd
import numpy as np
from src.divergence_engine.engine import DivergenceEngine
from src.database import get_db_connection
import logging
import os
from src.divergence_engine.utils import load_symbol_data

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_nifty_500_symbols():
    """Fetch symbols for NIFTY 500 watchlist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    # NIFTY 500 ID is 13 based on previous check
    cursor.execute("SELECT symbol FROM watchlist_items WHERE watchlist_id = 13")
    symbols = [row[0] for row in cursor.fetchall()]
    conn.close()
    return symbols

def find_cts_slope_bottoms(symbol, start_date=None, end_date=None):
    """Run DivergenceEngine and find local minima of cts_slope when price < cwvap."""
    try:
        # Load data manually and pass to engine to bypass Redis cache for 'cts_slope_trough'
        df_raw = load_symbol_data(symbol, start_date, end_date)
        engine = DivergenceEngine(ticker=symbol, df=df_raw)
        result = engine.run()
        df = result.ledger
        
        if 'cts_slope' not in df.columns or 'cwvap' not in df.columns or 'close' not in df.columns:
            logger.warning(f"[{symbol}] Missing required columns in ledger.")
            return []
            
        # identify troughs (local minima) specifically in weak phases
        # The engine now pre-computes this causally (slope < -0.20 & accel > 0 & regime == downtrend)
        troughs = df[df["cts_slope_trough"] == 1]["cts_slope"].tolist()
        return troughs
    except Exception as e:
        logger.error(f"Error processing {symbol}: {e}")
        return []

def main():
    print("Main started!", flush=True)
    symbols = get_nifty_500_symbols()
    logger.info(f"Found {len(symbols)} symbols in NIFTY 500.")
    
    # Analyze last 1 year (approx 252 bars)
    # If DivergenceEngine loads all data if start_date is None, that's fine too.
    # Let's use a recent window for relevance.
    all_bottoms = []
    
    for i, symbol in enumerate(symbols):
        if i % 50 == 0:
            print(f"Processing symbol {i}/{len(symbols)}: {symbol}", flush=True)
        
        bottoms = find_cts_slope_bottoms(symbol)
        all_bottoms.extend(bottoms)
        
    if not all_bottoms:
        logger.error("No bottom values found.")
        return
        
    # Statistical Analysis
    bottoms_series = pd.Series(all_bottoms)
    stats = {
        "count": len(bottoms_series),
        "mean": bottoms_series.mean(),
        "median": bottoms_series.median(),
        "std": bottoms_series.std(),
        "min": bottoms_series.min(),
        "5%": bottoms_series.quantile(0.05),
        "10%": bottoms_series.quantile(0.10),
        "25%": bottoms_series.quantile(0.25),
        "50%": bottoms_series.quantile(0.50),
        "75%": bottoms_series.quantile(0.75),
    }
    
    logger.info("--- CTS Slope Bottom Analysis Results ---")
    for k, v in stats.items():
        logger.info(f"{k}: {v:.6f}" if isinstance(v, float) else f"{k}: {v}")
        
    # Determine a suggested threshold
    # The 10th or 25th percentile might be a good "bottom threshold" candidate 
    # to catch truly oversold conditions.
    logger.info(f"Suggested Bottom Threshold (10th percentile): {stats['10%']:.6f}")
    logger.info(f"Suggested Bottom Threshold (25th percentile): {stats['25%']:.6f}")

if __name__ == "__main__":
    main()
