"""
interactive_analysis.py
────────────────────────────────────────────────────────────────────────────
Trend Participation Engine — NSE EOD (Bhav Copy) Edition  v5.0

What this outputs per bar
─────────────────────────
  price_slope_z   z-normalised rolling slope of close price
  rdv_slope_z     z-normalised rolling slope of RDV
  regime          CONFIRMED_UP | CONFIRMED_DOWN | ACCUMULATION
                  DISTRIBUTION | NEUTRAL
  regime_conf     [0–1]  how clearly defined is this regime
                  • 0.0 always for NEUTRAL (absence of regime = no confidence)
                  • Geometric mean of slope-rank magnitudes for all others
                  • Separation term added for ACCUMULATION / DISTRIBUTION
  coherence_raw   raw three-pillar logistic score [0–1]
  coherence       EMA-3 smoothed coherence        [0–1]
  score_slope     RISING / FLAT / FALLING  (T=5 delta on smoothed score)
  div_flag        PRICE↑/RDV↓  |  RDV↑/PRICE↓  |  empty
  div_conf        [0–1]  only when div_flag is set
                  • Slope separation magnitude (how far apart are the two)
                  • Leg strength (both must be meaningfully sized)
                  • Persistence (consecutive bars of same divergence)
                  • MCS alignment (does money-flow confirm the divergence)
  cps             Composite Pressure Score [-1, +1]
                  Multi-bar stealth signal: catches sub-threshold institutional
                  accumulation / distribution invisible to single-bar regime
  cps_flag        STEALTH_ACCUM | STEALTH_DIST | empty
  cps_conf        [0–1] confidence when cps_flag is active, else 0.0
                  Normalised distance above onset threshold: 0=just fired, 1=max
                  Analogous to regime_conf / div_conf — drives TV marker size

v5.2 changes vs v5.1
─────────────────────
  • Regime-conflict suppression: primary regime overrides stale stealth signals.
    STEALTH_DIST is force-cleared when regime turns ACCUMULATION/CONFIRMED_UP.
    STEALTH_ACCUM is force-cleared when regime turns DISTRIBUTION/CONFIRMED_DOWN.
    Eliminates ghost flags (e.g. STEALTH_DIST showing during ACCUMULATION 0.925).
  • cps_threshold replaced by cps_conf [0-1] — confidence score identical in
    style to regime_conf and div_conf. Drives TV marker text and size directly.
    Formula: clip((|CPS| - onset_thr) / (1 - onset_thr), 0, 1).

v5.1 changes vs v5.0
─────────────────────
  • CPS threshold now two-tier hysteresis, calibrated on NEUTRAL-regime bars only.
    - onset_thr  (75th pctile): signal fires when CPS first bursts above this.
    - sustain_thr (40th pctile): signal holds until CPS falls below this.
    Previously, using all bars inflated the threshold; even NEUTRAL-bar-only
    calibration left a self-defeating feedback (strong STEALTH days are
    NEUTRAL-regime, so they raised the onset bar for their own follow-through).
    Hysteresis cleanly separates "start" from "sustain" logic.

v5.0 changes vs v4.0
─────────────────────
  • CPS sub-component 2 changed from binary Regime Skew (RSK) to continuous
    RDV Pressure (RDVp = mean clip(rdv_slope_z / r_tol, -1, 1)).  Awards
    proportional credit to sub-threshold NEUTRAL bars — the key gap in v4.
  • CPS MONO directional guard: fade from positive (or negative) territory
    no longer penalises the score via a negative Spearman correlation.
  • CPS trailing calibration window reduced 60 → 30 bars so threshold resets
    faster after high-energy periods, avoiding inherited inflation.

No phase labels. No CVWAP distance. No advisory text.
────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import sys
import os
import json
import argparse
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from tabulate import tabulate

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.divergence_engine.engine import DivergenceEngine


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
                           # (30 bars ≈ 6 weeks — resets to current regime fast
                           #  enough to catch quiet phases after high-energy runs)
CPS_PERCENTILE         = 0.75   # onset: upper-quartile fires a new stealth signal
CPS_SUSTAIN_PERCENTILE = 0.40   # sustain: active signal holds until CPS falls here
                                # hysteresis prevents premature clearing on a fade
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

REGIME_COLOUR = {
    "CONFIRMED_UP"  : "\033[92m",   # green
    "CONFIRMED_DOWN": "\033[91m",   # red
    "ACCUMULATION"  : "\033[94m",   # blue
    "DISTRIBUTION"  : "\033[93m",   # amber
    "NEUTRAL"       : "\033[90m",   # grey
    "STEALTH_ACCUM" : "\033[96m",   # cyan
    "STEALTH_DIST"  : "\033[95m",   # magenta
}
RESET = "\033[0m"
SCORE_SLOPE_SYMBOL = {"RISING": "▲", "FLAT": "─", "FALLING": "▼"}


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

def _macd_z(series: pd.Series, span: int) -> pd.Series:
    """MACD-style momentum: fast EMA (span/2) - slow EMA (span)."""
    fast = series.ewm(span=max(2, span // 2), adjust=False).mean()
    slow = series.ewm(span=span, adjust=False).mean()
    macd = fast - slow
    
    roll_std = macd.rolling(window=span * 2, min_periods=span).std()
    roll_std = roll_std.bfill().replace(0.0, 1e-10)
    
    z_score = macd / roll_std
    return z_score.fillna(0.0)


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
    Product form penalises a weak reading in either leg.
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

    NEUTRAL → always 0.0 (absence of regime has no confidence)

    CONFIRMED_UP / CONFIRMED_DOWN:
      Both slopes same direction. Confidence = geometric mean of their
      percentile ranks. Both legs must be strong for a high score.

    ACCUMULATION / DISTRIBUTION:
      Slopes in opposite directions. Confidence = geometric mean of ranks
      combined with the separation between the two slopes.
      A narrow divergence is weak; a wide one is structural.

    Result is logistic-stretched so scores are decisive, not mushy.
    """
    p_rank = price_slope_z.abs().rank(pct=True)
    r_rank = rdv_slope_z.abs().rank(pct=True)
    geom   = (p_rank * r_rank) ** 0.5

    # Separation term — only meaningful for ACCUMULATION / DISTRIBUTION
    # where slopes are opposing. Ranks the absolute gap between the two.
    separation = (price_slope_z - rdv_slope_z).abs()
    sep_rank   = separation.rank(pct=True)

    conf = pd.Series(0.0, index=regime.index)

    confirmed_mask = regime.isin(["CONFIRMED_UP", "CONFIRMED_DOWN"])
    diverging_mask = regime.isin(["ACCUMULATION", "DISTRIBUTION"])

    # For confirmed: pure geometric mean of both legs
    conf[confirmed_mask] = geom[confirmed_mask]

    # For diverging: blend geometric mean (leg strength) + separation rank
    # 0.6 × leg strength + 0.4 × separation magnitude
    conf[diverging_mask] = (
        0.6 * geom[diverging_mask] +
        0.4 * sep_rank[diverging_mask]
    )

    # Logistic stretch — makes confident signals decisive
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

    div_flag fires when |p_z| and |r_z| both exceed DIV_THRESHOLD in
    opposite directions — the same as before.

    div_conf [0–1] measures:
      • Slope separation magnitude (how far apart are the two vectors)
        → ranked percentile across history
      • Leg strength (both legs must be above DIV_THRESHOLD, not just barely)
        → geometric mean of (|p_z| / threshold, |r_z| / threshold) capped at 1
      • Persistence (consecutive bars with same div_flag)
        → log-scaled count, capped at DIV_PERSIST_CAP
      • MCS confirmation (does money-flow health align with divergence direction)
        → rescaled MCS value weighted by direction

    Weights: separation 0.35 | legs 0.30 | persistence 0.20 | mcs 0.15
    """
    n          = len(price_slope_z)
    flags      = [""] * n
    confs      = [0.0] * n
    p_arr      = price_slope_z.values
    r_arr      = rdv_slope_z.values
    mcs_arr    = mcs_composite.values

    # Pre-compute separation rank across entire series
    sep = np.abs(p_arr - r_arr)
    sep_rank = pd.Series(sep).rank(pct=True).values

    # Consecutive persistence count
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

        # Leg strength — how far above the threshold are both legs
        p_strength = min(abs(p) / (DIV_THRESHOLD * 2), 1.0)
        r_strength = min(abs(r) / (DIV_THRESHOLD * 2), 1.0)
        leg_score  = (p_strength * r_strength) ** 0.5

        # Separation rank
        sep_score = float(sep_rank[i])

        # Persistence — log-scaled, capped
        raw_persist = min(persist[i], DIV_PERSIST_CAP)
        persist_score = np.log1p(raw_persist) / np.log1p(DIV_PERSIST_CAP)

        # MCS alignment
        # PRICE↑/RDV↓ is bearish — confirmed by low (negative) MCS
        # RDV↑/PRICE↓ is accumulation — confirmed by high MCS or rising
        mcs_norm = float((np.clip(mcs_arr[i], -1, 1) + 1) / 2)  # [0,1]
        if flag == DIV_PRICE_RDV:
            mcs_score = 1.0 - mcs_norm   # low MCS confirms bearish divergence
        else:
            mcs_score = mcs_norm          # high MCS confirms bullish accumulation

        raw = (
            0.35 * sep_score     +
            0.30 * leg_score     +
            0.20 * persist_score +
            0.15 * mcs_score
        )
        confs[i] = round(_logistic(raw), 3)

    return (
        pd.Series(flags, index=price_slope_z.index, name="div_flag"),
        pd.Series(confs, index=price_slope_z.index, name="div_conf"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Composite Pressure Score  (stealth accumulation / distribution detector)
# ─────────────────────────────────────────────────────────────────────────────

def _dynamic_cps_threshold(
    cps:    pd.Series,
    regime: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    """
    Two-tier hysteresis thresholds for CPS, calibrated on NEUTRAL-regime bars.

    Why NEUTRAL bars only?
    ──────────────────────
    The threshold should answer: "what does CPS look like when nothing is
    happening?"  High-conviction periods (CONFIRMED_UP, DISTRIBUTION etc.)
    produce naturally high |CPS| and would inflate the threshold if included.
    Crucially, strong STEALTH signal days are themselves NEUTRAL-regime bars
    with high CPS — including them creates a self-defeating feedback where a
    strong stealth signal on day N raises the onset bar for days N+1..N+30.

    Two-tier hysteresis
    ───────────────────
    onset_thr   (CPS_PERCENTILE = 75th)
        A new STEALTH signal fires only when CPS first crosses this bar.
        Requires a genuine burst above the NEUTRAL noise floor.

    sustain_thr (CPS_SUSTAIN_PERCENTILE = 40th)
        Once a signal is active, it is held until CPS falls below this bar.
        A natural institutional fade (decreasing but still positive RDV) will
        keep CPS above the sustain threshold even as it drops below onset.
        The signal clears only when CPS returns to true quiet-market levels.

    Forward-fill: non-NEUTRAL bars inherit the last computed NEUTRAL threshold.
    Hard floor:   CPS_THRESHOLD_FLOOR on both tiers.
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
    Composite Pressure Score — catches stealth accumulation / distribution
    that is invisible to single-bar regime classification.

    Three sub-components over a CPS_WINDOW rolling window
    ──────────────────────────────────────────────────────
    1. Sub-threshold Slope Drift (SSD)  weight 0.40
       Rolling mean of rdv_slope_z.  Even sub-deadband readings leave a
       consistent signed footprint when institutions are nibbling quietly.
       Anchored to rdv because delivery is the tell; price can be managed.

    2. RDV Pressure (RDVp)              weight 0.35  [replaces binary RSK]
       Mean of clip(rdv_slope_z / r_tol, -1, 1) over the window.
       Awards continuous credit proportional to how close each bar came to
       the deadband — rather than zero unless it crossed it.  A NEUTRAL bar
       with rdv_slope_z = 0.5×r_tol scores 0.5, not 0.
       This is the key fix: RSK was blind to sub-threshold NEUTRAL bars.

    3. RDV Monotonicity (MONO)          weight 0.25
       Spearman rank correlation of rdv_slope_z values vs time over the
       window.  +1 = delivery consistently rising bar-by-bar (institutional
       drip-buying footprint).  −1 = consistently falling (quiet offloading).
       Directional guard: when the majority of window values are positive
       (accumulation context), MONO is floored at 0 rather than penalising
       a natural fade from positive territory.  Symmetric guard on the short
       side: majority-negative context floors MONO ceiling at 0.

    CPS = 0.40 × SSD_norm + 0.35 × RDVp + 0.25 × MONO
    Ranges [−1, +1].  Positive → stealth accumulation pressure.
                      Negative → stealth distribution pressure.

    Threshold — two-tier hysteresis, NEUTRAL-bar calibrated
    ────────────────────────────────────────────────────────
    onset_thr   (75th pctile of NEUTRAL |CPS|, trailing 30 bars)
        Fire a new signal when CPS first crosses this.
    sustain_thr (40th pctile of NEUTRAL |CPS|, trailing 30 bars)
        Once active, hold the signal until CPS falls below this.
        Prevents premature clearing during natural institutional fades.
    Both tiers sample NEUTRAL-regime bars only — see _dynamic_cps_threshold.

    Returns (cps, cps_flag, onset_threshold_series)
    """
    n         = len(price_slope_z)
    r_arr     = rdv_slope_z.values
    reg_arr   = regime.values
    idx       = price_slope_z.index
    W         = CPS_WINDOW

    cps_vals  = np.zeros(n)

    # Normalisation range for SSD — use the full-series std of rdv_slope_z
    rdv_std = float(rdv_slope_z.std()) or 1.0

    for i in range(n):
        if i < W - 1:
            continue

        window_r = r_arr[i - W + 1 : i + 1]   # shape (W,)

        # ── 1. Sub-threshold Slope Drift ──────────────────────────────────
        ssd_raw  = float(np.mean(window_r))
        ssd_norm = np.clip(ssd_raw / (rdv_std + 1e-10), -1.0, 1.0)

        # ── 2. RDV Pressure — continuous, deadband-relative ───────────────
        # Each bar contributes proportionally to how far its rdv_slope_z
        # sits relative to the deadband, not a binary cross/no-cross.
        rdv_pressure = np.clip(window_r / (r_tol + 1e-10), -1.0, 1.0)
        rdvp = float(np.mean(rdv_pressure))

        # ── 3. RDV Monotonicity (Spearman via rank correlation) ───────────
        time_ranks = np.arange(W, dtype=float)
        rdv_ranks  = pd.Series(window_r).rank().values
        if rdv_ranks.std() < 1e-10:
            mono = 0.0
        else:
            mono = float(np.corrcoef(time_ranks, rdv_ranks)[0, 1])

        # Directional guard: a fade from positive territory should not
        # actively drag CPS negative — floor at 0 in accumulation context,
        # ceiling at 0 in distribution context.
        majority_positive = np.sum(window_r > 0) > W / 2
        majority_negative = np.sum(window_r < 0) > W / 2
        if majority_positive:
            mono = max(mono, 0.0)   # fade from long is OK; punishing it is not
        elif majority_negative:
            mono = min(mono, 0.0)   # fade from short is OK; rewarding it is not

        # ── Composite ─────────────────────────────────────────────────────
        cps_vals[i] = (
            0.40 * ssd_norm +
            0.35 * rdvp     +
            0.25 * mono
        )

    cps = pd.Series(np.round(cps_vals, 4), index=idx, name="cps")

    # ── Two-tier hysteresis thresholds (NEUTRAL-bar calibrated) ──────────
    onset_thr, sustain_thr = _dynamic_cps_threshold(cps, regime)

    # ── Stateful hysteresis flag ───────────────────────────────────────────
    # Fire on onset_thr crossing; hold until CPS falls back below sustain_thr.
    # Prevents premature clearing during natural institutional fade.
    # Regime-conflict suppression: primary regime takes precedence.
    #   STEALTH_DIST is force-cleared when regime is ACCUMULATION/CONFIRMED_UP
    #   STEALTH_ACCUM is force-cleared when regime is DISTRIBUTION/CONFIRMED_DOWN
    # CPS is a sub-threshold detector; when the primary engine has a confirmed
    # directional regime contradicting the stealth flag, the primary wins.
    flags       = [""] * n
    active_flag = ""
    on_arr   = onset_thr.values
    sus_arr  = sustain_thr.values
    cps_arr  = cps.values
    reg_arr  = regime.values

    ACCUM_REGIMES = {"ACCUMULATION", "CONFIRMED_UP"}
    DIST_REGIMES  = {"DISTRIBUTION", "CONFIRMED_DOWN"}

    for i in range(n):
        c   = cps_arr[i]
        on  = on_arr[i]
        sus = sus_arr[i]
        reg = reg_arr[i]

        # ── Regime-conflict suppression (runs before hysteresis) ──────────
        # If the primary regime explicitly contradicts the active stealth
        # flag, clear it immediately and prevent it from refiring this bar.
        if active_flag == "STEALTH_DIST"  and reg in ACCUM_REGIMES:
            active_flag = ""
        elif active_flag == "STEALTH_ACCUM" and reg in DIST_REGIMES:
            active_flag = ""

        # ── Hysteresis onset / sustain ────────────────────────────────────
        if not active_flag:
            if c > on:
                active_flag = "STEALTH_ACCUM"
            elif c < -on:
                active_flag = "STEALTH_DIST"
        else:
            # Clear when CPS falls below sustain threshold
            if active_flag == "STEALTH_ACCUM" and c < sus:
                active_flag = ""
            elif active_flag == "STEALTH_DIST" and c > -sus:
                active_flag = ""
            # Allow polarity flip directly through onset without gap
            if not active_flag:
                if c > on:
                    active_flag = "STEALTH_ACCUM"
                elif c < -on:
                    active_flag = "STEALTH_DIST"

        # ── Second-pass conflict check (catches same-bar onset into conflict) ─
        if active_flag == "STEALTH_DIST"  and reg in ACCUM_REGIMES:
            active_flag = ""
        elif active_flag == "STEALTH_ACCUM" and reg in DIST_REGIMES:
            active_flag = ""

        flags[i] = active_flag

    cps_flag = pd.Series(flags, index=idx, name="cps_flag")

    # ── CPS confidence score [0–1] ────────────────────────────────────────
    # How far above the onset threshold is |CPS|?
    # 0.0 = exactly at threshold (just fired)
    # 1.0 = CPS at theoretical maximum (1.0)
    # Analogous to regime_conf and div_conf — usable directly in TV markers.
    # Only non-zero when signal is active.
    cps_conf_arr = np.zeros(n)
    for i in range(n):
        if flags[i]:
            on    = on_arr[i]
            c_abs = abs(cps_arr[i])
            denom = max(1.0 - on, 0.01)
            cps_conf_arr[i] = round(float(np.clip((c_abs - on) / denom, 0.0, 1.0)), 3)

    cps_conf = pd.Series(cps_conf_arr, index=idx, name="cps_conf")

    return cps, cps_flag, cps_conf


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
    use_macd:       bool = False,
) -> pd.DataFrame:
    """
    Trend Participation Engine v4.0

    Output columns
    ──────────────
    price_slope_z   z-normalised rolling slope of close
    rdv_slope_z     z-normalised rolling slope of RDV
    regime          4-quadrant regime label
    regime_conf     [0–1] regime clarity score (0.0 for NEUTRAL)
    coherence_raw   raw three-pillar score [0–1]
    coherence       EMA-3 smoothed coherence [0–1]
    score_slope     RISING / FLAT / FALLING
    div_flag        PRICE↑/RDV↓  |  RDV↑/PRICE↓  |  empty
    div_conf        [0–1] only when div_flag is set, else 0.0
    """
    df = df.copy()

    # ── Column resolution ─────────────────────────────────────────────────
    col_map = {c.lower(): c for c in df.columns}

    def _resolve(name: str, required: bool = True):
        r = col_map.get(name.lower(), name)
        if required and r not in df.columns:
            raise ValueError(
                f"Column '{name}' not found. Available: {list(df.columns)}"
            )
        return r if r in df.columns else None

    close_col     = _resolve(close_col)
    rdv_col       = _resolve(rdv_col)
    cwc_slope_col = _resolve(cwc_slope_col)
    mcs_col       = _resolve(mcs_col)
    mcs_slope_col = _resolve(mcs_slope_col)

    # ── Slopes ─────────────────────────────────────────────────────────────
    if use_macd:
        df["price_slope_z"] = _macd_z(df[close_col], slope_window).round(4)
        df["rdv_slope_z"]   = _macd_z(df[rdv_col],   slope_window).round(4)
    else:
        df["price_slope_z"] = _rolling_slope_z(df[close_col], slope_window).round(4)
        df["rdv_slope_z"]   = _rolling_slope_z(df[rdv_col],   slope_window).round(4)

    # ── Regime — dynamic deadband ─────────────────────────────────────────
    p_tol = _dynamic_deadband(df["price_slope_z"])
    r_tol = _dynamic_deadband(df["rdv_slope_z"])

    p_sign = df["price_slope_z"].apply(lambda v: _sign_bin(v, p_tol))
    r_sign = df["rdv_slope_z"].apply(  lambda v: _sign_bin(v, r_tol))

    df["regime"] = [
        REGIME_MAP.get((int(p), int(r)), "NEUTRAL")
        for p, r in zip(p_sign, r_sign)
    ]

    # ── Regime confidence ─────────────────────────────────────────────────
    df["regime_conf"] = _compute_regime_confidence(
        df["price_slope_z"], df["rdv_slope_z"], df["regime"]
    )

    # ── Three-pillar coherence ────────────────────────────────────────────
    p_a = _pillar_a_slope_alignment(df["price_slope_z"], df["rdv_slope_z"])
    p_b = _pillar_b_mcs_confirmation(df[mcs_col], df[mcs_slope_col], df["price_slope_z"])
    p_c = _pillar_c_cwc_coherence(df[cwc_slope_col])

    raw_score           = 0.45 * p_a + 0.35 * p_b + 0.20 * p_c
    df["coherence_raw"] = raw_score.apply(lambda v: round(_logistic(v), 3))

    # ── EMA smoothing ─────────────────────────────────────────────────────
    df["coherence"] = (
        df["coherence_raw"]
        .ewm(span=EMA_SMOOTH_SPAN, adjust=False)
        .mean()
        .round(3)
    )

    # ── Score trajectory ──────────────────────────────────────────────────
    score_delta       = df["coherence"].diff(TRAJECTORY_BARS).fillna(0)
    df["score_slope"] = score_delta.apply(
        lambda d: "RISING" if d > 0.03 else ("FALLING" if d < -0.03 else "FLAT")
    )

    # ── Divergence flag + confidence ──────────────────────────────────────
    df["div_flag"], df["div_conf"] = _compute_divergence(
        df["price_slope_z"], df["rdv_slope_z"], df[mcs_col], p_tol, r_tol
    )

    # ── Composite Pressure Score (stealth signal layer) ───────────────────
    df["cps"], df["cps_flag"], df["cps_conf"] = _compute_composite_pressure(
        df["price_slope_z"], df["rdv_slope_z"], df["regime"], r_tol
    )

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Display
# ─────────────────────────────────────────────────────────────────────────────

def _colour_regime(val: str) -> str:
    c = REGIME_COLOUR.get(str(val), "")
    return f"{c}{val}{RESET}" if c else str(val)


def print_summary(df: pd.DataFrame, symbol: str) -> None:
    last  = df.dropna(subset=["coherence"]).iloc[-1]
    p_tol = _dynamic_deadband(df["price_slope_z"])
    r_tol = _dynamic_deadband(df["rdv_slope_z"])

    sep = "─" * 62
    print(f"\n{sep}")
    print(f"  Symbol      : {symbol}")
    print(f"  Date        : {str(last['date'])[:10]}")
    print(f"  Regime      : {_colour_regime(str(last['regime']))}  "
          f"[conf {last['regime_conf']:.3f}]")
    print(
        f"  Coherence   : {last['coherence_raw']:.3f} raw"
        f"  →  {last['coherence']:.3f} smooth"
        f"  {SCORE_SLOPE_SYMBOL.get(str(last['score_slope']), '?')}"
        f" {last['score_slope']}"
    )
    print(f"  Price z     : {last['price_slope_z']:+.4f}  (deadband ±{p_tol:.3f})")
    print(f"  RDV z       : {last['rdv_slope_z']:+.4f}  (deadband ±{r_tol:.3f})")
    if last.get("div_flag"):
        print(f"  Divergence  : {last['div_flag']}  [conf {last['div_conf']:.3f}]")
    if last.get("cps_flag"):
        conf = last.get("cps_conf", 0.0)
        print(
            f"  CPS         : {last['cps']:+.4f}  →  "
            f"{_colour_regime(str(last['cps_flag']))}  "
            f"[conf {conf:.3f}]"
        )
    elif "cps" in last and pd.notna(last.get("cps")):
        print(f"  CPS         : {last['cps']:+.4f}  (below threshold)")
    print(f"{sep}\n")


def build_display_df(df: pd.DataFrame, last_n: int) -> pd.DataFrame:
    cols = [
        "date",
        "close",
        "price_slope_z",
        "rdv_slope_z",
        "regime",
        "regime_conf",
        "coherence_raw",
        "coherence",
        "score_slope",
        "div_flag",
        "div_conf",
        "cps",
        "cps_flag",
        "cps_conf",
        "gradient_shape",
    ]
    return df[[c for c in cols if c in df.columns]].tail(last_n).copy()


# ─────────────────────────────────────────────────────────────────────────────
# TradingView JSON
# ─────────────────────────────────────────────────────────────────────────────

def build_tradingview_payload(df: pd.DataFrame) -> dict:
    """
    TradingView Lightweight Charts payload.

    regime_markers   → series.setMarkers()
                       On regime change only. text = "REGIME  conf"
                       Size scales with regime_conf.

    div_markers      → series.setMarkers()
                       On div_flag change only. text = "FLAG  conf"
                       Size scales with div_conf.

    coherence_raw    → addLineSeries() dotted
    coherence_smooth → addLineSeries() solid
    regime_hist      → addHistogramSeries() background colour

    Regime marker colours
    ─────────────────────
    CONFIRMED_UP    #26a69a  teal
    CONFIRMED_DOWN  #ef5350  red
    ACCUMULATION    #42a5f5  blue
    DISTRIBUTION    #ffb300  amber
    NEUTRAL         #9e9e9e  grey  (shown only on change)

    Divergence marker colours
    ─────────────────────────
    PRICE↑/RDV↓     #ef5350  red    arrowDown  aboveBar
    RDV↑/PRICE↓     #26a69a  teal   arrowUp    belowBar
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])

    regime_cfg = {
        "ACCUMULATION"  : {"color": "#42a5f5", "shape": "circle",    "pos": "belowBar"},
        "DISTRIBUTION"  : {"color": "#ffb300", "shape": "circle",    "pos": "aboveBar"},
        "NEUTRAL"       : {"color": "#9e9e9e", "shape": "square",    "pos": "belowBar"},
    }

    cps_cfg = {
        "STEALTH_ACCUM": {"color": "#00bcd4", "shape": "square", "pos": "belowBar"},
        "STEALTH_DIST" : {"color": "#ce93d8", "shape": "square", "pos": "aboveBar"},
    }

    div_cfg = {
        DIV_PRICE_RDV: {"color": "#ef5350", "shape": "arrowDown", "pos": "aboveBar"},
        DIV_RDV_PRICE: {"color": "#26a69a", "shape": "arrowUp",   "pos": "belowBar"},
    }

    regime_colour_hist = {
        "CONFIRMED_UP"  : "#26a69a22",
        "CONFIRMED_DOWN": "#ef535044",
        "ACCUMULATION"  : "#42a5f522",
        "DISTRIBUTION"  : "#ffb30022",
        "NEUTRAL"       : "#90909012",
    }

    regime_markers   = []
    div_markers      = []
    cps_markers      = []
    coherence_raw    = []
    coherence_smooth = []
    regime_hist      = []

    prev_regime  = None
    prev_div     = None
    prev_cps_flag = None

    for _, row in df.iterrows():
        ts = int(row["date"].timestamp())

        # Coherence lines — every bar
        if pd.notna(row.get("coherence_raw")):
            coherence_raw.append(   {"time": ts, "value": float(row["coherence_raw"])})
        if pd.notna(row.get("coherence")):
            coherence_smooth.append({"time": ts, "value": float(row["coherence"])})

        # Regime histogram — every bar
        colour = regime_colour_hist.get(str(row.get("regime", "NEUTRAL")), "#90909012")
        regime_hist.append({"time": ts, "value": 1, "color": colour})

        # Regime marker — only on regime change
        regime = str(row.get("regime", "NEUTRAL"))
        if regime != prev_regime and regime in regime_cfg:
            cfg  = regime_cfg[regime]
            conf = float(row.get("regime_conf", 0.0))
            text = f"{regime}  {conf:.2f}" if conf >= 0.5 else regime
            regime_markers.append({
                "time"    : ts,
                "position": cfg["pos"],
                "color"   : cfg["color"],
                "shape"   : cfg["shape"],
                "text"    : text,
                "size"    : round(max(conf * 3, 0.5), 1),
            })
        prev_regime = regime

        # Divergence marker — only on div change
        div_flag = str(row.get("div_flag", ""))
        if div_flag and div_flag != prev_div and div_flag in div_cfg:
            cfg  = div_cfg[div_flag]
            conf = float(row.get("div_conf", 0.0))
            div_markers.append({
                "time"    : ts,
                "position": cfg["pos"],
                "color"   : cfg["color"],
                "shape"   : cfg["shape"],
                "text"    : f"{div_flag}  {conf:.2f}",
                "size"    : round(max(conf * 3, 0.5), 1),
            })
        prev_div = div_flag if div_flag else prev_div

        # CPS marker — only on cps_flag change (onset of stealth signal)
        cps_flag = str(row.get("cps_flag", ""))
        if cps_flag and cps_flag != prev_cps_flag and cps_flag in cps_cfg:
            cfg      = cps_cfg[cps_flag]
            cps_val  = float(row.get("cps", 0.0))
            conf     = float(row.get("cps_conf", 0.0))
            cps_markers.append({
                "time"    : ts,
                "position": cfg["pos"],
                "color"   : cfg["color"],
                "shape"   : cfg["shape"],
                "text"    : f"{cps_flag}  {conf:.2f}",
                "size"    : round(max(conf * 3, 0.5), 1),
            })
        prev_cps_flag = cps_flag if cps_flag else prev_cps_flag

    return {
        "symbol"          : df.attrs.get("symbol", ""),
        "regime_markers"  : regime_markers,
        "div_markers"     : div_markers,
        "cps_markers"     : cps_markers,
        "coherence_raw"   : coherence_raw,
        "coherence_smooth": coherence_smooth,
        "regime_hist"     : regime_hist,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def run_analysis(
    symbol:       str,
    days:         int,
    slope_window: int,
    emit_json:    bool,
    emit_csv:     bool,
    use_macd:     bool = False,
) -> None:

    print(f"\nInitializing Divergence Engine for {symbol}...")

    try:
        engine = DivergenceEngine(ticker=symbol)
        result = engine.run()
        df     = result.ledger
    except Exception as e:
        print(f"[ERROR] Engine failed for {symbol}: {e}")
        return

    df["date"] = pd.to_datetime(df["date"])

    price_col = next((c for c in ["close", "ltp", "tp"] if c in df.columns), None)
    if price_col is None:
        print(f"[ERROR] No price column. Available: {list(df.columns)}")
        return

    print(f"  price='{price_col}'  window={slope_window}d  bars={days}  macd={use_macd}")

    df = compute_trend_participation(df, close_col=price_col, slope_window=slope_window, use_macd=use_macd)
    df.attrs["symbol"] = symbol

    print_summary(df, symbol)

    display_df   = build_display_df(df, last_n=days)
    table_string = tabulate(
        display_df,
        headers   = "keys",
        tablefmt  = "rounded_outline",
        showindex = False,
        floatfmt  = ".4f",
    )
    print(table_string)

    # ── Text file ────────────────────────────────────────────────────────
    last  = df.dropna(subset=["coherence"]).iloc[-1]
    p_tol = _dynamic_deadband(df["price_slope_z"])
    r_tol = _dynamic_deadband(df["rdv_slope_z"])

    txt_path = f"analysis_{symbol}.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(
            f"Trend Participation Analysis — {symbol}\n"
            f"Generated    : {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"Slope Window : {slope_window}d | Bars : {days} | "
            f"EMA : {EMA_SMOOTH_SPAN} | Traj : {TRAJECTORY_BARS}d\n"
            f"Deadband     : price ±{p_tol:.3f}  rdv ±{r_tol:.3f}  (auto)\n"
            f"{'─'*80}\n\n"
            f"LATEST STATE\n"
            f"  Date      : {str(last['date'])[:10]}\n"
            f"  Regime    : {last['regime']}  [conf {last['regime_conf']:.3f}]\n"
            f"  Coherence : {last['coherence_raw']:.3f} raw"
            f"  →  {last['coherence']:.3f} smooth  {last['score_slope']}\n"
            f"  Price z   : {last['price_slope_z']:+.4f}\n"
            f"  RDV z     : {last['rdv_slope_z']:+.4f}\n"
            f"  Gradient  : {last.get('gradient_shape', 'none')}\n"
        )
        if last.get("div_flag"):
            f.write(
                f"  Divergence: {last['div_flag']}  "
                f"[conf {last['div_conf']:.3f}]\n"
            )
        f.write(f"\n{'─'*80}\n\n{table_string}\n")

    print(f"\n[OK] {txt_path}")

    if emit_csv:
        csv_path = f"analysis_{symbol}.csv"
        display_df.to_csv(csv_path, index=False)
        print(f"[OK] {csv_path}")

    if emit_json:
        payload   = build_tradingview_payload(df.tail(days))
        json_path = f"analysis_{symbol}_tv.json"
        with open(json_path, "w") as f:
            json.dump(payload, f, indent=2)
        print(
            f"[OK] {json_path}\n"
            "     regime_markers   → series.setMarkers()  regime change + conf\n"
            "     div_markers      → series.setMarkers()  divergence onset + conf\n"
            "     cps_markers      → series.setMarkers()  stealth signal onset\n"
            "     coherence_raw    → addLineSeries() dotted\n"
            "     coherence_smooth → addLineSeries() solid\n"
            "     regime_hist      → addHistogramSeries() background\n"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Argparse
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Trend Participation Engine v5.2 — NSE EOD",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python interactive_analysis.py FINCABLES
  python interactive_analysis.py RELIANCE --days 120 --window 14
  python interactive_analysis.py TCS --json --csv --days 90

Window guide:  10 = responsive   14 = default   20 = balanced   30 = smooth
        """,
    )
    parser.add_argument("symbol",         help="NSE stock symbol")
    parser.add_argument("--days",  "-d",  type=int, default=84)
    parser.add_argument("--window", "-w", type=int, default=10)
    parser.add_argument("--json",  "-j",  action="store_true")
    parser.add_argument("--csv",   "-c",  action="store_true")
    parser.add_argument("--macd",  "-m",  action="store_true", help="Use MACD momentum instead of linear regression")

    args = parser.parse_args()
    run_analysis(
        symbol       = args.symbol.upper(),
        days         = args.days,
        slope_window = args.window,
        emit_json    = args.json,
        emit_csv     = args.csv,
        use_macd     = args.macd,
    )