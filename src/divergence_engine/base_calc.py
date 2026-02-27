"""
Module 1 — Base Calculations.

Computes the foundational technical indicators used by all downstream
modules in the divergence engine:

- **ATR₂₀**: 20-period Wilder-smoothed Average True Range.
- **RDV**: Relative Delivery Volume (delivery participation vs its own norm).
- **MFM**: Money Flow Multiplier using true high/low (range: −1 to +1).
- **TP**: Typical Price ((H+L+C)/3).
- **MFM_TP**: MFM × Typical Price — primary input to MCS.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class BaseCalculator:
    """Compute all Module 1 base indicators on a daily DataFrame.

    Expected input columns: ``open, high, low, close, volume, delivery_qty``.
    All calculations are vectorised for performance.
    """

    def __init__(self, atr_period: int = 20, rdv_period: int = 20) -> None:
        self.atr_period = atr_period
        self.rdv_period = rdv_period

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add all Module 1 columns and return *df* (mutated in place).

        Added columns: ``true_high, true_low, tr, atr_20, rdv,
        mfm, tp, mfm_tp``.
        """
        df = self.compute_true_range_components(df)
        df = self.compute_atr(df)
        df = self.compute_rdv(df)
        df = self.compute_mfm(df)
        df = self.compute_typical_price(df)
        df = self.compute_mfm_tp(df)
        return df

    # ------------------------------------------------------------------
    # Individual calculations
    # ------------------------------------------------------------------

    @staticmethod
    def compute_true_range_components(df: pd.DataFrame) -> pd.DataFrame:
        """Compute true high, true low, and True Range.

        True High = max(high, prev_close)
        True Low  = min(low, prev_close)
        TR        = max(high-low, |high-prev_close|, |low-prev_close|)

        The first row uses simple high-low (no previous close available).
        """
        prev_close = df["close"].shift(1)

        df["true_high"] = np.maximum(df["high"], prev_close)
        df["true_low"] = np.minimum(df["low"], prev_close)

        hl = df["high"] - df["low"]
        hpc = (df["high"] - prev_close).abs()
        lpc = (df["low"] - prev_close).abs()

        df["tr"] = np.maximum(hl, np.maximum(hpc, lpc))

        # First bar: no prev_close → use simple high‐low
        df.loc[df.index[0], "tr"] = df.loc[df.index[0], "high"] - df.loc[df.index[0], "low"]

        # Edge case: no-range candle (high == low) → TR should use prior bar
        # instead of zero, but only when prev_close is available.
        no_range = (df["high"] == df["low"]) & (df.index != df.index[0])
        if no_range.any():
            df.loc[no_range, "tr"] = np.maximum(
                df.loc[no_range, "tr"], df["tr"].shift(1).loc[no_range]
            )

        return df

    def compute_atr(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute ATR₂₀ using Wilder's smoothing.

        Wilder's smoothing:
            ATR₀ = mean(TR, period)
            ATRₙ = (ATR_{n-1} × (period-1) + TRₙ) / period
        """
        n = self.atr_period
        tr = df["tr"].values.copy()
        atr = np.full_like(tr, np.nan)

        # Seed: simple mean of first `n` TR values
        if len(tr) >= n:
            atr[n - 1] = np.nanmean(tr[:n])

            # Wilder smoothing from bar `n` onwards
            for i in range(n, len(tr)):
                atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n

        df["atr_20"] = atr
        return df

    def compute_rdv(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute Relative Delivery Volume.

        RDV = delivery_qty / rolling_mean(delivery_qty, 20)

        By construction, the mean of RDV over any 20-day window ≈ 1.0.
        RDV > 1.5 = elevated institutional participation.
        RDV < 0.5 = below-average commitment.
        """
        rolling_mean = df["delivery_qty"].rolling(window=self.rdv_period, min_periods=1).mean()
        # Avoid division by zero
        df["rdv"] = np.where(rolling_mean > 0, df["delivery_qty"] / rolling_mean, 1.0)
        return df

    @staticmethod
    def compute_mfm(df: pd.DataFrame) -> pd.DataFrame:
        """Compute Money Flow Multiplier using true high/low.

        MFM = ((close − true_low) − (true_high − close)) / (true_high − true_low)

        Range: −1 to +1.
        Edge case: when true_high == true_low, MFM = 0.
        """
        true_range = df["true_high"] - df["true_low"]
        numerator = (df["close"] - df["true_low"]) - (df["true_high"] - df["close"])

        df["mfm"] = np.where(true_range > 0, numerator / true_range, 0.0)
        return df

    @staticmethod
    def compute_typical_price(df: pd.DataFrame) -> pd.DataFrame:
        """Compute Typical Price = (high + low + close) / 3."""
        df["tp"] = (df["high"] + df["low"] + df["close"]) / 3.0
        return df

    @staticmethod
    def compute_mfm_tp(df: pd.DataFrame) -> pd.DataFrame:
        """Compute MFM_TP = MFM × Typical Price.

        Used as the primary input to the Money Composite Score (MCS).
        """
        df["mfm_tp"] = df["mfm"] * df["tp"]
        return df
