"""
Module 7 — State Classifier.

Classifies each bar into one of 6 market states using rule-based weighted
evidence scoring, then normalises scores into a probability distribution.

States:
  UPTREND, DOWNTREND, ACCUMULATION, DISTRIBUTION, SIDEWAYS, RECOVERY

Output per bar:
  ``{ state, confidence, probabilities: {…} }``
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# State labels in canonical order
STATES = [
    "UPTREND",
    "DOWNTREND",
    "ACCUMULATION",
    "DISTRIBUTION",
    "SIDEWAYS",
    "RECOVERY",
]


class StateClassifier:
    """Score and classify each bar into a market state.

    Prerequisite columns (from all prior modules):
    ``gradient_shape, cwc, cwc_delta, mcs_composite, mcs_delta,
    poc_spread, price_location, cwvap_slope_norm,
    divergence_direction, divergence_probability,
    ars_10..120, dvl_rate_10..120, pdd_10..120,
    c_10_30, c_60_120, va_width``.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add state classification columns to *df*.

        Added columns:
        - ``state``: predicted market state label.
        - ``state_confidence``: confidence (probability of winning state).
        - ``prob_UPTREND .. prob_RECOVERY``: per-state probabilities.
        """
        # Pre-compute weighted averages needed by scoring
        df = self._weighted_averages(df)

        n = len(df)
        score_matrix = np.zeros((n, len(STATES)))

        for i in range(n):
            row = df.iloc[i]
            for s_idx, state in enumerate(STATES):
                score_matrix[i, s_idx] = self._score_state(state, row)

        # Probability normalisation (softmax-style: proportional)
        # Add small epsilon to avoid division by zero
        row_totals = score_matrix.sum(axis=1, keepdims=True)
        row_totals = np.where(row_totals > 0, row_totals, 1e-9)
        prob_matrix = score_matrix / row_totals

        # Assign to df
        for s_idx, state in enumerate(STATES):
            df[f"prob_{state}"] = prob_matrix[:, s_idx]

        best_idx = np.argmax(prob_matrix, axis=1)
        df["state"] = [STATES[i] for i in best_idx]
        df["state_confidence"] = np.max(prob_matrix, axis=1)

        return df

    # ------------------------------------------------------------------
    # Weighted averages helper
    # ------------------------------------------------------------------

    @staticmethod
    def _weighted_averages(df: pd.DataFrame) -> pd.DataFrame:
        """Compute DVL_rate-weighted averages of ARS and PDD."""
        from src.divergence_engine.utils import WINDOWS

        ars_num = pd.Series(0.0, index=df.index)
        ars_den = pd.Series(0.0, index=df.index)

        for n in WINDOWS:
            w = df[f"dvl_rate_{n}"].fillna(0)
            ars_num += df[f"ars_{n}"].fillna(1.0) * w
            ars_den += w

        df["ars_weighted_avg"] = np.where(ars_den > 0, ars_num / ars_den, 1.0)

        # PDD weighted avg already computed in divergence.py
        # but ensure it exists
        if "pdd_weighted_avg" not in df.columns:
            pdd_num = pd.Series(0.0, index=df.index)
            pdd_den = pd.Series(0.0, index=df.index)
            for n in WINDOWS:
                w = df[f"dvl_rate_{n}"].fillna(0)
                pdd_num += df[f"pdd_{n}"].fillna(0) * w
                pdd_den += w
            df["pdd_weighted_avg"] = np.where(pdd_den > 0, pdd_num / pdd_den, 0.0)

        return df

    # ------------------------------------------------------------------
    # State scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _score_state(state: str, row: pd.Series) -> float:
        """Compute the evidence score for a single state on a single bar.

        Uses the exact weighted-evidence rules from the specification.
        Safely handles NaN values by defaulting to neutral.
        """

        def _safe(val, default=0.0):
            """Return *default* if *val* is NaN or missing."""
            try:
                if pd.isna(val):
                    return default
            except (TypeError, ValueError):
                pass
            return val

        grad = _safe(row.get("gradient_shape", ""), "")
        cwc = _safe(row.get("cwc", 0.0))
        cwc_delta = _safe(row.get("cwc_delta", 0.0))
        mcs = _safe(row.get("mcs_composite", 0.0))
        mcs_delta = _safe(row.get("mcs_delta", 0.0))
        poc_spread = _safe(row.get("poc_spread", 0.0))
        ploc = _safe(row.get("price_location", ""), "")
        cwvap_slope = _safe(row.get("cwvap_slope_norm", 0.0))
        div_dir = _safe(row.get("divergence_direction", "none"), "none")
        div_prob = _safe(row.get("divergence_probability", 0.0))
        ars_avg = _safe(row.get("ars_weighted_avg", 1.0))
        pdd_avg = _safe(row.get("pdd_weighted_avg", 0.0))
        c_10_30 = _safe(row.get("c_10_30", 0.5))
        c_60_120 = _safe(row.get("c_60_120", 0.5))
        va_width = _safe(row.get("va_width", 2.0))

        score = 0.0

        if state == "UPTREND":
            if grad in ("uptrend_mature", "uptrend_forming"):
                score += 2.0
            if cwc > 0.6:
                score += 1.5
            if mcs > 0.5:
                score += 1.5
            if ploc == "above_value":
                score += 1.0
            if cwvap_slope > 0.3:
                score += 1.0
            if ars_avg > 1.1:
                score += 0.5
            if div_dir == "bearish" and div_prob > 0.6:
                score -= 1.0

        elif state == "DOWNTREND":
            if grad in ("downtrend_mature", "downtrend_forming"):
                score += 2.0
            if cwc > 0.6:
                score += 1.5
            if mcs < -0.5:
                score += 1.5
            if ploc == "below_value":
                score += 1.0
            if cwvap_slope < -0.3:
                score += 1.0
            if div_dir == "bullish" and div_prob > 0.6:
                score -= 1.0

        elif state == "ACCUMULATION":
            if grad == "accumulation":
                score += 2.0
            if div_dir == "bullish" and div_prob > 0.5:
                score += 1.5
            if pdd_avg < -0.5:
                score += 1.5
            if ploc in ("below_value", "at_value"):
                score += 1.0
            if -0.2 <= mcs <= 0.4:
                score += 1.0
            if c_10_30 < 0.3 and c_60_120 > 0.3:
                score += 0.5

        elif state == "DISTRIBUTION":
            if grad == "distribution":
                score += 2.0
            if div_dir == "bearish" and div_prob > 0.5:
                score += 1.5
            if pdd_avg > 0.5:
                score += 1.5
            if ploc in ("extended_above", "above_value"):
                score += 1.0
            if -0.4 <= mcs <= 0.2 and cwvap_slope < 0.1:
                score += 1.0
            if c_10_30 < 0.3 and c_60_120 > 0.3:
                score += 0.5

        elif state == "SIDEWAYS":
            if grad == "sideways":
                score += 2.0
            if cwc < 0.25:
                score += 1.5
            if poc_spread < 0.5:
                score += 1.0
            if abs(mcs) < 0.2:
                score += 1.0
            if va_width < 1.5:
                score += 0.5

        elif state == "RECOVERY":
            if grad == "recovering":
                score += 2.0
            if div_dir == "bullish" and div_prob > 0.4:
                score += 1.5
            if ploc == "reclaiming_value":
                score += 1.0
            if cwc_delta > 0.1:
                score += 1.0
            if mcs_delta > 0.1:
                score += 0.5

        # Ensure non-negative (negative scores can occur from penalties)
        return max(score, 0.0)
