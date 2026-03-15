"""
Module 7.5 — Cumulative Evidence Index (CEI).

Computes a rolling net-directional-evidence score that captures whether
institutional accumulation or distribution pressure is building over time.

Positive CEI → Demand evidence building (accumulation)
Negative CEI → Supply evidence building (distribution)
Near zero    → Neutral

CEI uses signed feature scores weighted by config, modulated by a
structural multiplier (CWC × Coherence) and smoothed via EMA.

Includes a **position_score** feature that makes CEI aware of where price
sits relative to CWVAP (institutional avg cost) and CPOC (volume
concentration). This prevents false positives when CEI reads positive
evidence (e.g. delivery divergence) while price is deep below value.

Outputs per bar:
- **cei_raw**: per-bar weighted signed evidence × structure multiplier
- **cei**: EMA-smoothed cumulative index
- **cei_slope**: linear regression slope of CEI over N bars
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_cei(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Compute the Cumulative Evidence Index and add columns to *df*.

    Parameters
    ----------
    df : pd.DataFrame
        The full ledger with all prior modules computed (Modules 1-7).
    config : dict
        Full scoring config (v2 YAML). Must contain a ``cei`` section.

    Returns
    -------
    pd.DataFrame
        Input DataFrame with added columns: ``cei_raw``, ``cei``, ``cei_slope``.
    """
    cei_cfg = config.get("cei", {})

    # --- Parameters (all config-driven) ---
    ema_span = cei_cfg.get("ema_span", 10)
    slope_window = cei_cfg.get("slope_window", 5)
    features = cei_cfg.get("features", {})

    # Structure multiplier config
    struct_cfg = cei_cfg.get("structure_multiplier", {})
    coh_floor = struct_cfg.get("coherence_floor", 0.5)
    cwc_floor = struct_cfg.get("cwc_floor", 0.5)

    # Position score config
    pos_cfg = cei_cfg.get("position_score", {})
    pos_weight = pos_cfg.get("weight", 0.0)

    n = len(df)

    # --- Pre-compute cumulative divergence if configured ---
    for feat_name, feat_cfg in features.items():
        if feat_name == "cumul_divergence":
            window = feat_cfg.get("window", 20)
            if "accum_div" in df.columns and "distrib_div" in df.columns:
                ca = df["accum_div"].rolling(window, min_periods=window).sum().fillna(0.0)
                cd = df["distrib_div"].rolling(window, min_periods=window).sum().fillna(0.0)
                df["_cei_cumul_div"] = ca - cd
            break

    # --- RSZ–PSZ per-window confirmation ---
    # When RSZ and PSZ deltas disagree in sign, dampen RSZ contribution.
    rsz_confirm_cfg = cei_cfg.get("rsz_psz_confirmation", {})
    rsz_dampening = rsz_confirm_cfg.get("dampening", 1.0)  # 1.0 = no dampening
    rsz_pairs = rsz_confirm_cfg.get("pairs", {})
    # Pre-load PSZ arrays for paired RSZ features
    _psz_arrays: dict[str, np.ndarray] = {}
    if rsz_dampening < 1.0:
        for rsz_feat, psz_feat in rsz_pairs.items():
            if psz_feat in df.columns:
                vals = df[psz_feat].values.astype(float)
                _psz_arrays[rsz_feat] = np.where(np.isnan(vals), 0.0, vals)

    # --- Compute per-bar signed evidence ---
    evidence = np.zeros(n)

    for feat_name, feat_cfg in features.items():
        weight = feat_cfg.get("weight", 0.0)
        max_val = feat_cfg.get("max_value", 1.0)
        if weight == 0 or max_val == 0:
            continue

        # Map feature name to DataFrame column
        col = _resolve_column(feat_name, df)
        if col is None:
            continue

        values = df[col].values.astype(float)
        # Signed score: clip(value / max_value, -1, +1)
        signed = np.clip(np.where(np.isnan(values), 0.0, values) / max_val, -1.0, 1.0)

        # Apply RSZ dampening when paired PSZ disagrees in sign
        if feat_name in _psz_arrays:
            psz_vals = _psz_arrays[feat_name]
            agree = np.sign(values) == np.sign(psz_vals)
            dampen = np.where(agree, 1.0, rsz_dampening)
            signed = signed * dampen

        evidence += weight * signed

    # --- Position score: CWVAP + CPOC awareness ---
    if pos_weight > 0:
        evidence += pos_weight * _compute_position_scores(df, pos_cfg)

    # --- VA Spring Energy: computed for marker override (not evidence sum) ---
    va_spring_cfg = cei_cfg.get("va_spring", {})
    spring_scores = _compute_va_spring_scores(df, va_spring_cfg)

    # --- Structure multiplier: CWC × Coherence ---
    coherence = df["coherence"].values.astype(float) if "coherence" in df.columns else np.ones(n)
    cwc = df["cwc"].values.astype(float) if "cwc" in df.columns else np.ones(n)

    # Replace NaN with 0 for safety
    coherence = np.where(np.isnan(coherence), 0.0, coherence)
    cwc = np.where(np.isnan(cwc), 0.0, cwc)

    coherence_mult = coh_floor + (1.0 - coh_floor) * np.clip(coherence, 0.0, 1.0)
    cwc_mult = cwc_floor + (1.0 - cwc_floor) * np.clip(cwc, 0.0, 1.0)
    structure_mult = coherence_mult * cwc_mult

    # --- CEI raw = evidence × structure multiplier ---
    cei_raw = evidence * structure_mult

    # --- CEI = EMA of cei_raw ---
    cei_series = pd.Series(cei_raw, index=df.index)
    cei_smooth = cei_series.ewm(span=ema_span, adjust=False).mean()

    # --- CEI Slope: linear regression over slope_window bars ---
    cei_vals = cei_smooth.values
    slopes = np.full(n, np.nan)
    if slope_window >= 2:
        x = np.arange(slope_window, dtype=float)
        for i in range(slope_window - 1, n):
            y = cei_vals[i - slope_window + 1: i + 1]
            if not np.any(np.isnan(y)):
                coeffs = np.polyfit(x, y, 1)
                slopes[i] = coeffs[0]

    df["cei_raw"] = np.round(cei_raw, 6)
    df["cei"] = np.round(cei_smooth.values, 6)
    df["cei_slope"] = np.round(slopes, 6)

    # --- Phase 3: CEI-based markers (threshold crossings + cooldown) ---
    markers_cfg = cei_cfg.get("markers", {})
    close_arr = df["close"].values.astype(float)
    cwvap_arr = df["cwvap"].values.astype(float) if "cwvap" in df.columns else np.full(n, np.nan)
    spring_threshold = va_spring_cfg.get("spring_threshold", 0.20)
    df["cei_signal"] = _generate_cei_signals(
        cei_vals, slopes, close_arr, cwvap_arr, markers_cfg,
        cei_raw=cei_raw, spring_scores=spring_scores,
        spring_threshold=spring_threshold,
    )

    # --- Phase 3.5: Assister markers (cei_raw crosses cei EMA) ---
    assister_cfg = markers_cfg.get("assister", {})
    if assister_cfg.get("enabled", True):
        va_high_arr = df["va_high"].values.astype(float) if "va_high" in df.columns else np.full(n, np.nan)
        va_low_arr = df["va_low"].values.astype(float) if "va_low" in df.columns else np.full(n, np.nan)
        signal_list = df["cei_signal"].tolist()
        _overlay_assister_signals(
            signal_list, cei_raw, cei_vals,
            close_arr, cwvap_arr, va_high_arr, va_low_arr,
            assister_cfg,
        )
        df["cei_signal"] = signal_list

    return df


