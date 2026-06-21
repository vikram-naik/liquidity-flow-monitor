import pytest
import numpy as np
import pandas as pd
from src.divergence_engine.cts.causal_savgol import CausalSavgolStrategy

def test_causal_savgol_strict_causality():
    """
    Test that adding a new data point at the end of the series does not alter 
    the calculated values of 'cts', 'cts_slope', and 'cts_accel' on previous bars.
    """
    np.random.seed(42)
    n_bars = 100
    cwvap = np.cumsum(np.random.normal(0, 1, n_bars)) + 100.0
    atr = np.random.uniform(1.0, 5.0, n_bars)

    df_full = pd.DataFrame({
        "cwvap": cwvap,
        "atr_20": atr
    })

    # Run strategy on full dataset
    strategy = CausalSavgolStrategy(window_length=15, polyorder=2)
    res_full = strategy.compute(df_full.copy())

    # Run strategy on dataset without the last bar
    df_short = df_full.iloc[:-1].copy()
    res_short = strategy.compute(df_short)

    # The length of res_short is n_bars - 1
    # Verify that all calculated indicators on all bars in res_short match res_full (excluding the last bar of res_full)
    n_compare = n_bars - 1
    
    for col in ["cts", "cts_slope", "cts_accel", "smoothed_cwvap"]:
        vals_full = res_full[col].iloc[:n_compare].values
        vals_short = res_short[col].values
        
        # Filter out NaNs for comparison (warmup period)
        valid_idx = ~np.isnan(vals_short)
        
        assert np.any(valid_idx), f"All values are NaN in {col} of short series"
        np.testing.assert_allclose(
            vals_full[valid_idx],
            vals_short[valid_idx],
            rtol=1e-7,
            atol=1e-7,
            err_msg=f"Causality violation found in column '{col}'"
        )
