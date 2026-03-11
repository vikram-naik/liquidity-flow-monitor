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
    close_col:      str = "close",
    rdv_col:        str = "rdv",
    cwc_slope_col:  str = "cwc_slope",
    mcs_col:        str = "mcs_composite",
    mcs_slope_col:  str = "mcs_composite_slope",
    slope_window:   int = 10,
    psz_delta_windows: list[int] | None = None,
    rsz_delta_windows: list[int] | None = None,
) -> pd.DataFrame:
    """
    Trend Participation Engine — computes slopes and coherence.

    Outputs: price_slope_z, rdv_slope_z, coherence_raw, coherence

    Parameters
    ----------
    psz_delta_windows : list[int]
        Rolling diff periods for PSZ delta (default [3]).
    rsz_delta_windows : list[int]
        Rolling diff periods for RSZ delta (default [3]).
    """
    df = df.copy()
    psz_delta_windows = psz_delta_windows or [3]
    rsz_delta_windows = rsz_delta_windows or [3]
    col_map = {c.lower(): c for c in df.columns}
    def _resolve(name: str, required: bool = True):
        r = col_map.get(name.lower(), name)
        if required and r not in df.columns:
            raise ValueError(f"Column '{name}' not found. Available: {list(df.columns)}")
        return r if r in df.columns else None

    close_col = _resolve(close_col)
    rdv_col = _resolve(rdv_col)
    cwc_slope_col = _resolve(cwc_slope_col)
    mcs_col = _resolve(mcs_col)
    mcs_slope_col = _resolve(mcs_slope_col)

    df["price_slope_z"] = _rolling_slope_z(df[close_col], slope_window).round(4)
    df["rdv_slope_z"]   = _rolling_slope_z(df[rdv_col],   slope_window).round(4)

    for w in psz_delta_windows:
        df[f"psz_delta_{w}d"] = df["price_slope_z"].diff(w).fillna(0.0).round(4)
        
    for w in rsz_delta_windows:
        df[f"rsz_delta_{w}d"] = df["rdv_slope_z"].diff(w).fillna(0.0).round(4)

    p_a = _pillar_a_slope_alignment(df["price_slope_z"], df["rdv_slope_z"])
    p_b = _pillar_b_mcs_confirmation(df[mcs_col], df[mcs_slope_col], df["price_slope_z"])
    p_c = _pillar_c_cwc_coherence(df[cwc_slope_col])

    df["coherence_raw"] = (0.45 * p_a + 0.35 * p_b + 0.20 * p_c).apply(lambda v: round(_logistic(v), 3))
    df["coherence"] = df["coherence_raw"].ewm(span=EMA_SMOOTH_SPAN, adjust=False).mean().round(3)

    return df