# ------------------------------------------------------------------
# Phase 3: CEI-based markers — CEI zero-crossings + cooldown
# ------------------------------------------------------------------

def _generate_cei_signals(
    cei_vals: np.ndarray,
    slopes: np.ndarray,
    close: np.ndarray,
    cwvap: np.ndarray,
    markers_cfg: dict,
    *,
    cei_raw: np.ndarray | None = None,
    spring_scores: np.ndarray | None = None,
    spring_threshold: float = 0.20,
) -> list[str | None]:
    """Generate Demand/Supply markers based on CEI zero-crossings.

    A **Demand** marker fires when ``cei`` crosses above zero
    (previous bar CEI <= 0, current > 0).

    A **Supply** marker fires when ``cei`` crosses below zero
    (previous bar CEI >= 0, current < 0).
    If ``supply_below_cwvap`` is true, Supply also requires ``close < cwvap``.

    **Spring Override (Design A):** When VA spring score exceeds
    ``spring_threshold``, the marker checks ``cei_raw`` zero-crossings
    instead of EMA-smoothed ``cei``. This lets breakouts after long
    VA containment fire on the actual breakout bar without EMA lag.

    After a marker fires, a cooldown window suppresses repeat markers of
    the **same direction** for ``cooldown_bars`` bars.

    Returns a list of signal strings (same length as input arrays).
    """
    cooldown = markers_cfg.get("cooldown_bars", 5)
    supply_below_cwvap = markers_cfg.get("supply_below_cwvap", True)
    min_gap = markers_cfg.get("min_crossing_gap", 0.0)

    n = len(cei_vals)
    signals: list[str | None] = [None] * n

    has_spring = (spring_scores is not None and cei_raw is not None)

    last_demand_bar = -cooldown - 1  # allow first bar to fire
    last_supply_bar = -cooldown - 1

    for i in range(1, n):
        cei_now = cei_vals[i]
        cei_prev = cei_vals[i - 1]

        if np.isnan(cei_now) or np.isnan(cei_prev):
            continue

        # --- Spring Override: use cei_raw when spring is releasing ---
        use_raw = (
            has_spring
            and abs(spring_scores[i]) >= spring_threshold
            and not np.isnan(cei_raw[i])
            and not np.isnan(cei_raw[i - 1])
        )
        check_now = cei_raw[i] if use_raw else cei_now
        check_prev = cei_raw[i - 1] if use_raw else cei_prev

        # Demand: crosses above zero with minimum conviction
        if check_prev <= 0 < check_now and abs(check_now) >= min_gap and (i - last_demand_bar > cooldown):
            signals[i] = "Demand"
            last_demand_bar = i

        # Supply: crosses below zero with minimum conviction
        elif check_prev >= 0 > check_now and abs(check_now) >= min_gap and (i - last_supply_bar > cooldown):
            # CWVAP gate: Supply only fires when price is below CWVAP
            if supply_below_cwvap:
                c = close[i]
                w = cwvap[i]
                if np.isnan(c) or np.isnan(w) or c >= w:
                    continue
            signals[i] = "Supply"
            last_supply_bar = i

    return signals


