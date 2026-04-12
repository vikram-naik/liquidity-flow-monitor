"""
Module 1.30 — Price Range Trend (PRT).

Computes a composite trend score from multiple price range positions
[10, 22, 63, 252]. Smoothing is applied via Savitzky-Golay filter
to produce a stable signal of relative price location.

Output:
- **prt**: Price Range Trend (-1 to 1). 
  - Values near 1 indicates price is at the top of all historical ranges (Distribution).
  - Values near -1 indicates price is at the base of all historical ranges (Accumulation).
- **prt_slope**: 1st derivative of PRT.
- **prt_accel**: 2nd derivative of PRT.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import savgol_coeffs, lfilter

class SavitzkyGolayAnalyzer:
    """Base analyzer for Savitzky-Golay filtering."""
    def __init__(self, window_length: int = 15, polyorder: int = 2):
        self.window_length = window_length
        self.polyorder = polyorder

class PriceRangeTrend:
    """Compute Price Range Trend (PRT) and its derivatives.

    Combines RP10, RP22, RP63, RP252 into a smoothed score (PRT),
    slope, and acceleration using causal Savitzky-Golay filters.
    """

    def __init__(self, window_length: int = 15, polyorder: int = 2) -> None:
        self.window_length = window_length
        self.polyorder = polyorder
        # Causal coefficients for Smoothing (deriv=0), Slope (deriv=1), Accel (deriv=2)
        self.coeffs_v = savgol_coeffs(window_length, polyorder, deriv=0, pos=window_length - 1)
        self.coeffs_d1 = savgol_coeffs(window_length, polyorder, deriv=1, pos=window_length - 1)
        self.coeffs_d2 = savgol_coeffs(window_length, polyorder, deriv=2, pos=window_length - 1)

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute PRT, slope, and accel and add to DataFrame."""
        needed = ["range_pos_10", "range_pos_22", "range_pos_63", "range_pos_252"]
        if not all(c in df.columns for c in needed):
            df["prt"] = 0.0
            df["prt_slope"] = 0.0
            df["prt_accel"] = 0.0
            return df

        # 1. Composite Score: Average of range positions (0 to 1)
        raw_composite = df[needed].mean(axis=1)
        # Map 0..1 to -1..1
        raw_score = (raw_composite - 0.5) * 2.0

        # 2. Apply causal filters
        if len(df) >= self.window_length:
            vals = raw_score.fillna(0.0).values
            
            # Use a scale factor consistent with CTS for derivatives
            scale_factor = 5.0
            
            df["prt"] = np.clip(lfilter(self.coeffs_v, [1.0], vals), -1.0, 1.0)
            df["prt_slope"] = lfilter(self.coeffs_d1, [1.0], vals) * scale_factor
            df["prt_accel"] = lfilter(self.coeffs_d2, [1.0], vals) * scale_factor
            
            # Handle warm-up
            warmup = self.window_length - 1
            df.iloc[:warmup, df.columns.get_indexer(["prt", "prt_slope", "prt_accel"])] = np.nan
        else:
            df["prt"] = raw_score.clip(-1.0, 1.0)
            df["prt_slope"] = 0.0
            df["prt_accel"] = 0.0

        return df
