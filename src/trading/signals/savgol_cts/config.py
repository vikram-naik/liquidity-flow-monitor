"""Per-path entry/exit configuration dataclasses for SavgolCTS signal.

Each entry and exit path has its own config dataclass with sensible defaults.
The top-level ``SavgolCTSEntryConfig`` and ``SavgolCTSExitConfig`` compose
these per-path configs alongside shared parameters used across multiple paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.trading.signals.base import BaseEntryConfig, BaseExitConfig
from src.trading.signals.enums import ExitReason


# ---------------------------------------------------------------------------
# Entry path configs
# ---------------------------------------------------------------------------

@dataclass
class SlopeBottomEntryConfig:
    """Path 5: Slope Bottom — cts_slope rising from deep negative in downtrend.

    Entry fires when:
    - cts_slope <= slope_threshold (-0.187, P5 empirical bottom)
    - cts_slope is rising (current > previous)
    - regime == "downtrend"
    - slope_delta <= slope_delta_max (reject violent dead-cat bounces)
    - cwvap_dist in [cwvap_dist_min, cwvap_dist_max] (meaningful distance below CWVAP)

    Empirically (NIFTY 500, 2024-04 to 2026-03): 218 trades, 48.6% WR,
    1.84x payoff, +2.27% expectancy with pure slope zero-cross exit.
    """
    enabled: bool = True
    slope_threshold: float = -0.1     # P5 bottom threshold
    slope_delta_min: float = 0.002       # conviction gate (reject noise)
    slope_delta_max: float = 0.02       # reject violent bounces (dead cats)
    cwvap_dist_min: float = -3.0       # not too far below CWVAP (%)
    cwvap_dist_max: float = 0.5        # must be meaningfully below CWVAP (%)
    slope_exhaustion_min: float = -0.22 # floor for signal-day slope (avoid infinite falls)
    open_cwvap_guard: bool = True      # reject gap ups above CWVAP
    accel_rising_guard: bool = True    # reject dropping or negative accel
    cts_max: float = -0.85             # Require deep exhaustion (not mid-bounce)
    pdd_guard: bool = True             # toggle PDD institutional exhaustion guard
    pdd_max: float = 0.0               # reject when pdd_120 > max (institutions still distributing)
    pure_bear_guard: bool = True       # reject entries on pure red, lower-close days (falling knife guard)
    
    # Shallow Inflection Guard: if mathematical exhaustion is shallow (cts_slope > min), price must be deeply exhausted (cwvap_dist < max)
    shallow_guard_enabled: bool = True
    shallow_slope_min: float = -0.12
    shallow_dist_max: float = -1.0


@dataclass
class SlopeBottomExitConfig:
    """Slope Bottom exit: pure slope zero-cross cycle.

    After entry, wait for cts_slope to cross above zero, then exit when
    it drops back below zero. The full slope cycle captures the reversal
    and exits when momentum fades.
    """
    pnl_cap_enabled: bool = True
    pnl_cap_pct: float = 8.0  # take profit when PnL% >= this
    # MFE-based trailing stop: locks a fraction of peak PnL once
    # the running MFE exceeds activation threshold.
    trail_enabled: bool = False
    trail_activation_pct: float = 3.0    # activate once running MFE >= 3%
    trail_lock_ratio: float = 0.50       # lock 50% of peak PnL as floor


@dataclass
class InstitutionalFloorEntryConfig:
    """Path 3: Institutional Floor — Sustained PSZ recovery with institutional alignment.

    Entry fires when (9-gate process):
    1. Sustained Exhaustion: Previous 3 bars PSZ <= psz_threshold.
    2. Inflection: Signal bar PSZ >= psz_threshold + psz_delta.
    3. Displacement: Close price < CWVAP.
    4. Momentum Acceleration: psz_v strictly increasing over psz_v_lookback.
    5. Velocity Delta: Each acceleration step >= psz_v_delta.
    6. Institutional Dislocation: cts <= cts_buy_threshold.
    7. Institutional Improvement: cts_slope > prev_cts_slope.
    8. Contrarian Guard: cts_slope < 0.
    9. Institutional Alignment: cwc_slope > 0.
    """
    enabled: bool = True
    psz_threshold: float = -0.30
    psz_delta: float = 0.01
    psz_lookback: int = 3
    psz_v_lookback: int = 3
    psz_v_delta: float = 0.01
    cwvap_dist_max: float = 0.0
    cts_buy_guard: bool = True
    cts_slope_accel_guard: bool = True
    accel_rising_guard: bool = True
    cts_slope_neg_guard: bool = True
    cwc_slope_rising_guard: bool = True

    # Conviction scoring
    conviction_enabled: bool = True
    conviction_min_score: int = 5
    conv_cwvap_deep: float = -3.0
    conv_cwvap_mid: float = -1.5
    conv_psz_delta_strong: float = 0.02
    conv_psz_delta_mid: float = 0.01
    conv_spread_strong: float = 0.60
    conv_spread_mid: float = 0.45


@dataclass
class InstitutionalFloorExitConfig:
    """Institutional Floor exit: Bespoke exit replicating the NIFTY 50 study.
    
    Exits when:
    - Target: Price reclaims CWVAP, PSZ climbs above peak threshold, then falls below exit threshold (glides to zero).
    - Safety: Hard stop to prevent infinite holding (unlike the study).
    - PnL cap: Takes profit early if 8% target hit.
    """
    enabled: bool = True
    pnl_cap_enabled: bool = True
    pnl_cap_pct: float = 8.0
    hard_stop_enabled: bool = True
    hard_stop_pct: float = 8.0
    psz_peak_threshold: float = 0.25
    psz_exit_threshold: float = 0.0


@dataclass
class AccelCrossEntryConfig:
    """Path 2: Accel Cross — Triple-trend momentum cross with institutional alignment.

    Entry fires when:
    - cts_slope crosses above zero.
    - cts_accel is rising and above adaptive threshold.
    - psz in (0.0, 0.2].
    - cts in (0.0, 0.5].
    - cwvap_dist <= 8.0%.
    - cwc_slope > 0.
    - Conviction Score 20-29.
    """
    enabled: bool = True
    cts_min: float = 0.0
    cts_max: float = 0.5
    psz_min: float = 0.0
    psz_max: float = 0.2
    cwvap_dist_max: float = 8.0
    cwc_slope_min: float = 0.0
    score_min: int = 20
    score_max: int = 29
    flat_bars_boom: int = 4
    chain_len_trend: int = 3


@dataclass
class AccelCrossExitConfig:
    """Accel Cross exit: Two-Phase PSZ/CTS Glide (Mirroring Institutional Floor).

    Exits when:
    - Target: Price momentum (PSZ) cycles above peak then falls below exit threshold.
    - CTS Trail: If momentum fades but institutional alignment holds, wait for ST_CROSS.
    - Safety: Hard stop and PnL cap active.
    """
    enabled: bool = True
    pnl_cap_enabled: bool = True
    pnl_cap_pct: float = 8.0
    hard_stop_enabled: bool = True
    hard_stop_pct: float = 8.0
    psz_peak_threshold: float = 0.25
    psz_exit_threshold: float = 0.0


@dataclass
class StructuralInflectionEntryConfig:
    """Path 4: Structural Inflection (Diamond) — High-conviction mean-reversion.

    Entry fires when (8-gate process):
    1. PSZ crosses above 0.
    2. CTS Slope crosses above 0.
    3. PSZ velocity rising for 3 bars.
    4. CTS slope rising for 3 bars.
    5. CWC slope > 0.
    6. Price displacement: cwvap_dist < 0.5%.
    7. Institutional floor: cts > -0.5.
    8. Momentum surge: 4-bar total_slope_delta > 0.05.
    """
    enabled: bool = False
    v_lookback: int = 3
    s_lookback: int = 3
    cwvap_dist_max: float = 0.5
    cts_floor: float = -0.5
    total_slope_delta_min: float = 0.05


@dataclass
class RangeReversionEntryConfig:
    """Path 6: Range Reversion — Mean-reversion on oversold NIFTY 50 stocks.

    Entry fires when (10-gate process):
    1. rp_252 < 0.25 (Near 52-week low)
    2. rp_63 < 0.30 (Quarterly range beaten down)
    3. rp_10 > rp_10_prev (Short-term inflecting upward)
    4. close > prev_close (Green candle)
    5. bars_at_base >= 5 (Base formed)
    6. rw10_in_atrs < 2.0 (ATR-relative range tight)
    7. cts < -0.50 (Institutional capitulation confirmed)
    8. psz_v > 0 (Momentum velocity improving)
    9. NOT cts_slope < -0.05 (Institutions not in freefall)
    10. NOT (cts_accel < 0 AND falling) (Selling not accelerating)
    """
    enabled: bool = True
    rp252_max: float = 0.25
    rp63_max: float = 0.30
    bars_at_base_min: int = 5
    rw10_atrs_max: float = 2.0
    cts_max: float = -0.50
    cts_slope_min: float = -0.05


@dataclass
class RangeReversionExitConfig:
    """Range Reversion exit: PSZ zero-cross cycle.

    After entry, wait for PSZ to cross above zero, then exit when
    it drops back below zero.
    """
    enabled: bool = True
    patience_bars: int = 8
    hard_stop_pct: float = 8.0

@dataclass
class PositionSwingEntryConfig:
    """Path 7: Position Swing — Deep value in a long-term structural uptrend."""
    enabled: bool = True
    rp63_max: float = 0.4
    pdd_min: float = 0.0
    cts_accel_min: float = 0.02
    cwc_slope_min: float = 0.0
    mkt_psz_min: float = -0.5
    coherence_min: float = 0.0

@dataclass
class PositionSwingExitConfig:
    """Position Swing exit: ATR-based trailing and time failure."""
    enabled: bool = True
    stop_atr: float = 3.5
    trail_start_pnl: float = 5.0
    trail_atr: float = 2.5
    time_fail_bars: int = 20
    time_fail_pnl: float = 0.0


# ---------------------------------------------------------------------------
# Composite entry config
# ---------------------------------------------------------------------------

@dataclass
class SavgolCTSEntryConfig(BaseEntryConfig):
    """Configuration for CTS mean-reversion entry signal.

    Entry paths evaluated:
    1. Slope Bottom   — cts_slope inflects from deep negative in a downtrend.
    2. Accel Cross    — Triple-trend momentum cross with institutional alignment.
    3. Institutional Floor — Sustained PSZ recovery with institutional alignment.
    4. Structural Inflection — Momentum cross with institutional alignment.
    5. Range Reversion — Price range mean-reversion with institutional alignment.
    """
    # Cooldown: prevent entry within N bars after specific exit reasons.
    cooldown_enabled: bool = True
    cooldown_bars: int = 10
    cooldown_exit_reasons: tuple[ExitReason, ...] = (
        ExitReason.SUPPRESSED_EXIT,
        ExitReason.BAR3_STOP,
        ExitReason.BAR5_STOP,
    )

    # --- Per-path configs ---
    slope_bottom: SlopeBottomEntryConfig = field(default_factory=SlopeBottomEntryConfig)
    accel_cross: AccelCrossEntryConfig = field(default_factory=AccelCrossEntryConfig)
    institutional_floor: InstitutionalFloorEntryConfig = field(default_factory=InstitutionalFloorEntryConfig)
    structural_inflection: StructuralInflectionEntryConfig = field(default_factory=StructuralInflectionEntryConfig)
    range_reversion: RangeReversionEntryConfig = field(default_factory=RangeReversionEntryConfig)
    position_swing: PositionSwingEntryConfig = field(default_factory=PositionSwingEntryConfig)


# ---------------------------------------------------------------------------
# Exit path configs
# ---------------------------------------------------------------------------


@dataclass
class CwvapGuardConfig:
    """CWVAP price guard — post-exit suppression / release logic.

    While above CWVAP with positive momentum, structural exits are
    suppressed.  When momentum fades or price drops below tolerance,
    the suppressed exit is released.
    """
    tolerance_pct: float = 0.50  # allowable % dip below CWVAP
    tolerance_bars: int = 1      # max bars below CWVAP within tolerance


# ---------------------------------------------------------------------------
# Composite exit config
# ---------------------------------------------------------------------------

@dataclass
class SavgolCTSExitConfig(BaseExitConfig):
    """Configuration for CTS mean-reversion exit signal.

    Exit triggers:
    - CWVAP Lost:    Close drops below CWVAP + ATR margin.
    - Slope Cycle:   CTS slope crosses above and then below zero.
    - Bar Stops:     Early exit based on PnL at specific bar count.
    - PnL Cap:       Exit when PnL reaches a specific target.

    The CWVAP guard runs *after* all path-specific exits and may suppress
    or release the proposed exit based on price/momentum context.
    """
    # --- Shared exit parameters ---
    st_exit_enabled: bool = False    # universal Sell Threshold toggle
    st_crossover_tolerance: float = 0.03

    # --- Per-path configs ---
    slope_bottom: SlopeBottomExitConfig = field(default_factory=SlopeBottomExitConfig)
    accel_cross: AccelCrossExitConfig = field(default_factory=AccelCrossExitConfig)
    institutional_floor: InstitutionalFloorExitConfig = field(default_factory=InstitutionalFloorExitConfig)
    range_reversion: RangeReversionExitConfig = field(default_factory=RangeReversionExitConfig)
    position_swing: PositionSwingExitConfig = field(default_factory=PositionSwingExitConfig)
    cwvap_guard: CwvapGuardConfig = field(default_factory=CwvapGuardConfig)
