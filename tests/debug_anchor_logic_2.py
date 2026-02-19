from src.analysis.smart_money import find_optimal_trend_anchor, get_stock_history, calculate_smart_money_features
import pandas as pd
import json

def check_stock_anchor(symbol):
    print(f"--- Checking {symbol} ---")
    history = get_stock_history(symbol)
    if not history.empty:
        df = calculate_smart_money_features(history)
        print(f"Total Rows: {len(df)}")
        print("\nLast 3 Rows:")
        print(df[['record_date', 'price_close', 'smart_flow_cum']].tail(3))
        
        anchor = find_optimal_trend_anchor(symbol)
        print("\nAlgorithm Anchor Result:")
        print(json.dumps(anchor, indent=2))

if __name__ == "__main__":
    check_stock_anchor("BAJAJ-AUTO")
