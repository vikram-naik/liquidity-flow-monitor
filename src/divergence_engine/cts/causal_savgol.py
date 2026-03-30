import numpy as np
import pandas as pd
from scipy.signal import savgol_coeffs, lfilter
from .base import CTSStrategy


class CausalSavgolStrategy(CTSStrategy):
    """
    Causal Savitzky-Golay based implementation of CTS.
    Uses only past data points to estimate derivatives, suitable for real-time applications.
    Applies FIR coefficients derived from one-sided polynomial fitting.
    """

    def __init__(self, window_length: int = 15, polyorder: int = 2,
                 threshold_window: int = 60, threshold_pct: float = 35.0,
                 trough_threshold: float = -0.187):
        """
        :param window_length: The length of the filter window.
        :param polyorder: The order of the polynomial used to fit the samples.
        :param threshold_window: Rolling window for the cts_slope_threshold percentile.
        :param threshold_pct: Percentile (0-100) of |cts_slope| used as the threshold.
        :param trough_threshold: Level below which to look for troughs in downtrends.
        """
        self.window_length = window_length
        self.polyorder = polyorder
        self.threshold_window = threshold_window
        self.threshold_pct = threshold_pct
        self.trough_threshold = trough_threshold

        # Precompute causal coefficients for each derivative
        self.coeffs_v = savgol_coeffs(window_length, polyorder, deriv=0, pos=window_length - 1)
        self.coeffs_d1 = savgol_coeffs(window_length, polyorder, deriv=1, pos=window_length - 1)
        self.coeffs_d2 = savgol_coeffs(window_length, polyorder, deriv=2, pos=window_length - 1)
        
        # Jerk (3rd derivative)
        if polyorder >= 3:
            self.coeffs_d3 = savgol_coeffs(window_length, polyorder, deriv=3, pos=window_length - 1)
        else:
            self.coeffs_d3 = None

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute CTS and its derivatives using Causal Savitzky-Golay polynomial fitting.
        
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
            df["cts_slope_trough"] = 0
            return df

        # Apply causal filters using lfilter
        b_v = self.coeffs_v
        b_d1 = self.coeffs_d1
        b_d2 = self.coeffs_d2

        smoothed = lfilter(b_v, [1.0], cwvap_vals)
        velocity = lfilter(b_d1, [1.0], cwvap_vals)
        acceleration = lfilter(b_d2, [1.0], cwvap_vals)
        
        if self.coeffs_d3 is not None:
            jerk = lfilter(self.coeffs_d3, [1.0], cwvap_vals)
        else:
            # Fallback for polyorder < 3
            jerk = np.gradient(acceleration)

        # Normalization (scaled consistent with centered version)
        scale_factor = 5.0 
        
        df["smoothed_cwvap"] = smoothed
        df["cts"] = np.where(atr > 0, np.clip((velocity * scale_factor) / atr, -1.0, 1.0), 0.0)
        
        # Calculate Slope and Accel
        raw_slope = (acceleration * scale_factor) / atr
        raw_accel = (jerk * scale_factor) / atr
        
        df["cts_slope"] = np.where(atr > 0, raw_slope, 0.0)
        df["cts_accel"] = np.where(atr > 0, raw_accel, 0.0)

        # Causal Trough Detection (No forward-looking bias)
        # Condition: slope <= threshold AND accel > 0 AND regime == downtrend
        if "regime" in df.columns:
            df["cts_slope_trough"] = (
                (df["cts_slope"] <= self.trough_threshold) & 
                (df["cts_accel"] > 0) & 
                (df["regime"] == "downtrend")
            ).astype(int)
        else:
            df["cts_slope_trough"] = 0

        # 4. Rolling Empirical Threshold (Regime-Adaptive)
        abs_slope = df["cts_slope"].abs()
        df["cts_slope_threshold"] = abs_slope.rolling(
            window=self.threshold_window,
            min_periods=max(20, self.threshold_window // 2)
        ).quantile(self.threshold_pct / 100.0).fillna(0.0)

        # Fill warm-up period with NaN
        # For a causal filter of length N, the first N-1 outputs are incomplete
        warmup = self.window_length - 1
        df.iloc[:warmup, df.columns.get_indexer(["smoothed_cwvap", "cts", "cts_slope", "cts_accel", "cts_slope_threshold"])] = np.nan
        df.iloc[:warmup, df.columns.get_indexer(["cts_slope_trough"])] = 0

        return df
