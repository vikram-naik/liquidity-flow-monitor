import numpy as np
import pandas as pd
import pytest
from src.divergence_engine.engine import DivergenceEngine

def test_divergence_engine_strict_causality():
    """
    Test that adding a new data point at the end of the series does not alter
    the calculated indicator values on previous bars.
    We exclude oracle-related columns since they are offline labels designed to look ahead.
    """
    np.random.seed(42)
    n_bars = 150
    
    # Generate synthetic price-volume data
    close = np.cumsum(np.random.normal(0, 1.0, n_bars)) + 100.0
    high = close + np.random.uniform(0.1, 2.0, n_bars)
    low = close - np.random.uniform(0.1, 2.0, n_bars)
    open_px = close + np.random.normal(0, 0.5, n_bars)
    # Clamp high/low
    high = np.maximum(high, np.maximum(open_px, close))
    low = np.minimum(low, np.minimum(open_px, close))
    
    volume = np.random.randint(10000, 500000, n_bars)
    delivery_qty = (volume * np.random.uniform(0.2, 0.7, n_bars)).astype(int)
    
    dates = pd.date_range(start="2026-01-01", periods=n_bars, freq="D")
    
    df_full = pd.DataFrame({
        "date": dates,
        "open": open_px,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "delivery_qty": delivery_qty,
        "delivery_pct": (delivery_qty / volume) * 100.0
    })
    
    # Run engine on full dataset
    engine_full = DivergenceEngine(ticker="TEST", df=df_full.copy())
    res_full = engine_full.run()
    ledger_full = res_full.ledger
    
    # Run engine on truncated dataset (all except the last bar)
    df_short = df_full.iloc[:-1].copy()
    engine_short = DivergenceEngine(ticker="TEST", df=df_short)
    res_short = engine_short.run()
    ledger_short = res_short.ledger
    
    # Compare common bars
    n_compare = len(ledger_short)
    
    # Exclude columns that are either non-numeric/metadata or known look-ahead targets (like oracle)
    exclude_cols = {"date", "oracle_smooth", "oracle_label", "exit_reason", "entry_reason", "entry_tag", "gradient_shape"}
    
    for col in ledger_short.columns:
        if col in exclude_cols or col.startswith("oracle"):
            continue
            
        vals_full_s = ledger_full[col].iloc[:n_compare].reset_index(drop=True)
        vals_short_s = ledger_short[col].reset_index(drop=True)
        
        # Check if values are floats/numeric
        if np.issubdtype(vals_short_s.dtype, np.number):
            vals_full = vals_full_s.values
            vals_short = vals_short_s.values
            valid_idx = ~np.isnan(vals_short)
            assert np.any(valid_idx), f"All values are NaN in column '{col}'"
            
            np.testing.assert_allclose(
                vals_full[valid_idx],
                vals_short[valid_idx],
                rtol=1e-6,
                atol=1e-6,
                err_msg=f"Causality violation found in column '{col}'"
            )
        else:
            # String/Boolean/Object comparison (handles NaNs correctly)
            assert vals_full_s.equals(vals_short_s), f"Causality violation found in non-numeric column '{col}'"

if __name__ == "__main__":
    pytest.main([__file__])
