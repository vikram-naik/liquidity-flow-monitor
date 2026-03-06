"""
Feature Engineer — Step 2 of the XGBoost Divergence Engine pipeline.

Adds lookback features to a divergence engine ledger DataFrame, flattening
the trajectory of key indicators into each row so XGBoost can learn the
setup pattern.

Usage::

    from src.divergence_engine.engine import DivergenceEngine
    from src.feature_engineer import engineer_features

    engine = DivergenceEngine(ticker='RELIANCE')
    result = engine.run()

    df = engineer_features(result.ledger)
    # df now has 26 additional feature columns
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.divergence_engine.utils import WINDOWS


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add lookback features to a divergence engine ledger.

    Expects the DataFrame to already contain columns from the
    engine pipeline (cwvap, rdv, coherence, mfm, pdd_{n}, dvl_rate_{n}).

    Parameters
    ----------
    df : pd.DataFrame
        Ledger output from ``DivergenceEngine.run().ledger``.

    Returns
    -------
    pd.DataFrame
        Same DataFrame with 26 additional feature columns appended.
    """
    df = df.copy()
    _check_expected_columns(df)
    df = _cwvap_features(df)
    df = _rdv_features(df)
    df = _coherence_features(df)
    df = _mfm_features(df)
    df = _pdd_weighted_avg(df)
    df = _alignment_features(df)
    return df


# ──────────────────────────────────────────────────────────────────────────────
# Input Validation
# ──────────────────────────────────────────────────────────────────────────────