# ------------------------------------------------------------------
# Phase 3.5: Assister markers — cei_raw crosses cei (EMA)
# ------------------------------------------------------------------

def _overlay_assister_signals(
    signals: list[str | None],
    cei_raw: np.ndarray,
    cei_ema: np.ndarray,
    close: np.ndarray,
    cwvap: np.ndarray,
    va_high: np.ndarray,
    va_low: np.ndarray,
    assister_cfg: dict,
) -> None:
    """Overlay assister markers onto the existing signal list in-place.

    Assisters fire when ``cei_raw`` crosses the EMA-smoothed ``cei`` line,
    providing earlier trend-change indication than zero-crossings.

    An assister does NOT fire on a bar that already has a primary signal,
    nor within its own cooldown window. Primary signals also reset the
    assister cooldown for the same direction.

    No CWVAP gate — assisters are early warnings, not definitive signals.
    """
    cooldown = assister_cfg.get("cooldown_bars", 5)

    n = len(signals)

    # Track cooldowns dynamically — primary signals reset the assister
    # cooldown for the same direction as we scan forward.
    last_demand_bar = -cooldown - 1
    last_supply_bar = -cooldown - 1

    for i in range(1, n):
        # Primary signals reset the assister cooldown
        if signals[i] == "Demand":
            last_demand_bar = i
            continue
        elif signals[i] == "Supply":
            last_supply_bar = i
            continue
        # Skip bars that already have a primary signal
        if signals[i] is not None:
            continue

        raw_now = cei_raw[i]
        raw_prev = cei_raw[i - 1]
        ema_now = cei_ema[i]
        ema_prev = cei_ema[i - 1]

        if np.isnan(raw_now) or np.isnan(raw_prev) or np.isnan(ema_now) or np.isnan(ema_prev):
            continue

        # Demand assister: cei_raw crosses above cei (EMA)
        if raw_prev <= ema_prev and raw_now > ema_now and (i - last_demand_bar > cooldown):
            signals[i] = "Demand_Assister"
            last_demand_bar = i

        # Supply assister: cei_raw crosses below cei (EMA)
        elif raw_prev >= ema_prev and raw_now < ema_now and (i - last_supply_bar > cooldown):
            signals[i] = "Supply_Assister"
            last_supply_bar = i


