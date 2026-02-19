from src.analysis.smart_money import find_optimal_trend_anchor, get_stock_history
import pandas as pd
import json

def check_geship():
    symbol = "GESHIP"
    print(f"--- Checking {symbol} ---")
    
    # 1. Run Algorithm
    anchor = find_optimal_trend_anchor(symbol)
    print("\nAlgorithm Anchor Result:")
    print(json.dumps(anchor, indent=2))
    
    # 2. Check Database Range
    history = get_stock_history(symbol)
    if not history.empty:
        print(f"\nDatabase Range for {symbol}:")
        print(f"Start: {history['record_date'].min()}")
        print(f"End:   {history['record_date'].max()}")
        print(f"Total Rows: {len(history)}")
        
        # 3. Last 5 entries
        print("\nLast 5 Records:")
        print(history[['record_date', 'price_close', 'volume_total', 'delivery_qty']].tail())
    else:
        print("\nNo data found in database for GESHIP.")

if __name__ == "__main__":
    check_geship()
