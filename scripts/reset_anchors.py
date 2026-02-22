import pandas as pd
from src.analysis.data import load_stock_list, get_stock_data
from src.analysis.ledger import get_or_create_anchor
import logging
import sys

# Configure logging to show progress in terminal
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("reset_anchors")

def reset_all_anchors():
    """
    Reset all stock anchors using the new Volume-Confirmed Volatility Pivot logic.
    Ensures corporate actions are applied via get_stock_data.
    """
    try:
        symbols = load_stock_list()
        logger.info(f"🚀 Starting mass anchor reset for {len(symbols)} symbols...")
        
        success_count = 0
        error_count = 0
        
        for symbol in symbols:
            try:
                # 1. Fetch data (this applies Corporate Actions and uses Cache if available)
                # We don't use agg_period here because get_or_create_anchor needs daily data for Day Zero precision
                df, _, _ = get_stock_data(symbol, agg_period="daily", lookback_days=0)
                
                if df.empty:
                    logger.warning(f"⚠️ {symbol}: No data found, skipping.")
                    continue
                
                # 1.5 Restore DatetimeIndex for resample() logic
                if 'record_date' in df.columns:
                    df = df.set_index('record_date')
                
                # 2. Force recalculation of the anchor
                # This will call find_day_zero_anchor internally with the new logic
                new_anchor = get_or_create_anchor(symbol, df, force_new=True)
                
                if new_anchor:
                    logger.info(f"✅ {symbol}: Reset to {new_anchor['anchor_date']} ({new_anchor['anchor_type']})")
                    success_count += 1
                else:
                    logger.warning(f"⚠️ {symbol}: Failed to find a valid anchor.")
                    error_count += 1
                    
            except Exception as e:
                logger.error(f"❌ {symbol}: Error during reset - {str(e)}")
                error_count += 1
        
        logger.info(f"✨ Reset Complete! Success: {success_count}, Errors: {error_count}")
        
    except Exception as e:
        logger.critical(f"💥 Fatal error in reset script: {str(e)}")

if __name__ == "__main__":
    reset_all_anchors()
