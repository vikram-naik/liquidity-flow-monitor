"""
Module 2 — Rolling Anchor DVL Ledger.

Computes delivery-volume metrics across four rolling anchor windows
[10, 30, 60, 120] to characterise institutional commitment, velocity,
and price-delivery divergence.

Outputs per window *n*:
- **DVL_n**: Cumulative delivery volume over *n* bars.
- **DVL_rate_n**: Average daily delivery (DVL_n / n).
- **Velocity_n_norm**: Normalised slope of DVL_n over n/2 bars.
- **Price_distance_n**: Price displacement from anchor in ATR units.
- **ARS_n**: Anchor Relative Strength (DVL vs expectation).
- **PDD_n**: Price-DVL Divergence.

Also classifies the **Gradient Vector** into one of 9 trend shapes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.divergence_engine.utils import WINDOWS


class DVLLedger:
    """Compute the rolling-anchor DVL ledger for all configured windows.

    Expected prerequisite columns (from :class:`BaseCalculator`):
    ``close, delivery_qty, atr_20``.
    """

    def __init__(self, windows: list[int] | None = None) -> None:
        self.windows = windows or WINDOWS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add all Module 2 columns and the ``gradient_shape`` classification."""
        for n in self.windows:
            df = self._compute_window(df, n)
        df = self._classify_gradient(df)
        df = self._compute_cdvl(df)
        return df

    # ------------------------------------------------------------------
    # Per-window calculations
    # ------------------------------------------------------------------

    def _compute_window(self, df: pd.DataFrame, n: int) -> pd.DataFrame:
        """Compute DVL metrics for a single window *n*."""
        suf = f"_{n}"
        delivery = df["delivery_qty"]
        close = df["close"]
        atr = df["atr_20"]

        # 2.1 DVL & DVL_rate
        df[f"dvl{suf}"] = delivery.rolling(window=n, min_periods=1).sum()
        df[f"dvl_rate{suf}"] = df[f"dvl{suf}"] / n

        # 2.2 DVL Velocity (slope of DVL over last n/2 bars, normalised)
        half = max(n // 2, 2)
        df[f"velocity{suf}"] = self._rolling_slope(df[f"dvl{suf}"], half)

        # Normalise: divide by 60-bar mean of dvl_rate
        dvl_rate_mean_60 = df[f"dvl_rate{suf}"].rolling(window=60, min_periods=1).mean()
        df[f"velocity{suf}_norm"] = np.where(
            dvl_rate_mean_60 > 0,
            df[f"velocity{suf}"] / dvl_rate_mean_60,
            0.0,
        )

        # 2.3 Anchor Cost Basis
        price_anchor = close.shift(n)
        df[f"price_distance{suf}"] = np.where(
            atr > 0, (close - price_anchor) / atr, 0.0
        )

        # 2.4 ARS — Anchor Relative Strength
        expected_dvl = df[f"dvl_rate{suf}"].rolling(window=20, min_periods=1).mean() * n
        df[f"ars{suf}"] = np.where(expected_dvl > 0, df[f"dvl{suf}"] / expected_dvl, 1.0)

        # 2.5 PDD — Price-DVL Divergence
        price_move = np.where(atr > 0, (close - close.shift(n)) / atr, 0.0)
        dvl_rate_mean_20 = df[f"dvl_rate{suf}"].rolling(window=20, min_periods=1).mean()
        dvl_bias = np.where(
            dvl_rate_mean_20 > 0,
            df[f"dvl_rate{suf}"] / dvl_rate_mean_20 - 1.0,
            0.0,
        )
        df[f"pdd{suf}"] = price_move - dvl_bias

        return df

    # ------------------------------------------------------------------
    # Gradient Vector classification
    # ------------------------------------------------------------------

    def _classify_gradient(self, df: pd.DataFrame) -> pd.DataFrame:
        """Classify the velocity gradient into one of 9 trend shapes.

        The gradient vector is ``[V10, V30, V60, V120]`` (normalised).

        Shapes:
        - uptrend_forming:    V10 > 0, V30 > 0, V60 ≤ 0, V120 ≤ 0
        - uptrend_mature:     all > 0, V10 ≥ V30 ≥ V60
        - uptrend_exhausting: V10 < V30, V60 > 0, V120 > 0
        - distribution:       V10 < 0, V30 near 0, V60 > 0, V120 > 0
        - accumulation:       V10 > 0 and rising, V30..V120 ≤ 0
        - downtrend_forming:  V10 < 0, V30 < 0, V60 ≥ 0
        - downtrend_mature:   all < 0
        - recovering:         V10 > 0, V30 turning positive, V60 < 0
        - sideways:           all near zero (abs < 0.2)
        """
        v10 = df["velocity_10_norm"].values
        v30 = df["velocity_30_norm"].values
        v60 = df["velocity_60_norm"].values
        v120 = df["velocity_120_norm"].values

        # Compute V10 slope (last 5 bars) for "accumulation" and
        # V30 slope for "recovering"
        v10_slope = self._rolling_slope(df["velocity_10_norm"], 5).values
        v30_slope = self._rolling_slope(df["velocity_30_norm"], 5).values

        n = len(df)
        shapes = np.full(n, "sideways", dtype=object)
        near_zero = 0.2

        for i in range(n):
            _v10 = v10[i] if not np.isnan(v10[i]) else 0.0
            _v30 = v30[i] if not np.isnan(v30[i]) else 0.0
            _v60 = v60[i] if not np.isnan(v60[i]) else 0.0
            _v120 = v120[i] if not np.isnan(v120[i]) else 0.0
            _v10s = v10_slope[i] if not np.isnan(v10_slope[i]) else 0.0
            _v30s = v30_slope[i] if not np.isnan(v30_slope[i]) else 0.0

            # Check sideways first — all near zero
            if all(abs(v) < near_zero for v in [_v10, _v30, _v60, _v120]):
                shapes[i] = "sideways"
            elif _v10 > 0 and _v30 > 0 and _v60 > 0 and _v120 > 0 and _v10 >= _v30 >= _v60:
                shapes[i] = "uptrend_mature"
            elif _v10 > 0 and _v30 > 0 and _v60 <= 0 and _v120 <= 0:
                shapes[i] = "uptrend_forming"
            elif _v10 < _v30 and _v60 > 0 and _v120 > 0:
                shapes[i] = "uptrend_exhausting"
            elif _v10 < 0 and abs(_v30) < near_zero and _v60 > 0 and _v120 > 0:
                shapes[i] = "distribution"
            elif _v10 > 0 and _v10s > 0 and _v30 <= 0 and _v60 <= 0:
                shapes[i] = "accumulation"
            elif _v10 < 0 and _v30 < 0 and _v60 < 0 and _v120 < 0:
                shapes[i] = "downtrend_mature"
            elif _v10 < 0 and _v30 < 0 and _v60 >= 0:
                shapes[i] = "downtrend_forming"
            elif _v10 > 0 and _v30s > 0 and _v60 < 0:
                shapes[i] = "recovering"
            # else: stays "sideways" (default)

        df["gradient_shape"] = shapes
        return df

    def _compute_cdvl(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute Composite Delivery Velocity (CDVL).

        CDVL = Σ(Velocity_n_norm × DVL_rate_n) / Σ(DVL_rate_n).
        Provides a continuous float baseline for volume direction/momentum.
        """
        numerator = pd.Series(0.0, index=df.index)
        denominator = pd.Series(0.0, index=df.index)

        for n in self.windows:
            vel = df[f"velocity_{n}_norm"]
            weight = df[f"dvl_rate_{n}"]

            # Only weight valid velocities
            valid = vel.notna() & np.isfinite(vel)
            numerator += (vel * weight).where(valid, 0.0)
            denominator += weight.where(valid, 0.0)

        df["cdvl"] = np.where(denominator > 0, numerator / denominator, 0.0)
        return df

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rolling_slope(series: pd.Series, window: int) -> pd.Series:
        """Compute rolling linear regression slope using numpy.polyfit.

        Returns a Series of slopes where each value is the slope of the
        best-fit line over the last *window* bars.
        """
        values = series.values.astype(float)
        slopes = np.full(len(values), np.nan)
        x = np.arange(window, dtype=float)

        for i in range(window - 1, len(values)):
            y = values[i - window + 1 : i + 1]
            if np.any(np.isnan(y)):
                continue
            # polyfit degree=1 → [slope, intercept]
            coeffs = np.polyfit(x, y, 1)
            slopes[i] = coeffs[0]

        return pd.Series(slopes, index=series.index)
