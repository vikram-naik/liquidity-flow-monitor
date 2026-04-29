"""
Module 1.25 — Price Range Position.

Computes rolling price-range metrics that locate the current close
relative to historical highs and lows across multiple lookback windows.

Outputs per window *n* (trading days):
- **dist_high_n**: Distance from rolling *n*-bar high (%, always ≤ 0).
- **dist_low_n**: Distance from rolling *n*-bar low (%, always ≥ 0).
- **range_pos_n**: Normalised position within the range (0 = at low, 1 = at high).
- **range_width_n**: Range span as % of close (compression / expansion gauge).

Basing detection:
- **bars_at_base**: Consecutive bars where range_pos_252 < 0.25
  (how long price has been sitting in oversold territory).
- **base_tightness**: range_width_10 / range_width_63 — recent range
  compression relative to the broader move (< 0.30 = tight base).

Global flag:
- **is_ath**: True when the current close equals the all-time high
  (rolling max over the entire available history).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.divergence_engine.analysis.trend import PriceRangeTrend

# Default lookback windows (trading days)
_WINDOWS = (10, 22, 63, 252)


class PriceRange:
    """Compute rolling price-range position metrics.

    Expected input columns: ``high, low, close``.

    Parameters
    ----------
    windows : tuple[int, ...]
        Rolling lookback windows in trading days.
    """

    def __init__(self, windows: tuple[int, ...] = _WINDOWS) -> None:
        self.windows = windows

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add all price-range columns and the ATH flag. Returns *df*."""
        for n in self.windows:
            df = self._compute_window(df, n)

        df = self._compute_ath(df)
        df = self._compute_basing(df)
        df = self._compute_composite(df)

        # Module 1.30 — Price Range Trend (PRT)
        prt = PriceRangeTrend()
        df = prt.compute(df)

        return df

    # ------------------------------------------------------------------
    # Per-window calculations
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_window(df: pd.DataFrame, n: int) -> pd.DataFrame:
        """Compute range metrics for a single lookback window *n*."""
        close = df["close"]

        rng_high = df["high"].rolling(window=n, min_periods=1).max()
        rng_low = df["low"].rolling(window=n, min_periods=1).min()
        rng_span = rng_high - rng_low

        # Distance from high (always ≤ 0)
        df[f"dist_high_{n}"] = np.where(
            rng_high > 0,
            (close - rng_high) / rng_high * 100,
            0.0,
        )

        # Distance from low (always ≥ 0)
        df[f"dist_low_{n}"] = np.where(
            rng_low > 0,
            (close - rng_low) / rng_low * 100,
            0.0,
        )

        # Normalised position within range (0 = at low, 1 = at high)
        df[f"range_pos_{n}"] = np.where(
            rng_span > 0,
            (close - rng_low) / rng_span,
            0.5,  # degenerate range → midpoint
        )

        # Range width as % of close (compression / expansion)
        df[f"range_width_{n}"] = np.where(
            close > 0,
            rng_span / close * 100,
            0.0,
        )

        # Stash raw highs/lows temporarily (engine drops them later)
        df[f"rng_high_{n}"] = rng_high
        df[f"rng_low_{n}"] = rng_low

        return df

    # ------------------------------------------------------------------
    # All-time high flag
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_ath(df: pd.DataFrame) -> pd.DataFrame:
        """Set ``is_ath`` = True when either high or close hits the expanding all-time high."""
        ath = df["high"].expanding(min_periods=1).max()
        df["ath"] = ath
        # True if today's high reached the historical high OR today's close is >= previous ath
        df["is_ath"] = df["high"] >= ath
        return df

    # ------------------------------------------------------------------
    # Basing detection
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_basing(df: pd.DataFrame, threshold: float = 0.25) -> pd.DataFrame:
        """Detect price basing near 52-week lows.

        ``bars_at_base``: consecutive bars where range_pos_252 < *threshold*.
        Resets to 0 the moment price exits oversold territory.

        ``base_tightness``: range_width_10 / range_width_63.
        Values < 0.30 indicate the recent 10-day range is compressed
        relative to the quarterly range — tight consolidation after a drop.
        """
        rp252 = df["range_pos_252"].values
        is_oversold = rp252 < threshold

        # Consecutive oversold bar count (vectorised streak counter)
        bars = np.zeros(len(rp252), dtype=np.int32)
        for i in range(len(rp252)):
            if is_oversold[i]:
                bars[i] = (bars[i - 1] + 1) if i > 0 else 1
            # else: stays 0
        df["bars_at_base"] = bars

        # Tightness: recent range vs quarterly range
        rw10 = df["range_width_10"].values
        rw63 = df["range_width_63"].values
        df["base_tightness"] = np.where(rw63 > 0, rw10 / rw63, 1.0)

        return df

    # ------------------------------------------------------------------
    # Composite Features
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_composite(df: pd.DataFrame) -> pd.DataFrame:
        """Compute composite features like Fractal Alignment Score (FAS)."""
        # Map 0..1 to -1..1
        p10 = (df["range_pos_10"] * 2.0) - 1.0
        p22 = (df["range_pos_22"] * 2.0) - 1.0
        p63 = (df["range_pos_63"] * 2.0) - 1.0
        p252 = (df["range_pos_252"] * 2.0) - 1.0

        # Fractal Alignment Score (Macro-weighted to show structural location)
        # 60% Yearly, 30% Quarterly, 20% Monthly, 10% Weekly
        df["fas"] = (0.10 * p10) + (0.2 * p22) + (0.30 * p63) + (0.60 * p252)
        
        return df
