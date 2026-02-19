import pandas as pd
import numpy as np
from src.analysis.ledger import calculate_mfm, calculate_dvl, calculate_davwap

def test_ledger_logic():
    # 1. Create artificial data
    dates = pd.date_range(start="2024-01-01", periods=5)
    data = {
        'price_open':  [100, 105, 110, 108, 112],
        'price_high':  [110, 115, 120, 112, 118],
        'price_low':   [90,  100, 105, 100, 110],
        'price_close': [105, 110, 105, 102, 115], # Closest to High/Low varies
        'delivery_qty': [1000, 2000, 1500, 3000, 2500]
    }
    df = pd.DataFrame(data, index=dates)
    
    # 2. Test MFM
    mfm = calculate_mfm(df)
    print("MFM Values:\n", mfm)
    # Day 0: ((105-90) - (110-105)) / (110-90) = (15 - 5) / 20 = 10/20 = 0.5 (Correct)
    assert mfm[0] == 0.5
    
    # 3. Test DVL (Anchored at Day 1)
    anchor_date = dates[1]
    dvl = calculate_dvl(df, anchor_date)
    print("\nAnchored DVL (at Day 1):\n", dvl)
    # Day 0 should be 0
    # Day 1: MFM = ((110-100) - (115-110))/(115-100) = (10-5)/15 = 5/15 = 0.333
    # Day 1 Flow = 0.333 * 2000 = 666.66
    assert dvl[0] == 0
    assert np.isclose(dvl[1], (calculate_mfm(df.iloc[1:2])[0] * 2000))
    
    # 4. Test DAVWAP (Anchored at Day 1)
    davwap = calculate_davwap(df, anchor_date)
    print("\nAnchored DAVWAP (at Day 1):\n", davwap)
    # Day 0 should be NaN
    assert pd.isna(davwap[0])
    
    # Day 1: Typical Price = (115+100+110)/3 = 325/3 = 108.33
    # DAVWAP = 108.33
    assert np.isclose(davwap[1], (df.iloc[1]['price_high'] + df.iloc[1]['price_low'] + df.iloc[1]['price_close'])/3)

    print("\n✅ Ledger logic tests passed!")

if __name__ == "__main__":
    test_ledger_logic()
