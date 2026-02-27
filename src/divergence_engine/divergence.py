"""
Module 6 — Divergence Engine.

Detects mismatches between price momentum and delivery participation
across multiple anchor windows.

Key outputs per bar:
- Per-window bullish/bearish divergence flags.
- **CWVAP divergence**: price momentum outrunning/lagging composite value.
- **MCS divergence**: money-flow crossover signals.
- **Divergence confluence score**: fraction of windows agreeing.
- **divergence_probability**: sigmoid-scored probability [0, 1].
- **divergence_direction**: ``'bullish'``, ``'bearish'``, or ``'none'``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.divergence_engine.utils import WINDOWS, sigmoid


class DivergenceDetector:
    """Identify and score divergence signals across anchor windows.

    Prerequisite columns (from Modules 1-5):
    ``close, atr_20, rdv, cwvap, mcs_composite, pdd_10..120,
    dvl_rate_10..120, cwc``.
    """

    def __init__(self, windows: list[int] | None = None) -> None:
        self.windows = windows or WINDOWS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add all divergence detection columns."""
        df = self._price_vs_rdv_divergence(df)
        df = self._cwvap_divergence(df)
        df = self._mcs_divergence(df)
        df = self._confluence(df)
        df = self._probability(df)
        return df

    # ------------------------------------------------------------------
    # 6.1 Price vs RDV Divergence
    # ------------------------------------------------------------------

    def _price_vs_rdv_divergence(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detect per-window sign mismatches between price and RDV momentum."""
        close = df["close"]
        atr = df["atr_20"]
        rdv = df["rdv"]

        for n in self.windows:
            price_mom = np.where(
                atr > 0, (close - close.shift(n)) / atr, 0.0
            )
            rdv_std = rdv.rolling(window=n, min_periods=1).std().replace(0, 1)
            rdv_mom = (rdv - rdv.shift(n)) / rdv_std

            # Bullish: price falling (< 0), delivery rising (> 0.5)
            bull = (price_mom < 0) & (rdv_mom > 0.5)
            # Bearish: price rising (> 0), delivery falling (< −0.5)
            bear = (price_mom > 0) & (rdv_mom < -0.5)

            df[f"div_bull_{n}"] = bull.astype(int)
            df[f"div_bear_{n}"] = bear.astype(int)

        return df

    # ------------------------------------------------------------------
    # 6.2 CWVAP Divergence
    # ------------------------------------------------------------------

    @staticmethod
    def _cwvap_divergence(df: pd.DataFrame) -> pd.DataFrame:
        """Price momentum vs CWVAP momentum over 10 bars."""
        atr = df["atr_20"]
        cwvap_mom = np.where(
            atr > 0, (df["cwvap"] - df["cwvap"].shift(10)) / atr, 0.0
        )
        price_mom = np.where(
            atr > 0, (df["close"] - df["close"].shift(10)) / atr, 0.0
        )
        df["cwvap_divergence"] = price_mom - cwvap_mom
        return df

    # ------------------------------------------------------------------
    # 6.3 MCS Divergence
    # ------------------------------------------------------------------

    @staticmethod
    def _mcs_divergence(df: pd.DataFrame) -> pd.DataFrame:
        """MCS crossover signals relative to price location vs CWVAP."""
        mcs = df["mcs_composite"]
        close = df["close"]
        cwvap = df["cwvap"]
        prev_mcs = mcs.shift(1)

        # Bearish: MCS crosses below −0.3 while price > CWVAP
        bearish = (mcs < -0.3) & (prev_mcs >= -0.3) & (close > cwvap)
        # Bullish: MCS crosses above +0.3 while price < CWVAP
        bullish = (mcs > 0.3) & (prev_mcs <= 0.3) & (close < cwvap)

        df["mcs_div_bull"] = bullish.astype(int)
        df["mcs_div_bear"] = bearish.astype(int)
        return df

    # ------------------------------------------------------------------
    # 6.4 Multi-Window Divergence Confluence
    # ------------------------------------------------------------------

    def _confluence(self, df: pd.DataFrame) -> pd.DataFrame:
        """Count how many windows agree on divergence direction."""
        bull_cols = [f"div_bull_{n}" for n in self.windows]
        bear_cols = [f"div_bear_{n}" for n in self.windows]

        bull_count = df[bull_cols].sum(axis=1)
        bear_count = df[bear_cols].sum(axis=1)

        df["div_count_bull"] = bull_count
        df["div_count_bear"] = bear_count
        df["div_confluence_score"] = np.maximum(bull_count, bear_count) / len(self.windows)

        df["divergence_direction"] = np.where(
            bull_count > bear_count,
            "bullish",
            np.where(bear_count > bull_count, "bearish", "none"),
        )
        return df

    # ------------------------------------------------------------------
    # 6.5 Divergence Probability Score
    # ------------------------------------------------------------------

    def _probability(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute sigmoid-scored divergence probability.

        Components (from spec):
            0.3 × confluence_score +
            0.3 × |MCS_composite| +
            0.2 × |PDD weighted avg| +
            0.2 × (1 − CWC)
        """
        confluence = df["div_confluence_score"]
        mcs_abs = df["mcs_composite"].abs()

        # PDD weighted average (weighted by DVL_rate)
        pdd_num = pd.Series(0.0, index=df.index)
        pdd_den = pd.Series(0.0, index=df.index)
        for n in self.windows:
            w = df[f"dvl_rate_{n}"]
            pdd_num += df[f"pdd_{n}"].fillna(0) * w
            pdd_den += w

        pdd_wavg = np.where(pdd_den > 0, pdd_num / pdd_den, 0.0)
        df["pdd_weighted_avg"] = pdd_wavg

        cwc = df["cwc"].fillna(0)

        raw = (
            0.3 * confluence
            + 0.3 * mcs_abs
            + 0.2 * np.abs(pdd_wavg)
            + 0.2 * (1.0 - cwc)
        )

        df["divergence_probability"] = sigmoid(raw.values)
        return df
