from __future__ import annotations

import numpy as np
import pandas as pd
from .base import CTSStrategy


class KAMAStrategy(CTSStrategy):
    """
    KAMA-based implementation of CTS.
    Uses Kaufman's Adaptive Moving Average to automatically adapt to volatility,
    flattening during chop and trailing closely during fast trends.
    """

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute CTS and its derivatives using the KAMA strategy.
        Mutates df in place and returns it.
        """
        cwvap_s = df["cwvap"]
        atr = df["atr_20"].values
        close = df["close"]

        # Helper for KAMA
        def kama(price: pd.Series, er_period: int, fast_span: int = 2, slow_span: int = 30) -> pd.Series:
            """
            Computes Kaufman's Adaptive Moving Average.
            er_period: Window used to calculate the Efficiency Ratio (ER).
            fast_span/slow_span: Define the bounds of the smoothing constant.
            """
            change = price.diff(er_period).abs()
            volatility = price.diff().abs().rolling(window=er_period).sum()
            
            # Efficiency Ratio (ER) = Direction / Volatility
            er = np.where(volatility > 0, change / volatility, 0.0)
            
            # Smoothing constants
            fastest = 2.0 / (fast_span + 1.0)
            slowest = 2.0 / (slow_span + 1.0)
            
            # Scaled smoothing constant
            sc = (er * (fastest - slowest) + slowest) ** 2
            
            # Apply KAMA using EWMA approach iteratively
            # KAMA_i = KAMA_{i-1} + SC_i * (Price_i - KAMA_{i-1})
            kama_vals = np.full(len(price), np.nan)
            
            # Initialize the first valid KAMA with simple average of first ER period
            start_idx = er_period
            if start_idx < len(price):
                kama_vals[start_idx] = price.iloc[start_idx]  # simplified init
                
            prices_arr = price.values
            for i in range(start_idx + 1, len(prices_arr)):
                kama_vals[i] = kama_vals[i-1] + sc[i] * (prices_arr[i] - kama_vals[i-1])
                
            return pd.Series(kama_vals, index=price.index)

        # For KAMA we use the original periods (5, 8, 14, 21) as the ER lookback periods
        kama_5 = kama(cwvap_s, er_period=5)
        kama_8 = kama(cwvap_s, er_period=8)
        kama_14 = kama(cwvap_s, er_period=14)
        kama_21 = kama(cwvap_s, er_period=21)
        
        # Save them into DF just in case they are needed for plotting/debugging
        df["cwvap_ema_5"] = kama_5
        df["cwvap_ema_8"] = kama_8
        df["cwvap_ema_14"] = kama_14
        df["cwvap_ema_21"] = kama_21

        # CWVAP Trend Score (CTS): average normalised spread of adjacent KAMA pairs
        pairs = [
            (kama_5.values, kama_8.values),
            (kama_8.values, kama_14.values),
            (kama_14.values, dema_21.values if 'dema_21' in locals() else kama_21.values), 
        ]
        
        # fix local issue in pairs tuple above (handling the dema typo inside kama)
        pairs = [
            (kama_5.values, kama_8.values),
            (kama_8.values, kama_14.values),
            (kama_14.values, kama_21.values),
        ]
        
        spreads = np.zeros(len(df))
        for short, long in pairs:
            # Avoid division by zero
            raw = np.where(atr > 0, (short - long) / atr, 0.0)
            spreads += np.clip(raw, -1.0, 1.0)
        df["cts"] = spreads / len(pairs)

        # Uses the fast vectorized linear regression convolution
        slopes, accel = self._compute_derivatives(df, window=5)
        df["cts_slope"] = slopes
        df["cts_accel"] = accel

        return df