def _check_expected_columns(df: pd.DataFrame) -> None:
    """Validate that the DataFrame has the columns we need."""
    required = ["cwvap", "rdv", "coherence", "mfm"]
    recommended = ["cwvap_dist", "cpoc_dist", "c_10_30", "c_30_60", "c_60_120"]

    missing_required = [c for c in required if c not in df.columns]
    missing_recommended = [c for c in recommended if c not in df.columns]

    if missing_required:
        raise ValueError(
            f"engineer_features: missing required columns: {missing_required}"
        )
    if missing_recommended:
        import warnings
        warnings.warn(
            f"engineer_features: recommended columns not found (will be absent "
            f"from feature matrix): {missing_recommended}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# CWVAP Features (7 columns)
# ──────────────────────────────────────────────────────────────────────────────

def _cwvap_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute CWVAP lags, slopes, and acceleration."""
    cwvap = df["cwvap"]

    # Lags
    df["cwvap_lag_3d"] = cwvap.shift(3)
    df["cwvap_lag_5d"] = cwvap.shift(5)
    df["cwvap_lag_10d"] = cwvap.shift(10)

    # Slopes (simple difference quotient)
    df["cwvap_slope_3d"] = (cwvap - cwvap.shift(3)) / 3
    df["cwvap_slope_5d"] = (cwvap - cwvap.shift(5)) / 5
    df["cwvap_slope_10d"] = (cwvap - cwvap.shift(10)) / 10

    # Acceleration (is the slope steepening?)
    df["cwvap_acceleration"] = df["cwvap_slope_3d"] - df["cwvap_slope_10d"]

    return df


# ──────────────────────────────────────────────────────────────────────────────
# RDV Features (4 columns)
# ──────────────────────────────────────────────────────────────────────────────

def _rdv_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute RDV slopes and consistency."""
    rdv = df["rdv"]

    # Slopes at 3 timeframes
    df["rdv_slope_3d"] = (rdv - rdv.shift(3)) / 3
    df["rdv_slope_5d"] = (rdv - rdv.shift(5)) / 5
    df["rdv_slope_10d"] = (rdv - rdv.shift(10)) / 10

    # Consistency: count of days in last 5 where RDV > 1.0 (above average)
    df["rdv_consistency"] = (rdv > 1.0).astype(float).rolling(5, min_periods=5).sum()

    return df


# ──────────────────────────────────────────────────────────────────────────────
# Coherence Features (4 columns)
# ──────────────────────────────────────────────────────────────────────────────

def _coherence_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute coherence trend slopes and volatility.

    All trend slopes use simple difference quotients for consistency:
    coherence_trend_{n}d = (coherence - coherence.shift(n)) / n
    """
    coh = df["coherence"]

    # Trend slopes (simple difference quotient, all three consistent)
    df["coherence_trend_3d"] = (coh - coh.shift(3)) / 3
    df["coherence_trend_5d"] = (coh - coh.shift(5)) / 5
    df["coherence_trend_10d"] = (coh - coh.shift(10)) / 10

    # Volatility: erratic = noise, smooth = institutional
    df["coherence_volatility"] = coh.rolling(10, min_periods=5).std()

    return df


# ──────────────────────────────────────────────────────────────────────────────
# MFM Features (4 columns)
# ──────────────────────────────────────────────────────────────────────────────

def _mfm_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute MFM slopes, ratio to average, and acceleration."""
    mfm = df["mfm"]

    # Slopes
    df["mfm_slope_3d"] = (mfm - mfm.shift(3)) / 3
    df["mfm_slope_10d"] = (mfm - mfm.shift(10)) / 10

    # MFM relative to its own 10-day mean
    mfm_10d_avg = mfm.rolling(10, min_periods=5).mean()
    # Clip to [-10, 10] to prevent outliers when avg is near-zero
    raw_ratio = mfm / mfm_10d_avg.replace(0, np.nan)
    df["mfm_vs_10d_avg"] = raw_ratio.clip(-10, 10).fillna(0.0)

    # Acceleration
    df["mfm_acceleration"] = df["mfm_slope_3d"] - df["mfm_slope_10d"]

    return df


# ──────────────────────────────────────────────────────────────────────────────
# PDD Weighted Average (1 column)
# ──────────────────────────────────────────────────────────────────────────────

def _pdd_weighted_avg(df: pd.DataFrame) -> pd.DataFrame:
    """Compute DVL-rate-weighted average of per-window PDD.

    Ported from deprecated/divergence.py:_probability.
    Positive = price outrunning delivery; negative = delivery building.
    """
    pdd_num = pd.Series(0.0, index=df.index)
    pdd_den = pd.Series(0.0, index=df.index)

    for n in WINDOWS:
        w = df[f"dvl_rate_{n}"].fillna(0)
        pdd_num += df[f"pdd_{n}"].fillna(0) * w
        pdd_den += w

    df["pdd_weighted_avg"] = np.where(pdd_den > 0, pdd_num / pdd_den, 0.0)
    return df


# ──────────────────────────────────────────────────────────────────────────────
# Cross-Indicator Alignment (6 columns)
# ──────────────────────────────────────────────────────────────────────────────

def _alignment_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute multi-timeframe alignment flags and setup duration counters.

    The institutional fingerprint: CWVAP rising, RDV rising, coherence
    falling — all three must agree for alignment = True.
    """
    for period in (3, 5, 10):
        cwvap_col = f"cwvap_slope_{period}d"
        rdv_col = f"rdv_slope_{period}d"
        coh_col = f"coherence_trend_{period}d"

        aligned = (
            (df[cwvap_col] > 0)
            & (df[rdv_col] > 0)
            & (df[coh_col] < 0)
        )

        align_col = f"all_aligned_{period}d"
        duration_col = f"setup_duration_{period}d"

        df[align_col] = aligned.astype(int)
        df[duration_col] = _consecutive_count(aligned)

    return df


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _rolling_slope(series: pd.Series, window: int = 5) -> pd.Series:
    """Compute rolling linear regression slope over *window* bars.

    Uses numpy polyfit for accuracy. Returns NaN for the warmup period.
    """
    result = np.full(len(series), np.nan)
    values = series.values

    for i in range(window - 1, len(values)):
        chunk = values[i - window + 1 : i + 1]
        if np.any(np.isnan(chunk)):
            continue
        x = np.arange(window, dtype=float)
        slope = np.polyfit(x, chunk, 1)[0]
        result[i] = slope

    return pd.Series(result, index=series.index)


def _consecutive_count(mask: pd.Series) -> pd.Series:
    """Count consecutive True values, resetting on False.

    Example: [F, T, T, T, F, T, T] → [0, 1, 2, 3, 0, 1, 2]
    """
    # Group by streaks of True/False
    groups = (~mask).cumsum()
    # Within each True streak, cumcount gives 0,1,2,...
    # We want 1-indexed counting for True, 0 for False
    counts = mask.groupby(groups).cumsum()
    return counts.astype(int)
