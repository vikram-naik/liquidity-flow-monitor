from __future__ import annotations

from abc import ABC, abstractmethod
import numpy as np
import pandas as pd
import scipy.signal


class CTSStrategy(ABC):
    """
    Base strategy interface for Computing CWVAP Trend Score (CTS).
    """

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute CTS and its derivatives (slope, acceleration) and attach them to df.

        Required input columns typically: 'cwvap', 'atr_20'.
        Should populate at minimum: 'cts', 'cts_slope', 'cts_accel'.
        """
        pass

    def _compute_derivatives(self, df: pd.DataFrame, window: int = 5) -> tuple[np.ndarray, np.ndarray]:
        """
        Utility method to compute rolling linear regression slope and acceleration
        using a vectorized 1D convolution (equivalent to algebraic linear regression
        slope but orders of magnitude faster).
        
        Args:
            df: DataFrame containing the 'cts' column.
            window: Number of bars for the regression window.
            
        Returns:
            Tuple of (slope_array, accel_array) aligned to the right edge of the window.
        """
        cts_vals = df["cts"].values.astype(float)
        
        # Linear regression slope via convolution:
        # cov(x, y) / var(x) 
        # For a fixed x (e.g., [0, 1, 2, 3, 4]), var(x) and (x - mean(x)) are constants.
        x = np.arange(window, dtype=float)
        x_mean = x.mean()
        ss_xx = ((x - x_mean) ** 2).sum()
        
        # weights = (x - x_mean) / ss_xx
        weights = (x - x_mean) / ss_xx
        
        # We want the dot product of the sliding window with these weights.
        # np.convolve flips the kernel, so we pass it backwards to get a standard dot product.
        kernel = weights[::-1]
        
        # 1. Slope (First Derivative)
        slopes = np.full(len(cts_vals), np.nan)
        # mode='valid' returns array of length N - window + 1
        conv_res = np.convolve(cts_vals, kernel, mode='valid')
        slopes[window - 1:] = conv_res
        
        # 2. Acceleration (Second Derivative)
        # This is strictly the slope of the slope over the SAME window size, per original implementation.
        accel = np.full(len(slopes), np.nan)
        # Find which elements of slope are valid (not nan)
        valid_mask = ~np.isnan(slopes)
        
        # In a generic dataset there could be scattered NaNs, but for typical
        # timeseries it starts with NaNs and then is valid.
        # We can just convolve where it's valid to be safe.
        # Ensure we only convolve on a contiguous block or handle boundaries.
        # For simplicity, just pad with zeros or apply convolve and set back to nan if it used a nan.
        conv_accel = np.convolve(slopes, kernel, mode='same')
        
        # Since 'mode=same' works differently than valid (centers it), 
        # let's just do valid on the whole array and shift it.
        # np.convolve handles NaNs as spreading NaNs. That's exactly what we want.
        conv_accel_valid = np.convolve(slopes, kernel, mode='valid')
        accel[2 * (window - 1):] = conv_accel_valid[window - 1:]

        return slopes, accel
