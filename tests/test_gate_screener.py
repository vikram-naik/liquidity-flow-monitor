import pytest
import numpy as np
import pandas as pd
from src.divergence_engine.gate_screener import GateScreener


def test_gate_screener_basic():
    """Verify that GateScreener adds all core score and indicator columns."""
    # Construct 30 bars of static mock data
    df = pd.DataFrame({
        "open": [100.0] * 30,
        "high": [102.0] * 30,
        "low": [98.0] * 30,
        "close": [101.0] * 30,
        "volume": [1000.0] * 30,
        "delivery_qty": [500.0] * 30,
        "delivery_pct": [50.0] * 30,
        "mfm": [0.5] * 30,
        "esr": [1.0] * 30,
        "cwc": [0.8] * 30,
        "psz_v": [0.5] * 30,
        "range_pos_252": [0.5] * 30,
        "cwvap": [100.0] * 30,
        "cts": [0.5] * 30,
        "regime": ["strongup"] * 30,
    })

    screener = GateScreener()
    df = screener.compute_all(df)

    # Core columns should exist
    assert "clean_delivery" in df.columns
    assert "z_5" in df.columns
    assert "z_252" in df.columns
    assert "s_volume" in df.columns
    assert "s_price" in df.columns
    assert "s_trend" in df.columns
    assert "s_total" in df.columns
    assert "gate_setup" in df.columns
    assert "gate_signal" in df.columns


def test_gate_screener_cdma_setup():
    """Verify that a catalyst-driven momentum surge is classified as Setup 2: CDMA."""
    # Create 30 bars of base data, then spike on the last bar
    highs = [102.0] * 29 + [110.0]
    lows = [98.0] * 29 + [100.0]
    closes = [100.0] * 29 + [109.0] # closes in upper 90% of range (MFM > 0.6)
    
    # Volume spike
    vols = [1000.0] * 29 + [4000.0] # relative volume = 4.0 (>= 2.5)
    dels = [500.0] * 29 + [2000.0]  # delivery percentage = 50.0 (>= 35%)
    
    df = pd.DataFrame({
        "open": [100.0] * 30,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": vols,
        "delivery_qty": dels,
        "delivery_pct": [50.0] * 30,
        "mfm": [0.0] * 29 + [0.8], # strong money flow
        "esr": [1.0] * 29 + [2.0], # high spread efficiency
        "cwc": [0.8] * 30,
        "cwc_slope": [0.1] * 30,
        "psz_v": [0.8] * 30,
        "range_pos_252": [0.5] * 29 + [0.85], # breaks out to upper range
        "cwvap": [98.0] * 30, # price is above cwvap
        "cts": [0.8] * 30, # strong uptrend
        "regime": ["strongup"] * 30,
    })

    screener = GateScreener()
    df = screener.compute_all(df)

    last_row = df.iloc[-1]
    assert last_row["gate_setup"] == "CDMA"
    assert last_row["gate_signal"] == 1
    assert last_row["s_total"] >= 80.0
