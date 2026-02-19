from src.analysis.smart_money import find_optimal_trend_anchor, get_stock_history
import pandas as pd
import json

def check_bajaj():
    symbol = "BAJFINANCE"
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
        
        # Check last flow to see if BULLISH or BEARISH is justified
        from src.analysis.smart_money import calculate_smart_money_features
        df = calculate_smart_money_features(history)
        last_flow = df['smart_flow_cum'].iloc[-1]
        print(f"Latest Cumulative Flow: {last_flow:.2f}")

if __name__ == "__main__":
    check_bajaj()
