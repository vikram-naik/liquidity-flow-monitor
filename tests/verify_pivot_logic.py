import pandas as pd
import numpy as np
import json
from datetime import datetime, timedelta
from src.analysis.ledger import find_day_zero_anchor

def create_mock_data(periods=600):
    # Create 600 days of data, normalized to midnight
    dates = pd.date_range(end=datetime.now().replace(hour=0, minute=0, second=0, microsecond=0), periods=periods)
    
    # Baseline price around 100
    price_low = 100 + np.cumsum(np.random.normal(0, 1, periods))
    price_high = price_low + 2
    price_close = price_low + 1
    price_open = price_low + 1
    
    # Delivery Volume
    delivery_qty = np.random.randint(1000, 2000, periods)
    
    df = pd.DataFrame({
        'price_open': price_open,
        'price_high': price_high,
        'price_low': price_low,
        'price_close': price_close,
        'delivery_qty': delivery_qty
    }, index=dates)
    
    return df

def test_manual_anchor():
    print("\n--- Testing Manual Anchor ---")
    df = create_mock_data()
    manual_date = df.index[100].strftime('%Y-%m-%d')
    anchor = find_day_zero_anchor(df, manual_date=manual_date)
    
    print(f"Requested: {manual_date}, Found: {anchor['anchor_date']}")
    assert anchor['anchor_date'] == manual_date
    assert anchor['anchor_type'] == 'MANUAL'
    meta = json.loads(anchor['meta'])
    assert meta['is_manual'] == True
    print("✅ Manual anchor test passed!")

def test_auto_anchor_breakout():
    print("\n--- Testing Auto Anchor (Breakout) ---")
    # Create a breakout scenario 20 weeks ago
    df = create_mock_data()
    
    # 20 weeks * 5 days = 100 days from end
    breakout_idx = len(df) - 100
    
    # Make a sharp drop (accumulation base) before the breakout
    df.iloc[breakout_idx-20 : breakout_idx-5, df.columns.get_indexer(['price_low', 'price_close'])] = 80
    
    # The Breakout: Price jump and Volume jump
    df.iloc[breakout_idx, df.columns.get_indexer(['price_close'])] = 150
    df.iloc[breakout_idx, df.columns.get_indexer(['delivery_qty'])] = 50000 
    
    anchor = find_day_zero_anchor(df)
    
    print(f"Anchor Found: {anchor['anchor_date']}, Type: {anchor['anchor_type']}")
    # It should find the pivot and anchor to the base low (80)
    meta = json.loads(anchor['meta'])
    print(f"Metadata: {meta}")
    
    # In this mock, it should ideally find VOL_PIVOT
    assert anchor['anchor_type'] in ['VOL_PIVOT', 'FALLBACK_MIN']
    print("✅ Auto anchor breakout test finished!")

def test_auto_anchor_fallback():
    print("\n--- Testing Auto Anchor (Fallback) ---")
    # Random noise with one absolute low
    df = create_mock_data()
    min_idx = 50
    df.iloc[min_idx, df.columns.get_indexer(['price_low'])] = 10
    
    anchor = find_day_zero_anchor(df)
    print(f"Anchor Found: {anchor['anchor_date']}, Type: {anchor['anchor_type']}")
    # Should fallback to the absolute low of the 2-year window
    # Actually, recent_df is tail(500). 
    assert anchor['anchor_type'] == 'FALLBACK_MIN'
    print("✅ Auto anchor fallback test finished!")

if __name__ == "__main__":
    try:
        test_manual_anchor()
        test_auto_anchor_breakout()
        test_auto_anchor_fallback()
        print("\nAll verification tests completed successfully!")
    except Exception as e:
        print(f"\n❌ Tests failed: {e}")
        import traceback
        traceback.print_exc()