# ------------------------------------------------------------------
# Position score — CWVAP / CPOC awareness
# ------------------------------------------------------------------

def _compute_position_scores(df: pd.DataFrame, pos_cfg: dict) -> np.ndarray:
    """Vectorised position score for all bars.

    Signed: positive = Demand-favourable, negative = Supply-favourable.
    Uses percentage distance from CWVAP (institutional avg cost) with a
    sweet-spot peak at -1.5% and decay in both directions, plus a CPOC
    (volume concentration) confirmation modifier.

    All thresholds are config-driven for future UI exposure.
    """
    n = len(df)
    scores = np.zeros(n)

    close = df["close"].values.astype(float)
    cwvap = df["cwvap"].values.astype(float) if "cwvap" in df.columns else np.full(n, np.nan)
    cpoc = df["cpoc"].values.astype(float) if "cpoc" in df.columns else np.full(n, np.nan)

    # Config thresholds (with sensible defaults from backtest)
    cw_sweet_lo = pos_cfg.get("cwvap_sweet_lo", -3.0)
    cw_sweet_peak = pos_cfg.get("cwvap_sweet_peak", -1.5)
    cw_decay_hi = pos_cfg.get("cwvap_decay_hi", 6.0)
    cw_deep_lo = pos_cfg.get("cwvap_deep_lo", -6.0)
    cw_deep_floor = pos_cfg.get("cwvap_deep_floor", -12.0)
    cpoc_sat = pos_cfg.get("cpoc_saturation_pct", 5.0)
    cw_blend = pos_cfg.get("cwvap_blend", 0.6)
    cp_blend = 1.0 - cw_blend

    for i in range(n):
        c, w, p = close[i], cwvap[i], cpoc[i]
        if np.isnan(w) or w <= 0 or np.isnan(c):
            continue

        cw_pct = (c - w) / w * 100.0

        # --- CWVAP component ---
        if cw_sweet_lo <= cw_pct <= 0.0:
            # Sweet spot: ramp up to peak then back down
            half = abs(cw_sweet_peak)
            if cw_pct >= cw_sweet_peak:
                cw_score = 0.5 + (abs(cw_pct) / half) * 0.5
            else:
                span = abs(cw_sweet_lo - cw_sweet_peak)
                cw_score = 1.0 - ((abs(cw_pct) - half) / span) * 0.5 if span > 0 else 0.5
        elif cw_pct > 0.0:
            # Above CWVAP: decay toward -0.5
            cw_score = 0.5 - (cw_pct / 3.0) * 0.5
            if cw_pct >= cw_decay_hi:
                cw_score = -0.5
        elif cw_pct >= cw_deep_lo:
            # Below sweet spot but not deep: 0.5 → -0.3
            span = abs(cw_deep_lo - cw_sweet_lo)
            cw_score = 0.5 - ((abs(cw_pct) - abs(cw_sweet_lo)) / span) * 0.8 if span > 0 else -0.3
        else:
            # Deep below: -0.3 → -1.0
            span = abs(cw_deep_floor - cw_deep_lo)
            excess = min(abs(cw_pct) - abs(cw_deep_lo), span)
            cw_score = -0.3 - (excess / span) * 0.7 if span > 0 else -1.0

        cw_score = max(-1.0, min(1.0, cw_score))

        # --- CPOC component ---
        cp_score = 0.0
        if not np.isnan(p) and p > 0 and cpoc_sat > 0:
            cp_pct = (c - p) / p * 100.0
            cp_score = max(-1.0, min(1.0, cp_pct / cpoc_sat))

        scores[i] = max(-1.0, min(1.0, cw_blend * cw_score + cp_blend * cp_score))

    return scores



