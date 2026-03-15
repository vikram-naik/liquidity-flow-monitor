"""
Extracted signal logic for entry/exit decisions.

Moved from scripts/backtest_long_signals.py so it can be shared
between backtesting and live scanning.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


FAVORABLE_SHAPES = {
    "accumulation", "recovering", "uptrend_forming", "uptrend_mature", "sideways",
}


# ── Configuration ────────────────────────────────────────────────────────────

@dataclass
class EntryConfig:
    psz_threshold: float = -0.25
    regime_block: tuple = ("downtrend",)
    min_soft_filters: int = 2
    # Soft filter thresholds
    rdv_min: float = 0.8
    mcs_min: float = -0.20
    cwc_min: float = 0.10
    gradient_ok: set = field(default_factory=lambda: FAVORABLE_SHAPES)
    # Quality gate: PDD_120 + RSZ falling
    pdd_120_max: float | None = None      # e.g. -3.6 — None disables gate
    rsz_falling: bool = False              # require RSZ 3-bar delta < 0


@dataclass
class ExitConfig:
    # Hard stop
    stop_atr_multiple: float = 2.0
    # Trailing — CWVAP primary, ATR fallback
    trail_activation_atr: float = 0.5     # min profit to activate trailing
    trail_atr_fallback: float = 1.0       # ATR distance when CWVAP not usable
    cwvap_slope_window: int = 5           # bars to check CWVAP slope
    # Delivery deterioration
    cdvl_exit: float = -0.5
    rdv_exit: float = 0.6
    delivery_confirm_bars: int = 2
    # Coherence breakdown
    cwc_exit: float = -0.20
    # MCS collapse
    mcs_exit: float = -0.50
    mcs_slope_exit: float = -0.03
    # PSZ reversal backstop
    psz_exit_threshold: float = -0.25
    # PSZ momentum failure — exit if PSZ peaked below this and reverses
    psz_peak_min: float = 0.20            # PSZ must reach this to "confirm" momentum
    psz_fail_drop: float = 0.10           # exit if PSZ drops this much from its peak (while peak < psz_peak_min)
    # Time decay
    max_bars: int = 40
    time_decay_min_profit_atr: float = 0.5


@dataclass
class Trade:
    symbol: str
    entry_date: str
    entry_price: float
    entry_idx: int
    atr_at_entry: float
    soft_filters_passed: int
    # Individual filter flags
    rdv_pass: bool = False
    mcs_pass: bool = False
    cwc_pass: bool = False
    grad_pass: bool = False
    regime_at_entry: str = ""
    psz_at_entry: float = 0.0
    psz_peak: float = 0.0             # track highest PSZ during trade
    # Filled on exit
    exit_date: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_pct: float = 0.0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    duration: int = 0


# ── Entry Logic ──────────────────────────────────────────────────────────────

def check_entry(row, prev_row, cfg: EntryConfig, records=None, idx=0) -> tuple[bool, int, dict]:
    """Check if this bar qualifies as a long entry.

    Returns (qualifies, soft_filter_count, filter_details).
    """
    empty = {"rdv": False, "mcs": False, "cwc": False, "grad": False, "regime": ""}
    psz = row.get("price_slope_z", np.nan)
    prev_psz = prev_row.get("price_slope_z", np.nan)

    # PSZ crossing check
    if np.isnan(psz) or np.isnan(prev_psz):
        return False, 0, empty
    if not (prev_psz < cfg.psz_threshold <= psz):
        return False, 0, empty

    # Hard gate: regime
    regime = row.get("regime", "notrend")
    if regime in cfg.regime_block:
        return False, 0, empty

    # Quality gate: PDD_120 + RSZ falling
    if cfg.pdd_120_max is not None:
        pdd_120 = row.get("pdd_120", np.nan)
        if np.isnan(pdd_120) or pdd_120 >= cfg.pdd_120_max:
            return False, 0, empty
    if cfg.rsz_falling and records is not None and idx >= 3:
        rsz_now = row.get("rdv_slope_z", np.nan)
        rsz_3ago = records[idx - 3].get("rdv_slope_z", np.nan)
        if np.isnan(rsz_now) or np.isnan(rsz_3ago) or rsz_now >= rsz_3ago:
            return False, 0, empty

    # Soft filters
    rdv_ok = row.get("rdv", 0) >= cfg.rdv_min
    mcs_ok = row.get("mcs_composite", -1) > cfg.mcs_min
    cwc_ok = row.get("cwc", -1) > cfg.cwc_min
    grad_ok = row.get("gradient_shape", "") in cfg.gradient_ok
    soft = sum([rdv_ok, mcs_ok, cwc_ok, grad_ok])

    details = {"rdv": rdv_ok, "mcs": mcs_ok, "cwc": cwc_ok, "grad": grad_ok, "regime": regime}
    return soft >= cfg.min_soft_filters, soft, details


# ── Exit Logic ───────────────────────────────────────────────────────────────

def check_exit(
    row: dict,
    trade: Trade,
    peak_close: float,
    bars_held: int,
    delivery_bad_count: int,
    cwvap_values: list[float],
    cfg: ExitConfig,
) -> tuple[str | None, int]:
    """Check all exit conditions. Returns (reason or None, updated delivery_bad_count)."""

    close = row["close"]
    entry = trade.entry_price
    atr_pct = trade.atr_at_entry / entry if entry > 0 else 0.02

    pnl_pct = (close / entry - 1) * 100
    peak_pnl_pct = (peak_close / entry - 1) * 100

    # Track PSZ peak during trade
    psz = row.get("price_slope_z", 0)
    if psz > trade.psz_peak:
        trade.psz_peak = psz

    # 1. Hard stop-loss
    if pnl_pct < -(cfg.stop_atr_multiple * atr_pct * 100):
        return "hard_stop", 0

    # 2. Trailing stop (CWVAP primary, ATR fallback)
    activation_pct = cfg.trail_activation_atr * atr_pct * 100
    if peak_pnl_pct >= activation_pct:
        # Check if CWVAP is rising — use it as trail
        cwvap = row.get("cwvap", np.nan)
        cwvap_rising = False
        if len(cwvap_values) >= cfg.cwvap_slope_window and not np.isnan(cwvap):
            recent = cwvap_values[-cfg.cwvap_slope_window:]
            if all(not np.isnan(v) for v in recent):
                cwvap_rising = recent[-1] > recent[0]

        va_low = row.get("va_low", np.nan)
        if cwvap_rising and not np.isnan(va_low) and cwvap > entry:
            # Trail at VA Low — exit if close drops below value area
            if close < va_low:
                return "trail_cwvap", 0
        else:
            # Fallback: ATR-based trail below peak
            trail_level = peak_close * (1 - cfg.trail_atr_fallback * atr_pct)
            if close < trail_level:
                return "trail_atr", 0

    # 3. Delivery deterioration (consecutive bars)
    cdvl = row.get("cdvl", 0)
    rdv = row.get("rdv", 1)
    if cdvl < cfg.cdvl_exit and rdv < cfg.rdv_exit:
        delivery_bad_count += 1
    else:
        delivery_bad_count = 0

    if delivery_bad_count >= cfg.delivery_confirm_bars:
        return "delivery", 0

    # 4. Coherence breakdown
    if row.get("cwc", 1) < cfg.cwc_exit:
        return "coherence", delivery_bad_count

    # 5. MCS collapse
    mcs = row.get("mcs_composite", 0)
    mcs_slope = row.get("mcs_composite_slope", 0)
    if mcs < cfg.mcs_exit and mcs_slope < cfg.mcs_slope_exit:
        return "mcs_collapse", delivery_bad_count

    # 6. PSZ reversal backstop
    if psz < cfg.psz_exit_threshold:
        return "psz_reversal", delivery_bad_count

    # 6b. PSZ momentum failure — PSZ never confirmed and is fading
    if bars_held >= 3 and trade.psz_peak < cfg.psz_peak_min:
        if psz < (trade.psz_peak - cfg.psz_fail_drop):
            return "psz_momentum_fail", delivery_bad_count

    # 7. Time decay
    if bars_held >= cfg.max_bars:
        if pnl_pct < cfg.time_decay_min_profit_atr * atr_pct * 100:
            return "time_decay", delivery_bad_count

    return None, delivery_bad_count
