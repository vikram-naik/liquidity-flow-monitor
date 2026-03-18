from __future__ import annotations

import numpy as np
import pandas as pd
from .base import CTSStrategy


class DefaultEMAStrategy(CTSStrategy):
    """
    Original EMA-based implementation of CTS.
    Uses 5, 8, 14, 21 period EMAs and manual looping for slope calculations.
    """

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute CTS and its derivatives using the Default EMA strategy.
        Mutates df in place and returns it.
        """
        cwvap_s = df["cwvap"]
        atr = df["atr_20"].values

        # CWVAP EMAs for trend scoring
        df["cwvap_ema_5"] = cwvap_s.ewm(span=5, adjust=False).mean()
        df["cwvap_ema_8"] = cwvap_s.ewm(span=8, adjust=False).mean()
        df["cwvap_ema_14"] = cwvap_s.ewm(span=14, adjust=False).mean()
        df["cwvap_ema_21"] = cwvap_s.ewm(span=21, adjust=False).mean()

        # CWVAP Trend Score (CTS): average normalised spread of adjacent EMA pairs
        # Each pair: (short - long) / ATR, clipped to [-1, 1], then averaged.
        pairs = [
            (df["cwvap_ema_5"].values, df["cwvap_ema_8"].values),
            (df["cwvap_ema_8"].values, df["cwvap_ema_14"].values),
            (df["cwvap_ema_14"].values, df["cwvap_ema_21"].values),
        ]
        spreads = np.zeros(len(df))
        for short, long in pairs:
            # Avoid division by zero
            raw = np.where(atr > 0, (short - long) / atr, 0.0)
            spreads += np.clip(raw, -1.0, 1.0)
        df["cts"] = spreads / len(pairs)

        # CTS slope: linear regression over 5-bar window (first derivative)
        cts_vals = df["cts"].values.astype(float)
        n_slope = 5
        slopes = np.full(len(cts_vals), np.nan)
        x = np.arange(n_slope, dtype=float)
        x_mean = x.mean()
        ss_xx = ((x - x_mean) ** 2).sum()
        for i in range(n_slope - 1, len(cts_vals)):
            y = cts_vals[i - n_slope + 1 : i + 1]
            if not np.any(np.isnan(y)):
                slopes[i] = ((x - x_mean) * (y - y.mean())).sum() / ss_xx
        df["cts_slope"] = slopes

        # CTS acceleration: slope of cts_slope over 5-bar window (second derivative)
        accel = np.full(len(cts_vals), np.nan)
        for i in range(n_slope - 1, len(slopes)):
            y = slopes[i - n_slope + 1 : i + 1]
            if not np.any(np.isnan(y)):
                accel[i] = ((x - x_mean) * (y - y.mean())).sum() / ss_xx
        df["cts_accel"] = accel

        return df
