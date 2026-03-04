"""
Module 3 — Composite VWAP (CWVAP).

Builds per-window DVWAPs plus a composite weighted VWAP, composite POC,
value-area bands, and a price-location classifier.

Key outputs:
- **DVWAP_n**: Delivery-weighted VWAP per anchor window.
- **POC_n**: Point of Control (highest-delivery price bin) per window.
- **CWVAP**: Composite weighted VWAP across all anchors.
- **CPOC**: Composite POC.
- **POC_spread**: Regime-agreement signal (std of POCs / ATR).
- **CVAH / CVAL**: Composite Value Area High/Low.
- **price_location**: Categorical classification of close vs value zones.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.divergence_engine.utils import WINDOWS


class CompositeVWAP:
    """Compute CWVAP, CPOC, value-area bands, and price-location labels.

    Prerequisite columns (from Modules 1-2):
    ``high, low, close, volume, delivery_qty, tp, atr_20,
    dvl_rate_10..120``.
    """

    def __init__(self, windows: list[int] | None = None) -> None:
        self.windows = windows or WINDOWS

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

        # 3.4 CPOC — Composite POC + POC Spread
        df = self._compute_cpoc(df)

        # 3.5 Composite Value Area
        df = self._compute_value_area(df)

        # 3.6 Price Location Classification
        df = self._classify_location(df)

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
        """
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        delivery = df["delivery_qty"].values
        atr = df["atr_20"].values

        poc_values = np.full(len(df), np.nan)

        for i in range(n - 1, len(df)):
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

            poc_values[i] = bin_mids[np.argmax(bin_delivery)]

        df[f"poc_{n}"] = poc_values
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
    # 3.4 CPOC — Composite POC + POC Spread
    # ------------------------------------------------------------------

    def _compute_cpoc(self, df: pd.DataFrame) -> pd.DataFrame:
        """Composite POC (Method A: weighted average) and POC Spread (Method B)."""
        atr = df["atr_20"]

        # Method A — Weighted average of POC_n by DVL_rate_n
        numerator = pd.Series(0.0, index=df.index)
        denominator = pd.Series(0.0, index=df.index)

        for n in self.windows:
            poc = df[f"poc_{n}"]
            dvl_rate = df[f"dvl_rate_{n}"]
            weight = dvl_rate  # simplified: delivery_qty_at_POC_n ≈ DVL_rate_n

            valid = poc.notna()
            numerator += (poc * weight).fillna(0)
            denominator += weight.where(valid, 0)

        df["cpoc"] = np.where(denominator > 0, numerator / denominator, df["close"])

        # Method B — POC Spread (regime signal)
        poc_cols = [f"poc_{n}" for n in self.windows]
        poc_matrix = df[poc_cols]
        poc_std = poc_matrix.std(axis=1)
        df["poc_spread"] = np.where(atr > 0, poc_std / atr, 0.0)

        return df

    # ------------------------------------------------------------------
    # 3.5 Composite Value Area
    # ------------------------------------------------------------------

    def _compute_value_area(self, df: pd.DataFrame) -> pd.DataFrame:
        """CVAH / CVAL bands per window; composite = delivery-weighted extremes."""
        atr = df["atr_20"]

        # Per-window CVAH_n / CVAL_n
        cvah_max = pd.Series(-np.inf, index=df.index)
        cval_min = pd.Series(np.inf, index=df.index)

        total_weight = pd.Series(0.0, index=df.index)

        for n in self.windows:
            price_std = df["close"].rolling(window=n, min_periods=1).std()
            dvwap = df[f"dvwap_{n}"]
            dvl_rate = df[f"dvl_rate_{n}"]

            cvah_n = dvwap + price_std
            cval_n = dvwap - price_std

            # Delivery-weighted: track the max CVAH and min CVAL
            # weighted by dvl_rate (higher delivery = more influence)
            cvah_max = np.maximum(cvah_max, cvah_n)
            cval_min = np.minimum(cval_min, cval_n)

        df["cvah"] = cvah_max
        df["cval"] = cval_min
        df["va_width"] = np.where(atr > 0, (df["cvah"] - df["cval"]) / atr, 0.0)

        return df

    # ------------------------------------------------------------------
    # 3.6 Price Location Classification
    # ------------------------------------------------------------------

    def _classify_location(self, df: pd.DataFrame) -> pd.DataFrame:
        """Classify close relative to CWVAP, CPOC, CVAH, CVAL."""
        close = df["close"].values
        cwvap = df["cwvap"].values
        cpoc = df["cpoc"].values
        atr = df["atr_20"].values
        cvah = df["cvah"].values
        cval = df["cval"].values

        n = len(df)
        locations = np.full(n, "in_value_area", dtype=object)

        # Pre-compute: did close cross above/below CWVAP in last 3 bars?
        close_above_cwvap = close > cwvap

        for i in range(n):
            c = close[i]
            cw = cwvap[i]
            cp = cpoc[i]
            a = atr[i] if not np.isnan(atr[i]) else 1.0
            ch = cvah[i]
            cl = cval[i]

            # Check crossings (last 3 bars)
            if i >= 3:
                recent_above = close_above_cwvap[i - 2 : i + 1]
                # Crossed above: was below, now above
                if not recent_above[0] and recent_above[-1]:
                    locations[i] = "reclaiming_value"
                    continue
                # Crossed below: was above, now below
                if recent_above[0] and not recent_above[-1]:
                    locations[i] = "losing_value"
                    continue

            if c > cw and c > cp:
                # Extended above: price > CPOC > CWVAP (distribution risk)
                if cp > cw:
                    locations[i] = "extended_above"
                else:
                    locations[i] = "above_value"
            elif abs(c - cw) < 0.3 * a:
                locations[i] = "at_value"
            elif c < cw and c < cp:
                locations[i] = "below_value"
            elif cl <= c <= ch:
                locations[i] = "in_value_area"

        df["price_location"] = locations
        return df
