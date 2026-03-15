"""
Module 3 — Composite VWAP (CWVAP).

Builds per-window DVWAPs plus a composite weighted VWAP and
delivery-profile value-area bands.

Key outputs:
- **DVWAP_n**: Delivery-weighted VWAP per anchor window.
- **POC_n**: Point of Control (highest-delivery price bin) per window.
- **CWVAP**: Composite weighted VWAP across all anchors.
- **va_high / va_low**: Delivery-profile Value Area boundaries.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.divergence_engine.utils import WINDOWS


class CompositeVWAP:
    """Compute CWVAP and delivery-profile value-area bands.

    Prerequisite columns (from Modules 1-2):
    ``high, low, close, volume, delivery_qty, tp, atr_20,
    dvl_rate_10..120``.
    """

    def __init__(self, windows: list[int] | None = None, va_pct: float = 0.70) -> None:
        self.windows = windows or WINDOWS
        self.va_pct = va_pct

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add all Module 3 columns and return *df*."""
        # 3.1 Per-window DVWAP
        for n in self.windows:
            df = self._compute_dvwap(df, n)

        # 3.2 Per-window POC
        for n in self.windows:
            df = self._compute_poc(df, n)

        # 3.3 CWVAP — Composite Weighted VWAP
        df = self._compute_cwvap(df)

        # 3.4 Delivery-Profile Value Area (va_high / va_low)
        df = self._compute_composite_va(df)

        return df

    # ------------------------------------------------------------------
    # 3.1 Individual Anchor DVWAP
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_dvwap(df: pd.DataFrame, n: int) -> pd.DataFrame:
        """DVWAP_n = rolling_sum(TP × volume, n) / rolling_sum(volume, n).

        Note: uses *total volume* for VWAP (price discovery), not delivery_qty.
        """
        tp_vol = df["tp"] * df["volume"]
        numerator = tp_vol.rolling(window=n, min_periods=1).sum()
        denominator = df["volume"].rolling(window=n, min_periods=1).sum()

        df[f"dvwap_{n}"] = np.where(denominator > 0, numerator / denominator, df["close"])
        return df

    # ------------------------------------------------------------------
    # 3.2 Individual Anchor POC
    # ------------------------------------------------------------------

    def _compute_poc(self, df: pd.DataFrame, n: int) -> pd.DataFrame:
        """POC_n = midpoint of the price bin with highest cumulative delivery.

        Bins are ATR_20/4 wide, delivery-weighted.
        Also computes per-window VA boundaries (va_high_n, va_low_n) using
        standard TPO Value Area logic: expand from POC bin until accumulated
        delivery >= va_pct of total.
        """
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        delivery = df["delivery_qty"].values
        atr = df["atr_20"].values

        length = len(df)
        poc_values = np.full(length, np.nan)
        va_high_values = np.full(length, np.nan)
        va_low_values = np.full(length, np.nan)

        for i in range(n - 1, length):
            start = max(0, i - n + 1)
            window_high = high[start : i + 1]
            window_low = low[start : i + 1]
            window_delivery = delivery[start : i + 1]
            current_atr = atr[i]

            if np.isnan(current_atr) or current_atr <= 0:
                poc_values[i] = close[i]
                continue

            bin_width = current_atr / 4.0
            if bin_width <= 0:
                poc_values[i] = close[i]
                continue

            price_min = np.min(window_low)
            price_max = np.max(window_high)

            if price_min == price_max:
                poc_values[i] = price_min
                continue

            n_bins = max(int(np.ceil((price_max - price_min) / bin_width)), 1)
            # Cap bins to prevent memory issues on extreme ranges
            n_bins = min(n_bins, 200)

            bin_edges = np.linspace(price_min, price_max, n_bins + 1)
            bin_mids = (bin_edges[:-1] + bin_edges[1:]) / 2.0

            # Assign each bar's delivery to the bin containing its close
            window_close = close[start : i + 1]
            bin_indices = np.clip(
                np.digitize(window_close, bin_edges) - 1, 0, n_bins - 1
            )
            bin_delivery = np.bincount(bin_indices, weights=window_delivery, minlength=n_bins)

            poc_bin = int(np.argmax(bin_delivery))
            poc_values[i] = bin_mids[poc_bin]

            # --- Value Area boundaries (TPO expansion from POC) ---
            total_delivery = bin_delivery.sum()
            if total_delivery <= 0:
                continue

            accumulated = bin_delivery[poc_bin]
            lo_idx = poc_bin
            hi_idx = poc_bin
            target = self.va_pct * total_delivery

            while accumulated < target:
                look_above = bin_delivery[hi_idx + 1] if hi_idx + 1 < n_bins else -1.0
                look_below = bin_delivery[lo_idx - 1] if lo_idx - 1 >= 0 else -1.0

                if look_above < 0 and look_below < 0:
                    break  # both sides exhausted

                if look_above < 0:
                    lo_idx -= 1
                    accumulated += bin_delivery[lo_idx]
                elif look_below < 0:
                    hi_idx += 1
                    accumulated += bin_delivery[hi_idx]
                elif look_below >= look_above:
                    # tie or below wins → go lower
                    lo_idx -= 1
                    accumulated += bin_delivery[lo_idx]
                else:
                    hi_idx += 1
                    accumulated += bin_delivery[hi_idx]

            va_low_values[i] = bin_edges[lo_idx]
            va_high_values[i] = bin_edges[hi_idx + 1]

        df[f"poc_{n}"] = poc_values
        df[f"va_high_{n}"] = va_high_values
        df[f"va_low_{n}"] = va_low_values
        return df

    # ------------------------------------------------------------------
    # 3.3 CWVAP — Composite Weighted VWAP
    # ------------------------------------------------------------------

    def _compute_cwvap(self, df: pd.DataFrame) -> pd.DataFrame:
        """CWVAP = Σ(DVWAP_n × W_n) / Σ(W_n).

        Weight_n = DVL_rate_n × (1 / (1 + |close − DVWAP_n| / ATR_20)).
        """
        close = df["close"]
        atr = df["atr_20"]

        numerator = pd.Series(0.0, index=df.index)
        denominator = pd.Series(0.0, index=df.index)

        for n in self.windows:
            dvwap = df[f"dvwap_{n}"]
            dvl_rate = df[f"dvl_rate_{n}"]
            distance = (close - dvwap).abs()

            weight = dvl_rate * (1.0 / (1.0 + np.where(atr > 0, distance / atr, 0.0)))

            valid = dvwap.notna()
            numerator += (dvwap * weight).fillna(0)
            denominator += weight.where(valid, 0)

        df["cwvap"] = np.where(denominator > 0, numerator / denominator, close)

        # CWVAP slope: linear regression over last 10 bars
        cwvap_vals = df["cwvap"].values.astype(float)
        slopes = np.full(len(cwvap_vals), np.nan)
        x = np.arange(10, dtype=float)
        for i in range(9, len(cwvap_vals)):
            y = cwvap_vals[i - 9 : i + 1]
            if not np.any(np.isnan(y)):
                coeffs = np.polyfit(x, y, 1)
                slopes[i] = coeffs[0]

        df["cwvap_slope"] = slopes
        df["cwvap_slope_norm"] = np.where(atr > 0, df["cwvap_slope"] / atr, 0.0)

        return df

    # ------------------------------------------------------------------
    # 3.4 Delivery-Profile Composite Value Area
    # ------------------------------------------------------------------

    def _compute_composite_va(self, df: pd.DataFrame) -> pd.DataFrame:
        """DVL-rate weighted average of per-window VA boundaries.

        va_high = Σ(va_high_n × dvl_rate_n) / Σ(dvl_rate_n)
        va_low  = Σ(va_low_n  × dvl_rate_n) / Σ(dvl_rate_n)
        va_profile_width = (va_high - va_low) / atr_20
        """
        atr = df["atr_20"]

        num_high = pd.Series(0.0, index=df.index)
        num_low = pd.Series(0.0, index=df.index)
        denom = pd.Series(0.0, index=df.index)

        for n in self.windows:
            va_h = df[f"va_high_{n}"]
            va_l = df[f"va_low_{n}"]
            dvl_rate = df[f"dvl_rate_{n}"]

            valid = va_h.notna() & va_l.notna()
            w = dvl_rate.where(valid, 0.0)
            num_high += (va_h * w).fillna(0)
            num_low += (va_l * w).fillna(0)
            denom += w

        df["va_high"] = np.where(denom > 0, num_high / denom, np.nan)
        df["va_low"] = np.where(denom > 0, num_low / denom, np.nan)
        df["va_profile_width"] = np.where(
            (denom > 0) & (atr > 0),
            (df["va_high"] - df["va_low"]) / atr,
            0.0,
        )
        return df

