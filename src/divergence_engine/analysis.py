"""
analysis.py
────────────────────────────────────────────────────────────────────────────
Trend Participation Engine v5.2 implementation for main pipeline integration.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

TRAJECTORY_BARS = 5     # delta window for score_slope
EMA_SMOOTH_SPAN = 3     # EMA span applied to coherence
DEADBAND_PCTILE = 0.25  # auto-calibrated regime threshold percentile

# Divergence flag fires when both slopes exceed this threshold in opposite dirs
DIV_THRESHOLD   = 0.10

# Persistence: div_conf persistence weight caps at this many bars
DIV_PERSIST_CAP = 8

# Composite Pressure Score (CPS) — stealth accumulation / distribution detector
CPS_WINDOW        = 5      # rolling window (bars) for sub-threshold evidence
CPS_TRAILING_BARS = 30     # trailing window for dynamic threshold calibration
CPS_PERCENTILE         = 0.75   # onset: upper-quartile fires a new stealth signal
CPS_SUSTAIN_PERCENTILE = 0.40   # sustain: active signal holds until CPS falls here
CPS_THRESHOLD_FLOOR = 0.10      # hard floor — prevents near-zero on very quiet stocks

REGIME_MAP = {
    ( 1,  1): "CONFIRMED_UP",
    ( 1, -1): "DISTRIBUTION",
    (-1,  1): "ACCUMULATION",
    (-1, -1): "CONFIRMED_DOWN",
    ( 0,  0): "NEUTRAL",
}

DIV_PRICE_RDV = "PRICE↑/RDV↓"   # price up, delivery down → distribution
DIV_RDV_PRICE = "RDV↑/PRICE↓"   # delivery up, price down → accumulation


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


def _dynamic_deadband(slope_z: pd.Series) -> float:
    """
    Auto-calibrate regime threshold to this stock's noise floor.
    25th percentile of |slope_z|. Hard floor 0.03.
    """
    return max(float(slope_z.abs().quantile(DEADBAND_PCTILE)), 0.03)


def _sign_bin(val: float, tol: float) -> int:
    if val >  tol: return  1
    if val < -tol: return -1
    return 0


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
# Regime Confidence
# ─────────────────────────────────────────────────────────────────────────────

def _compute_regime_confidence(
    price_slope_z: pd.Series,
    rdv_slope_z:   pd.Series,
    regime:        pd.Series,
) -> pd.Series:
    """
    Measures how clearly defined the current regime is.
    """
    p_rank = price_slope_z.abs().rank(pct=True)
    r_rank = rdv_slope_z.abs().rank(pct=True)
    geom   = (p_rank * r_rank) ** 0.5

    separation = (price_slope_z - rdv_slope_z).abs()
    sep_rank   = separation.rank(pct=True)

    conf = pd.Series(0.0, index=regime.index)

    confirmed_mask = regime.isin(["CONFIRMED_UP", "CONFIRMED_DOWN"])
    diverging_mask = regime.isin(["ACCUMULATION", "DISTRIBUTION"])

    conf[confirmed_mask] = geom[confirmed_mask]
    conf[diverging_mask] = (0.6 * geom[diverging_mask] + 0.4 * sep_rank[diverging_mask])

    conf = conf.apply(lambda v: round(_logistic(v), 3) if v > 0 else 0.0)
    return conf.rename("regime_conf")


# ─────────────────────────────────────────────────────────────────────────────
# Divergence Flag + Confidence
# ─────────────────────────────────────────────────────────────────────────────

def _compute_divergence(
    price_slope_z:   pd.Series,
    rdv_slope_z:     pd.Series,
    mcs_composite:   pd.Series,
    p_tol:           float,
    r_tol:           float,
) -> tuple[pd.Series, pd.Series]:
    """
    Returns (div_flag, div_conf).
    """
    n          = len(price_slope_z)
    flags      = [""] * n
    confs      = [0.0] * n
    p_arr      = price_slope_z.values
    r_arr      = rdv_slope_z.values
    mcs_arr    = mcs_composite.values

    sep = np.abs(p_arr - r_arr)
    sep_rank = pd.Series(sep).rank(pct=True).values

    persist = np.zeros(n, dtype=int)
    cur_flag = ""
    run = 0
    for i in range(n):
        p, r = p_arr[i], r_arr[i]
        if p > p_tol and r < -r_tol:
            flag = DIV_PRICE_RDV
        elif r > r_tol and p < -p_tol:
            flag = DIV_RDV_PRICE
        else:
            flag = ""
        if flag and flag == cur_flag:
            run += 1
        elif flag:
            run = 1
            cur_flag = flag
        else:
            run = 0
            cur_flag = ""
        flags[i]   = flag
        persist[i] = run

    for i in range(n):
        flag = flags[i]
        if not flag:
            continue

        p, r = p_arr[i], r_arr[i]
        p_strength = min(abs(p) / (DIV_THRESHOLD * 2), 1.0)
        r_strength = min(abs(r) / (DIV_THRESHOLD * 2), 1.0)
        leg_score  = (p_strength * r_strength) ** 0.5
        sep_score = float(sep_rank[i])
        raw_persist = min(persist[i], DIV_PERSIST_CAP)
        persist_score = np.log1p(raw_persist) / np.log1p(DIV_PERSIST_CAP)

        mcs_norm = float((np.clip(mcs_arr[i], -1, 1) + 1) / 2)
        if flag == DIV_PRICE_RDV:
            mcs_score = 1.0 - mcs_norm
        else:
            mcs_score = mcs_norm

        raw = (0.35 * sep_score + 0.30 * leg_score + 0.20 * persist_score + 0.15 * mcs_score)
        confs[i] = round(_logistic(raw), 3)

    return (
        pd.Series(flags, index=price_slope_z.index, name="div_flag"),
        pd.Series(confs, index=price_slope_z.index, name="div_conf"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Composite Pressure Score (CPS)
# ─────────────────────────────────────────────────────────────────────────────

def _dynamic_cps_threshold(
    cps:    pd.Series,
    regime: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    """
    Two-tier hysteresis thresholds for CPS, calibrated on NEUTRAL-regime bars.
    """
    neutral_mask = (regime == "NEUTRAL")
    cps_neutral  = cps.where(neutral_mask)
    abs_neutral  = cps_neutral.abs()

    def _rolling_q(q: float) -> pd.Series:
        tq = (abs_neutral
              .rolling(CPS_TRAILING_BARS, min_periods=max(CPS_WINDOW, 5))
              .quantile(q))
        return tq.ffill().fillna(CPS_THRESHOLD_FLOOR).clip(lower=CPS_THRESHOLD_FLOOR)

    onset_thr   = _rolling_q(CPS_PERCENTILE).rename("cps_threshold")
    sustain_thr = _rolling_q(CPS_SUSTAIN_PERCENTILE).rename("cps_sustain_thr")

    return onset_thr, sustain_thr


def _compute_composite_pressure(
    price_slope_z: pd.Series,
    rdv_slope_z:   pd.Series,
    regime:        pd.Series,
    r_tol:         float,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Composite Pressure Score — catches stealth accumulation / distribution.
    """
    n         = len(price_slope_z)
    r_arr     = rdv_slope_z.values
    idx       = price_slope_z.index
    W         = CPS_WINDOW

    cps_vals  = np.zeros(n)
    rdv_std = float(rdv_slope_z.std()) or 1.0

    for i in range(n):
        if i < W - 1: continue
        window_r = r_arr[i - W + 1 : i + 1]

        # 1. Sub-threshold Slope Drift
        ssd_raw  = float(np.mean(window_r))
        ssd_norm = np.clip(ssd_raw / (rdv_std + 1e-10), -1.0, 1.0)

        # 2. RDV Pressure
        rdv_pressure = np.clip(window_r / (r_tol + 1e-10), -1.0, 1.0)
        rdvp = float(np.mean(rdv_pressure))

        # 3. RDV Monotonicity
        time_ranks = np.arange(W, dtype=float)
        rdv_ranks  = pd.Series(window_r).rank().values
        if rdv_ranks.std() < 1e-10:
            mono = 0.0
        else:
            mono = float(np.corrcoef(time_ranks, rdv_ranks)[0, 1])

        majority_positive = np.sum(window_r > 0) > W / 2
        majority_negative = np.sum(window_r < 0) > W / 2
        if majority_positive: mono = max(mono, 0.0)
        elif majority_negative: mono = min(mono, 0.0)

        cps_vals[i] = (0.40 * ssd_norm + 0.35 * rdvp + 0.25 * mono)

    cps = pd.Series(np.round(cps_vals, 4), index=idx, name="cps")
    onset_thr, sustain_thr = _dynamic_cps_threshold(cps, regime)

    flags       = [""] * n
    active_flag = ""
    on_arr, sus_arr, cps_arr, reg_arr = onset_thr.values, sustain_thr.values, cps.values, regime.values

    ACCUM_REGIMES = {"ACCUMULATION", "CONFIRMED_UP"}
    DIST_REGIMES  = {"DISTRIBUTION", "CONFIRMED_DOWN"}

    for i in range(n):
        c, on, sus, reg = cps_arr[i], on_arr[i], sus_arr[i], reg_arr[i]

        if active_flag == "STEALTH_DIST" and reg in ACCUM_REGIMES: active_flag = ""
        elif active_flag == "STEALTH_ACCUM" and reg in DIST_REGIMES: active_flag = ""

        if not active_flag:
            if c > on: active_flag = "STEALTH_ACCUM"
            elif c < -on: active_flag = "STEALTH_DIST"
        else:
            if active_flag == "STEALTH_ACCUM" and c < sus: active_flag = ""
            elif active_flag == "STEALTH_DIST" and c > -sus: active_flag = ""
            if not active_flag:
                if c > on: active_flag = "STEALTH_ACCUM"
                elif c < -on: active_flag = "STEALTH_DIST"

        if active_flag == "STEALTH_DIST" and reg in ACCUM_REGIMES: active_flag = ""
        elif active_flag == "STEALTH_ACCUM" and reg in DIST_REGIMES: active_flag = ""
        flags[i] = active_flag

    cps_flag = pd.Series(flags, index=idx, name="cps_flag")
    cps_conf_arr = np.zeros(n)
    for i in range(n):
        if flags[i]:
            on, c_abs = on_arr[i], abs(cps_arr[i])
            denom = max(1.0 - on, 0.01)
            cps_conf_arr[i] = round(float(np.clip((c_abs - on) / denom, 0.0, 1.0)), 3)

    return cps, cps_flag, pd.Series(cps_conf_arr, index=idx, name="cps_conf")


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
    slope_window:   int = 14,
) -> pd.DataFrame:
    """
    Trend Participation Engine v5.2
    """
    df = df.copy()
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

    p_tol = _dynamic_deadband(df["price_slope_z"])
    r_tol = _dynamic_deadband(df["rdv_slope_z"])

    p_sign = df["price_slope_z"].apply(lambda v: _sign_bin(v, p_tol))
    r_sign = df["rdv_slope_z"].apply(  lambda v: _sign_bin(v, r_tol))

    df["regime"] = [REGIME_MAP.get((int(p), int(r)), "NEUTRAL") for p, r in zip(p_sign, r_sign)]
    df["regime_conf"] = _compute_regime_confidence(df["price_slope_z"], df["rdv_slope_z"], df["regime"])

    p_a = _pillar_a_slope_alignment(df["price_slope_z"], df["rdv_slope_z"])
    p_b = _pillar_b_mcs_confirmation(df[mcs_col], df[mcs_slope_col], df["price_slope_z"])
    p_c = _pillar_c_cwc_coherence(df[cwc_slope_col])

    df["coherence_raw"] = (0.45 * p_a + 0.35 * p_b + 0.20 * p_c).apply(lambda v: round(_logistic(v), 3))
    df["coherence"] = df["coherence_raw"].ewm(span=EMA_SMOOTH_SPAN, adjust=False).mean().round(3)
    df["score_slope"] = df["coherence"].diff(TRAJECTORY_BARS).fillna(0).apply(
        lambda d: "RISING" if d > 0.03 else ("FALLING" if d < -0.03 else "FLAT")
    )

    df["div_flag"], df["div_conf"] = _compute_divergence(df["price_slope_z"], df["rdv_slope_z"], df[mcs_col], p_tol, r_tol)

    # Composite Pressure Score (stealth signal layer)
    df["cps"], df["cps_flag"], df["cps_conf"] = _compute_composite_pressure(df["price_slope_z"], df["rdv_slope_z"], df["regime"], r_tol)

    # ── Stealth Signal Promotion ───────────────────────────────────────────
    # Requirement: STEALTH_ACCUM / STEALTH_DIST need to be shown as
    # ACCUMULATION and DISTRIBUTION when they are present and regime is "NEUTRAL".
    neutral_mask = (df["regime"] == "NEUTRAL")
    
    accum_mask = neutral_mask & (df["cps_flag"] == "STEALTH_ACCUM")
    df.loc[accum_mask, "regime"] = "ACCUMULATION"
    df.loc[accum_mask, "regime_conf"] = df.loc[accum_mask, "cps_conf"]

    dist_mask = neutral_mask & (df["cps_flag"] == "STEALTH_DIST")
    df.loc[dist_mask, "regime"] = "DISTRIBUTION"
    df.loc[dist_mask, "regime_conf"] = df.loc[dist_mask, "cps_conf"]

    return df