# ------------------------------------------------------------------
# VA Spring Energy — stored institutional energy from range containment
# ------------------------------------------------------------------

def _compute_va_spring_scores(df: pd.DataFrame, va_cfg: dict) -> np.ndarray:
    """Vectorised VA Spring Energy score for all bars.

    Measures how long price has been contained inside the Value Area
    (CVAL–CVAH) and converts that stored energy into directional evidence
    when price breaks out.

    Score = va_dwell × va_breakout, where:
      va_dwell    = fraction of last N bars where close was inside VA (0–1)
      va_breakout = signed distance beyond VA boundary, normalised (−1 to +1)
                    Above CVAH → positive (Demand), below CVAL → negative (Supply)
                    Inside VA → 0

    The multiplication ensures both conditions are needed: long dwell alone
    (still inside) scores zero, and a breakout after brief containment scores
    near-zero.
    """
    n = len(df)
    scores = np.zeros(n)

    close = df["close"].values.astype(float)
    cvah = df["va_high"].values.astype(float) if "va_high" in df.columns else (
        df["cvah"].values.astype(float) if "cvah" in df.columns else np.full(n, np.nan))
    cval = df["va_low"].values.astype(float) if "va_low" in df.columns else (
        df["cval"].values.astype(float) if "cval" in df.columns else np.full(n, np.nan))

    dwell_window = va_cfg.get("dwell_window", 60)
    sat_pct = va_cfg.get("saturation_pct", 3.0)
    max_val = va_cfg.get("max_value", 2.0)

    # Pre-compute per-bar inside-VA flag
    inside = np.zeros(n, dtype=float)
    for i in range(n):
        c, vah, val = close[i], cvah[i], cval[i]
        if not np.isnan(vah) and not np.isnan(val) and val <= c <= vah:
            inside[i] = 1.0

    # Rolling dwell fraction
    dwell = np.zeros(n)
    cumsum = np.cumsum(inside)
    for i in range(dwell_window, n):
        dwell[i] = (cumsum[i] - cumsum[i - dwell_window]) / dwell_window

    for i in range(dwell_window, n):
        c, vah, val = close[i], cvah[i], cval[i]
        if np.isnan(vah) or np.isnan(val) or vah <= 0 or val <= 0:
            continue

        # Breakout direction
        if c > vah:
            breakout_pct = (c - vah) / vah * 100.0
            breakout = min(1.0, breakout_pct / sat_pct)
        elif c < val:
            breakout_pct = (val - c) / val * 100.0
            breakout = -min(1.0, breakout_pct / sat_pct)
        else:
            continue  # inside VA → score stays 0

        spring = dwell[i] * breakout
        scores[i] = np.clip(spring / max_val, -1.0, 1.0)

    return scores


# ------------------------------------------------------------------
# Column resolution helpers
# ------------------------------------------------------------------

def _resolve_column(feat_name: str, df: pd.DataFrame) -> str | None:
    """Map a CEI feature name to a DataFrame column.

    Handles special cases like 'divergence' which maps to computed columns,
    and direct column names for deltas/levels.
    """
    # Special: divergence = accum_div - distrib_div (net)
    if feat_name == "divergence":
        if "accum_div" in df.columns and "distrib_div" in df.columns:
            # Pre-compute net divergence column if not present
            if "_cei_net_div" not in df.columns:
                df["_cei_net_div"] = df["accum_div"] - df["distrib_div"]
            return "_cei_net_div"
        return None

    # Special: cumulative divergence (rolling sum of accum - distrib)
    if feat_name == "cumul_divergence":
        return "_cei_cumul_div" if "_cei_cumul_div" in df.columns else None

    # Level features with custom mapping
    level_map = {
        "psz_level": "price_slope_z",
        "rsz_level": "rdv_slope_z",
    }
    col = level_map.get(feat_name, feat_name)

    if col in df.columns:
        return col
    return None
