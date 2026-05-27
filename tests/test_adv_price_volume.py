import pytest
import numpy as np
import pandas as pd
from src.divergence_engine.adv_price_volume import AdvancedPriceVolume


def test_dv_shock():
    """Verify that DV-Shock computes correct z-scores relative to rolling window."""
    # Create simple dataset with a major liquidity shock on last bar
    df = pd.DataFrame({
        "delivery_qty": [10.0] * 19 + [100.0] # 20 bars
    })
    
    adv = AdvancedPriceVolume(lookback=20)
    df = adv.compute_dv_shock(df)
    
    # Standard deviation should be positive, and last bar should be a massive positive shock
    assert df["dv_shock"].iloc[-1] > 2.0
    # First few bars (less than min_periods=5) should be 0.0 or filled
    assert df["dv_shock"].iloc[0] == 0.0


def test_esr():
    """Verify that Relative Volume Spread Efficiency (ESR) handles spreads and relative volume."""
    # Bar 1: Wide price spread on extremely low volume (Highly Efficient)
    # Bar 2: Tight price spread on extremely high volume (Low Efficiency / Churn)
    df = pd.DataFrame({
        "high": [110.0, 100.5],
        "low": [100.0, 100.0],
        "volume": [10.0, 1000.0]
    })
    
    adv = AdvancedPriceVolume(lookback=2)
    df = adv.compute_esr(df)
    
    esr_high = df["esr"].iloc[0]
    esr_churn = df["esr"].iloc[1]
    
    # Efficient bar should have much higher ESR than the churn bar
    assert esr_high > esr_churn


def test_swing_anchored_dvwap():
    """Verify that S-DVWAP anchors dynamically to the local trough over lookback window."""
    # Price rises from 100 to 120, then crashes to 90 (new trough), then starts recovery
    lows = [100.0, 105.0, 110.0, 120.0, 90.0, 95.0, 97.0]
    highs = [102.0, 107.0, 112.0, 122.0, 92.0, 97.0, 99.0]
    closes = [101.0, 106.0, 111.0, 121.0, 91.0, 96.0, 98.0]
    del_qty = [10.0] * len(lows)
    
    df = pd.DataFrame({
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [20.0] * len(lows),
        "delivery_qty": del_qty
    })
    
    adv = AdvancedPriceVolume(swing_lookback=4)
    df = adv.compute_swing_anchored_dvwap(df)
    
    # On the last bar (index 6, low=97), the 4-day lookback includes low values [120, 90, 95, 97]
    # The minimum low in this window is index 4 (value=90)
    # So it must anchor S-DVWAP starting at index 4 through 6
    # Let's verify typical price averages over index 4, 5, 6
    tp_4 = (92.0 + 90.0 + 91.0) / 3.0 # 91.0
    tp_5 = (97.0 + 95.0 + 96.0) / 3.0 # 96.0
    tp_6 = (99.0 + 97.0 + 98.0) / 3.0 # 98.0
    expected_sdvwap = (tp_4*10 + tp_5*10 + tp_6*10) / 30.0 # 95.0
    
    assert pytest.approx(df["sdvwap"].iloc[6], 1e-4) == expected_sdvwap
