from __future__ import annotations

import numpy as np
import pandas as pd
from .base import CTSStrategy


class DEMAStrategy(CTSStrategy):
    """
    DEMA-based implementation of CTS.
    Uses 5, 8, 14, 21 period DEMAs `(2*EMA - EMA(EMA))` to reduce lag.
    """

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute CTS and its derivatives using the DEMA strategy.
        Mutates df in place and returns it.
        """
        cwvap_s = df["cwvap"]
        atr = df["atr_20"].values

        # Helper for DEMA
        def dema(s: pd.Series, span: int) -> pd.Series:
            ema1 = s.ewm(span=span, adjust=False).mean()
            ema2 = ema1.ewm(span=span, adjust=False).mean()
            return 2 * ema1 - ema2

        # CWVAP DEMAs for trend scoring
        dema_5 = dema(cwvap_s, span=5)
        dema_8 = dema(cwvap_s, span=8)
        dema_14 = dema(cwvap_s, span=14)
        dema_21 = dema(cwvap_s, span=21)
        
        # Save them into DF just in case they are needed for plotting/debugging
        df["cwvap_ema_5"] = dema_5
        df["cwvap_ema_8"] = dema_8
        df["cwvap_ema_14"] = dema_14
        df["cwvap_ema_21"] = dema_21

        # CWVAP Trend Score (CTS): average normalised spread of adjacent DEMA pairs
        # Each pair: (short - long) / ATR, clipped to [-1, 1], then averaged.
        pairs = [
            (dema_5.values, dema_8.values),
            (dema_8.values, dema_14.values),
            (dema_14.values, dema_21.values),
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
