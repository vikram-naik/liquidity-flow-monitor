"""Per-path entry/exit configuration dataclasses for SavgolCTS signal.

The top-level ``SavgolCTSEntryConfig`` and ``SavgolCTSExitConfig`` compose
the active Universal Master Path configs alongside shared parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.trading.signals.base import BaseEntryConfig, BaseExitConfig
from src.trading.signals.enums import ExitReason


# ---------------------------------------------------------------------------
# Entry path configs
# ---------------------------------------------------------------------------

@dataclass
class UniversalCrossEntryConfig:
    """Universal ML Master Path — Catches any structural inflection and relies purely on ML Guard."""
    enabled: bool = True
    min_ml_score: float = 85.0
    gap_down_lookback: int = 10  # Lookback window for recent gap downs (bars)
    cwc_basing_filter_enabled: bool = True
    cwc_basing_cwc_threshold: float = 0.10
    cwc_basing_slope_threshold: float = -0.02





# ---------------------------------------------------------------------------
# Flow Momentum entry config
# ---------------------------------------------------------------------------

@dataclass
class FlowMomentumEntryConfig:
    """Flow Momentum entry path configuration (Optimized)."""
    enabled: bool = True
    rsz_v_cross_thresh: float = 0.015
    psz_v_min: float = 0.04
    psz_v_rising_bars: int = 1
    cwc_slope_min: float = 0.0
    coherence_min: float = 0.30
    only_bullish_regime: bool = True
    mcs_composite_min: float = 0.05


@dataclass
class CoherentPullbackEntryConfig:
    """Coherent Pullback entry path configuration to capture early momentum inflections."""
    enabled: bool = True
    cwc_min: float = 0.50
    pdd_30_min: float = -5.00
    pdd_30_max: float = -2.50
    range_pos_10_max: float = 0.40
    pdd_120_min: float = -4.00
    base_tightness_max: float = 0.45
    spearman_10_min: float = -0.90
    only_bullish_regime: bool = True
    score: int = 82


@dataclass
class AnchorShockPullbackEntryConfig:
    """Anchor-Shock-Pullback entry configuration leveraging advanced price-volume indicators."""
    enabled: bool = True
    
    # Volume & Spread Dry-up
    shock_max: float = -0.8            # Deep delivery volume dry-up z-score
    esr_max: float = 0.10              # Spread efficiency ceiling (consolidation/absorption)
    
    # Proximity discount under Swing-Anchored DVWAP (S-DVWAP)
    dist_thresh: float = 0.02          # Maximum distance below S-DVWAP (2.0%)
    
    # Price-DVL Divergence (PDD) safety gates
    pdd_120_min: float = 2.0           # Secular structure floor (avoids deep downtrends)
    pdd_30_min: float = -3.0           # Falling knife limit (avoids short-term crashes)
    
    # Price Slope Momentum Floor
    psz_min: float = -0.25             # Price Slope Z-Score floor (avoids sharp falling knives)
    
    # Volatility / Trading Range Width Gate
    rw_252_min: float = 20.0           # Minimum 252-day high-to-low range width in % (requires rebound energy)
    
    # Range Position (avoid high-altitude peaks)
    rp_252_max: float = 0.70           # Lowered ceiling to avoid distribution peaks
    
    score: int = 83




# ---------------------------------------------------------------------------
# Composite entry config
# ---------------------------------------------------------------------------

@dataclass
class SavgolCTSEntryConfig(BaseEntryConfig):
    """Configuration for CTS mean-reversion entry signal."""
    cooldown_enabled: bool = False
    cooldown_bars: int = 10
    cooldown_exit_reasons: tuple[ExitReason, ...] = (
        ExitReason.SUPPRESSED_EXIT,
        ExitReason.BAR3_STOP,
        ExitReason.BAR5_STOP,
    )

    universal_cross: UniversalCrossEntryConfig = field(default_factory=UniversalCrossEntryConfig)
    trend_pullback_enabled: bool = True
    flow_momentum: FlowMomentumEntryConfig = field(default_factory=FlowMomentumEntryConfig)
    coherent_pullback: CoherentPullbackEntryConfig = field(default_factory=CoherentPullbackEntryConfig)
    anchor_shock_pullback: AnchorShockPullbackEntryConfig = field(default_factory=AnchorShockPullbackEntryConfig)


# ---------------------------------------------------------------------------
# Exit path configs
# ---------------------------------------------------------------------------

@dataclass
class UniversalCrossExitConfig:
    """Universal Cross exit: Pure CTS Trailing Logic."""
    enabled: bool = True
    hard_stop_enabled: bool = False
    hard_stop_pct: float = 8.0
    gap_down_enabled: bool = False
    gap_down_atr_mult: float = 0.30
    cwvap_lost_enabled: bool = False
    negative_pnl_timeout_enabled: bool = False
    negative_pnl_timeout_days: int = 15
    pnl_cap_enabled: bool = False
    pnl_cap_threshold: float = 8.0

    panic_exit_suppression_enabled: bool = True
    panic_exit_rdv_threshold: float = 2.0
    prt_slope_exit_enabled: bool = True
    prt_st_cross_enabled: bool = True
    cts_st_cross_enabled: bool = True
    cwc_slope_neg_exit_enabled: bool = False
    cwc_neg_exit_enabled: bool = False

    # CTS Near-Miss Rollover Exit parameters
    cts_near_miss_exit_enabled: bool = True
    cts_near_miss_gap: float = 0.10
    cts_near_miss_rollover_level: float = 0.50



@dataclass
class CwvapGuardConfig:
    """CWVAP price guard logic."""
    tolerance_pct: float = 0.50
    tolerance_bars: int = 1
    candle_guard_enabled: bool = False
    max_upper_wick_pct: float = 0.65  # Max wick size relative to full range before rejection exit
    min_ibs_rejection: float = 0.15   # IBS floor for rejection
    vol_lookback: int = 20            # Lookback for average volume
    inside_bar_guard_enabled: bool = False
    inside_bar_vol_mult: float = 1.5  # Volume multiplier for inside bar rejection
    climax_guard_enabled: bool = True
    climax_rp_threshold: float = 0.95    # Requires RP_63 and RP_252 > 0.95
    climax_cwvap_dist: float = 10.0      # Requires distance > 10%
    climax_fas_threshold: float = 1.11    # OR FAS > 1.0
    cwc_slope_early_release_enabled: bool = True
    cwc_slope_early_release_threshold: float = -0.01
    va_high_breakout_suppression_enabled: bool = True
    # Climax VA High trail guards
    climax_va_intraday_guard_enabled: bool = True  # Suppress trail release when high > va_high (wick scenario)



@dataclass
class AnchorShockPullbackExitConfig:
    """Anchor-Shock-Pullback exit configuration."""
    enabled: bool = True
    shock_exit_enabled: bool = False
    shock_exit_threshold: float = 2.5  # Volume climax exit level
    
    # Protection fallbacks
    hard_stop_enabled: bool = True
    hard_stop_pct: float = 15.0        # Emergency catastrophe shield stop loss
    time_decay_enabled: bool = True
    max_hold_bars: int = 50            # Maximizes breathing room while cutting off grinding pullbacks



# ---------------------------------------------------------------------------
# Composite exit config
# ---------------------------------------------------------------------------

@dataclass
class SavgolCTSExitConfig(BaseExitConfig):
    """Configuration for CTS mean-reversion exit signal."""
    st_exit_enabled: bool = False
    st_crossover_tolerance: float = 0.03

    universal_cross: UniversalCrossExitConfig = field(default_factory=UniversalCrossExitConfig)
    cwvap_guard: CwvapGuardConfig = field(default_factory=CwvapGuardConfig)
    anchor_shock_pullback: AnchorShockPullbackExitConfig = field(default_factory=AnchorShockPullbackExitConfig)

