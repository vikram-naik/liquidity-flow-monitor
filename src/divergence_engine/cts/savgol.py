import numpy as np
import pandas as pd
from scipy.signal import savgol_filter
from .base import CTSStrategy


class SavgolStrategy(CTSStrategy):
    """
    Savitzky-Golay based implementation of CTS.
    Applies polynomial fitting directly to the CWVAP series to extract derivatives.
    Bypasses the 4-EMA funnel.
    """

    def __init__(self, window_length: int = 11, polyorder: int = 2):
        """
        :param window_length: The length of the filter window (must be odd).
        :param polyorder: The order of the polynomial used to fit the samples.
        """
        if window_length % 2 == 0:
            window_length += 1
        self.window_length = window_length
        self.polyorder = polyorder

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute CTS and its derivatives using Savitzky-Golay polynomial fitting.
        
        CTS is defined as the normalized Velocity (1st derivative).
        CTS Slope is the normalized Acceleration (2nd derivative).
        """
        cwvap_vals = df["cwvap"].values.astype(float)
        atr = df["atr_20"].values
        
        # We need at least window_length points
        if len(cwvap_vals) < self.window_length:
            df["cts"] = np.nan
            df["cts_slope"] = np.nan
            df["cts_accel"] = np.nan
            return df

        # 0. Smoothed Series (Trend line) - Centered (Non-Causal)
        smoothed = savgol_filter(cwvap_vals, self.window_length, self.polyorder, deriv=0)

        # 1. First Derivative (Velocity) -> Map to CTS
        velocity = savgol_filter(cwvap_vals, self.window_length, self.polyorder, deriv=1)
        
        # 2. Second Derivative (Acceleration) -> Map to CTS Slope
        acceleration = savgol_filter(cwvap_vals, self.window_length, self.polyorder, deriv=2)
        
        # 3. Third Derivative (Jerk) -> Map to CTS Accel
        if self.polyorder >= 3:
            jerk = savgol_filter(cwvap_vals, self.window_length, self.polyorder, deriv=3)
        else:
            jerk = np.gradient(acceleration)

        # Normalization
        scale_factor = 5.0 
        
        df["smoothed_cwvap"] = smoothed
        df["cts"] = np.where(atr > 0, np.clip((velocity * scale_factor) / atr, -1.0, 1.0), 0.0)
        
        # Calculate Slope and Accel
        raw_slope = (acceleration * scale_factor) / atr
        raw_accel = (jerk * scale_factor) / atr
        
        df["cts_slope"] = np.where(atr > 0, raw_slope, 0.0)
        df["cts_accel"] = np.where(atr > 0, raw_accel, 0.0)

        # 4. Empirical Threshold for Filtering (Data-driven)
        # Using 35th percentile of absolute slope to identify 'meaningful' acceleration
        valid_slopes = np.abs(df["cts_slope"].dropna().values)
        if len(valid_slopes) > 0:
            df["cts_slope_threshold"] = np.percentile(valid_slopes, 35)
        else:
            df["cts_slope_threshold"] = 0.0

        # Fill first few entries with NaN
        half_win = self.window_length // 2
        df.iloc[:half_win, df.columns.get_indexer(["smoothed_cwvap", "cts", "cts_slope", "cts_accel", "cts_slope_threshold"])] = np.nan

        return df
