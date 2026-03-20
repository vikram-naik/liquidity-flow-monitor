"""
analysis.py
────────────────────────────────────────────────────────────────────────────
Trend Participation Engine — computes slope, coherence, and EMA-smoothed
coherence signals for the Divergence Engine pipeline.

Outputs per bar:
    price_slope_z     z-normalised rolling slope of close price
    rdv_slope_z       z-normalised rolling slope of RDV
    coherence_raw     three-pillar coherence score (logistic-scaled)
    coherence         EMA-smoothed coherence_raw
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from scipy.signal import savgol_coeffs, lfilter

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

EMA_SMOOTH_SPAN = 3     # EMA span applied to coherence


# ─────────────────────────────────────────────────────────────────────────────
# Core Maths
# ─────────────────────────────────────────────────────────────────────────────

def _linear_slope_z(arr: np.ndarray) -> float:
    """Z-normalised linear slope. Dimensionless — comparable across stocks/time."""
    if len(arr) < 2:
        return 0.0
    x     = np.arange(len(arr), dtype=float)
    slope = np.polyfit(x, arr, 1)[0]
    std   = arr.std()
    return float(slope / (std + 1e-10))


def _rolling_slope_z(series: pd.Series, window: int) -> pd.Series:
    return (
        series
        .rolling(window, min_periods=max(2, window // 2))
        .apply(_linear_slope_z, raw=True)
        .fillna(0.0)
    )


def _logistic(x: float, k: float = 8.0, mid: float = 0.5) -> float:
    return 1.0 / (1.0 + np.exp(-k * (x - mid)))


# ─────────────────────────────────────────────────────────────────────────────
# Three-Pillar Coherence Score
# ─────────────────────────────────────────────────────────────────────────────

def _pillar_a_slope_alignment(
    price_slope_z: pd.Series,
    rdv_slope_z:   pd.Series,
) -> pd.Series:
    """
    A (0.45) — Both slopes strong AND aligned.
    Geometric mean of percentile ranks + directional agreement bonus.
    """
    p_rank    = price_slope_z.abs().rank(pct=True)
    r_rank    = rdv_slope_z.abs().rank(pct=True)
    agreement = np.sign(price_slope_z) * np.sign(rdv_slope_z)
    raw       = (p_rank * r_rank) ** 0.5
    return (raw + 0.2 * agreement).clip(0, 1).rename("pillar_a")


def _pillar_b_mcs_confirmation(
    mcs_composite:       pd.Series,
    mcs_composite_slope: pd.Series,
    price_slope_z:       pd.Series,
) -> pd.Series:
    """
    B (0.35) — MCS_composite level + slope confirms price direction.
    Level weight 0.6, slope weight 0.4.
    """
    mcs_norm   = (mcs_composite.clip(-1, 1) + 1) / 2
    mcs_s_norm = (mcs_composite_slope.clip(-1, 1) + 1) / 2
    price_dir  = (price_slope_z > 0).astype(int)

    level_conf = np.where(price_dir == 1, mcs_norm,   1 - mcs_norm)
    slope_conf = np.where(price_dir == 1, mcs_s_norm, 1 - mcs_s_norm)

    return pd.Series(
        0.6 * level_conf + 0.4 * slope_conf,
        index=mcs_composite.index,
        name="pillar_b",
    )


def _pillar_c_cwc_coherence(cwc_slope: pd.Series) -> pd.Series:
    """C (0.20) — CWC structural delivery consistency."""
    return cwc_slope.abs().rank(pct=True).rename("pillar_c")


# ─────────────────────────────────────────────────────────────────────────────
# Main Analysis Function
# ─────────────────────────────────────────────────────────────────────────────

def compute_trend_participation(
    df:             pd.DataFrame,
    price_col:      str = "tp",
    rdv_col:        str = "rdv",
    cwc_slope_col:  str = "cwc_slope",
    mcs_col:        str = "mcs_composite",
    mcs_slope_col:  str = "mcs_composite_slope",
    slope_window:   int = 10,
    psz_threshold_window: int = 60,
    psz_buy_pct:    float = 10.0,
    psz_sell_pct:   float = 70.0,
) -> pd.DataFrame:
    """
    Trend Participation Engine — computes slopes and coherence.

    Outputs: price_slope_z, rdv_slope_z, psz_buy_threshold, psz_sell_threshold,
             coherence_raw, coherence, accum_div, distrib_div
    """
    df = df.copy()
    col_map = {c.lower(): c for c in df.columns}
    def _resolve(name: str, required: bool = True):
        r = col_map.get(name.lower(), name)
        if required and r not in df.columns:
            raise ValueError(f"Column '{name}' not found. Available: {list(df.columns)}")
        return r if r in df.columns else None

    price_col = _resolve(price_col)
    rdv_col = _resolve(rdv_col)
    cwc_slope_col = _resolve(cwc_slope_col)
    mcs_col = _resolve(mcs_col)
    mcs_slope_col = _resolve(mcs_slope_col)

    df["price_slope_z"] = _rolling_slope_z(df[price_col], slope_window).round(4)
    df["rdv_slope_z"]   = _rolling_slope_z(df[rdv_col],   slope_window).round(4)

    # Adaptive PSZ Thresholds — rolling percentiles of the stock's own PSZ distribution
    psz_series = df["price_slope_z"]
    min_periods = max(30, psz_threshold_window // 2)
    df["psz_buy_threshold"] = psz_series.rolling(
        window=psz_threshold_window, min_periods=min_periods
    ).quantile(psz_buy_pct / 100.0).fillna(0.0).round(4)
    df["psz_sell_threshold"] = psz_series.rolling(
        window=psz_threshold_window, min_periods=min_periods
    ).quantile(psz_sell_pct / 100.0).fillna(0.0).round(4)

    # Causal Savgol-Filtered PSZ (Normalized Acceleration)
    # Uses one-sided (causal) FIR coefficients so psz_smooth and psz_v
    # only depend on past data — valid for walk-forward / live execution.
    sg_win = 11
    sg_poly = 2
    if len(psz_series) >= sg_win:
        psz_vals = psz_series.values.astype(float)
        coeffs_smooth = savgol_coeffs(sg_win, sg_poly, deriv=0, pos=sg_win - 1)
        coeffs_vel    = savgol_coeffs(sg_win, sg_poly, deriv=1, pos=sg_win - 1)

        psz_smooth_raw = lfilter(coeffs_smooth, [1.0], psz_vals)
        psz_v_raw      = lfilter(coeffs_vel,    [1.0], psz_vals)

        # Blank the warm-up period (first sg_win-1 bars are unreliable)
        warmup = sg_win - 1
        psz_smooth_raw[:warmup] = np.nan
        psz_v_raw[:warmup]      = np.nan

        df["psz_smooth"] = psz_smooth_raw.round(4)
        df["psz_v"]      = psz_v_raw.round(6)
    else:
        df["psz_smooth"] = np.nan
        df["psz_v"]      = np.nan

    # Accumulation/Distribution Divergence features
    # accum_div: positive when price is falling AND delivery is rising
    # distrib_div: positive when price is rising AND delivery is falling
    psz = df["price_slope_z"]
    rsz = df["rdv_slope_z"]
    df["accum_div"] = (rsz.clip(lower=0) * (-psz).clip(lower=0)).round(4)
    df["distrib_div"] = ((-rsz).clip(lower=0) * psz.clip(lower=0)).round(4)

    p_a = _pillar_a_slope_alignment(df["price_slope_z"], df["rdv_slope_z"])
    p_b = _pillar_b_mcs_confirmation(df[mcs_col], df[mcs_slope_col], df["price_slope_z"])
    p_c = _pillar_c_cwc_coherence(df[cwc_slope_col])

    df["coherence_raw"] = (0.45 * p_a + 0.35 * p_b + 0.20 * p_c).apply(lambda v: round(_logistic(v), 3))
    df["coherence"] = df["coherence_raw"].ewm(span=EMA_SMOOTH_SPAN, adjust=False).mean().round(3)

    # Adaptive CTS Thresholds — rolling percentiles of the stock's own CTS distribution
    # This targets the CWVAP-based CTS indicator.
    if "cts" in df.columns:
        cts_series = df["cts"]
        df["cts_buy_threshold"] = cts_series.rolling(
            window=psz_threshold_window, min_periods=min_periods
        ).quantile(0.10).fillna(0.0).round(4)
        df["cts_sell_threshold"] = cts_series.rolling(
            window=psz_threshold_window, min_periods=min_periods
        ).quantile(0.90).fillna(0.0).round(4)

    if "cts_accel" in df.columns:
        df["cts_accel_threshold"] = df["cts_accel"].rolling(
            window=psz_threshold_window, min_periods=min_periods
        ).quantile(0.70).fillna(0.0).round(6)

    if "pdd_120" in df.columns:
        df["pdd_120_threshold"] = df["pdd_120"].rolling(
            window=psz_threshold_window, min_periods=min_periods
        ).quantile(0.70).fillna(0.0).round(4)

    if "psz_v" in df.columns:
        df["psz_v_extreme_threshold"] = df["psz_v"].abs().rolling(
            window=psz_threshold_window, min_periods=min_periods
        ).quantile(0.90).fillna(0.0).round(4)

    return df
