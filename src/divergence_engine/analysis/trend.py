"""
Module 1.30 — Price Range Trend (PRT).

Computes a composite trend score from multiple price range positions
[10, 22, 63, 252]. Smoothing is applied via Savitzky-Golay filter
to produce a stable signal of relative price location.

Output:
- **prt**: Price Range Trend (-1 to 1). 
  - Values near 1 indicates price is at the top of all historical ranges (Distribution).
  - Values near -1 indicates price is at the base of all historical ranges (Accumulation).
- **prt_buy_threshold**: 10th percentile adaptive threshold.
- **prt_sell_threshold**: 90th percentile adaptive threshold.
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

    def __init__(self, window_length: int = 15, polyorder: int = 2, threshold_window: int = 60) -> None:
        self.window_length = window_length
        self.polyorder = polyorder
        self.threshold_window = threshold_window
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
            df["fas_slope"] = 0.0
            df["fas_accel"] = 0.0
            df["fas_slope_sum_5"] = 0.0
            df["prt_slope_sum_5"] = 0.0
            df["fas_min_5"] = 0.0
            df["fas_min_10"] = 0.0
            df["fas_max_5"] = 0.0
            df["fas_max_10"] = 0.0
            df["fas_slope_change_3"] = 0.0
            df["prt_slope_change_3"] = 0.0
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
            
            # Compute fas_slope and fas_accel using the same filter & scale factor
            if "fas" in df.columns:
                fas_vals = df["fas"].fillna(0.0).values
                df["fas_slope"] = lfilter(self.coeffs_d1, [1.0], fas_vals) * scale_factor
                df["fas_accel"] = lfilter(self.coeffs_d2, [1.0], fas_vals) * scale_factor
            else:
                df["fas_slope"] = 0.0
                df["fas_accel"] = 0.0

            # Compute Lookback Context Features
            df["fas_slope_sum_5"] = df["fas_slope"].rolling(window=5, min_periods=5).sum()
            df["prt_slope_sum_5"] = df["prt_slope"].rolling(window=5, min_periods=5).sum()
            df["fas_min_5"] = df["fas"].rolling(window=5, min_periods=5).min()
            df["fas_min_10"] = df["fas"].rolling(window=10, min_periods=10).min()
            df["fas_max_5"] = df["fas"].rolling(window=5, min_periods=5).max()
            df["fas_max_10"] = df["fas"].rolling(window=10, min_periods=10).max()
            df["fas_slope_change_3"] = df["fas_slope"].diff(3)
            df["prt_slope_change_3"] = df["prt_slope"].diff(3)

            # 3. Adaptive Thresholds
            min_periods = max(30, self.threshold_window // 2)
            df["prt_buy_threshold"] = df["prt"].rolling(
                window=self.threshold_window, min_periods=min_periods
            ).quantile(0.10).fillna(0.0).round(4)
            df["prt_sell_threshold"] = df["prt"].rolling(
                window=self.threshold_window, min_periods=min_periods
            ).quantile(0.90).fillna(0.0).round(4)

            # Handle warm-up
            warmup = self.window_length - 1
            cols = [
                "prt", "prt_slope", "prt_accel", "prt_buy_threshold", "prt_sell_threshold",
                "fas_slope", "fas_accel",
                "fas_slope_sum_5", "prt_slope_sum_5",
                "fas_min_5", "fas_min_10", "fas_max_5", "fas_max_10",
                "fas_slope_change_3", "prt_slope_change_3"
            ]
            df.iloc[:warmup, df.columns.get_indexer(cols)] = np.nan
        else:
            df["prt"] = raw_score.clip(-1.0, 1.0)
            df["prt_slope"] = 0.0
            df["prt_accel"] = 0.0
            df["fas_slope"] = 0.0
            df["fas_accel"] = 0.0
            df["fas_slope_sum_5"] = 0.0
            df["prt_slope_sum_5"] = 0.0
            df["fas_min_5"] = 0.0
            df["fas_min_10"] = 0.0
            df["fas_max_5"] = 0.0
            df["fas_max_10"] = 0.0
            df["fas_slope_change_3"] = 0.0
            df["prt_slope_change_3"] = 0.0

        return df
