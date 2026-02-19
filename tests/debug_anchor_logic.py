from src.analysis.smart_money import find_optimal_trend_anchor, get_stock_history, calculate_smart_money_features
import pandas as pd
import json

def check_stock_anchor(symbol):
    print(f"--- Checking {symbol} ---")
    
    # 1. Run Algorithm
    anchor = find_optimal_trend_anchor(symbol)
    print("\nAlgorithm Anchor Result:")
    print(json.dumps(anchor, indent=2))
    
    # 2. Check Database Range
    history = get_stock_history(symbol)
    if not history.empty:
        df = calculate_smart_money_features(history)
        
        # Look at the data around the anchor date
        anchor_date = anchor['anchor_date']
        # Convert to datetime for comparison
        df['record_date'] = pd.to_datetime(df['record_date'])
        a_dt = pd.to_datetime(anchor_date)
        
        # Get 10 rows before and after
        idx = df[df['record_date'] == a_dt].index
        if not idx.empty:
            start_idx = max(0, idx[0] - 5)
            end_idx = min(len(df), idx[0] + 6)
            print(f"\nData surrounding anchor date ({anchor_date}):")
            cols = ['record_date', 'price_close', 'smart_flow_cum', 'smart_slope']
            print(df.iloc[start_idx:end_idx][cols])
            
            # Check the crossing logic
            row = df.iloc[idx[0]]
            prev_row = df.iloc[idx[0]-1] if idx[0] > 0 else None
            
            if prev_row is not None:
                print(f"\nCrossing check:")
                print(f"Previous Flow: {prev_row['smart_flow_cum']:.2f}")
                print(f"Current Flow:  {row['smart_flow_cum']:.2f}")

if __name__ == "__main__":
    check_stock_anchor("BAJAJ-AUTO")
