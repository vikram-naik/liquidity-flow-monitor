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
class CdvlCtsEntryConfig:
    """CDVL-CTS entry path configuration (High Performance Flow-Momentum)."""
    enabled: bool = True
    cwc_min: float = 0.35
    bayesian_mode: bool = True
    score_threshold: float = 0.5358
    feature_bins: dict = field(default_factory=lambda: {
        'cdvl': [-float('inf'), 0.0344, 0.0699, float('inf')],
        'cdvl_surge': [-float('inf'), 0.0783, 0.14, float('inf')],
        'cts': [-float('inf'), -1.0, -0.788, float('inf')],
        'cts_accel': [-float('inf'), 0.0257, 0.0506, float('inf')],
        'cwc': [-float('inf'), 0.475, 0.629, float('inf')],
        'cts_accel_surge': [-float('inf'), 0.00965, 0.025, float('inf')],
    })
    feature_weights: dict = field(default_factory=lambda: {
        'cdvl': [
            (-float('inf'), 0.0344, 0.382992),
            (0.0344, 0.0699, -0.405465),
            (0.0699, float('inf'), 0.105361),
        ],
        'cdvl_surge': [
            (-float('inf'), 0.0783, 0.382992),
            (0.0783, 0.14, -0.656780),
            (0.14, float('inf'), 0.382992),
        ],
        'cts': [
            (-float('inf'), -1.0, -0.087011),
            (-1.0, -0.788, 0.064539),
            (-0.788, float('inf'), 0.105361),
        ],
        'cts_accel': [
            (-float('inf'), 0.0257, 0.382992),
            (0.0257, 0.0506, -0.154151),
            (0.0506, float('inf'), -0.154151),
        ],
        'cwc': [
            (-float('inf'), 0.475, 0.382992),
            (0.475, 0.629, 0.382992),
            (0.629, float('inf'), -0.656780),
        ],
        'cts_accel_surge': [
            (-float('inf'), 0.00965, -0.405465),
            (0.00965, 0.025, 0.693147),
            (0.025, float('inf'), -0.154151),
        ],
    })


@dataclass
class UniversalCrossEntryConfig:
    """Universal ML Master Path — Catches any structural inflection and relies purely on ML Guard."""
    enabled: bool = True
    min_ml_score: float = 85.0
    gap_down_lookback: int = 10  # Lookback window for recent gap downs (bars)
    cwc_basing_filter_enabled: bool = True
    cwc_basing_cwc_threshold: float = 0.10
    cwc_basing_slope_threshold: float = -0.02
    bayesian_mode: bool = True
    score_threshold: float = 0.3540
    feature_bins: dict = field(default_factory=lambda: {
        'cwc': [-float('inf'), 0.495, 0.743, float('inf')],
        'psz_v': [-float('inf'), 0.0314, 0.0625, float('inf')],
        'fas': [-float('inf'), -0.601, -0.339, float('inf')],
        'cts_accel': [-float('inf'), 0.0248, 0.04, float('inf')],
        'pdd_30': [-float('inf'), -4.263, -1.449, float('inf')],
        'range_pos_10': [-float('inf'), 0.289, 0.458, float('inf')],
    })
    feature_weights: dict = field(default_factory=lambda: {
        'cwc': [
            (-float('inf'), 0.495, -0.092465),
            (0.495, 0.743, 0.248461),
            (0.743, float('inf'), -0.193371),
        ],
        'psz_v': [
            (-float('inf'), 0.0314, -0.193371),
            (0.0314, 0.0625, 0.207639),
            (0.0625, float('inf'), -0.054725),
        ],
        'fas': [
            (-float('inf'), -0.601, 0.384263),
            (-0.601, -0.339, -0.438255),
            (-0.339, float('inf'), 0.005900),
        ],
        'cts_accel': [
            (-float('inf'), 0.0248, 0.102279),
            (0.0248, 0.04, -0.228463),
            (0.04, float('inf'), 0.102279),
        ],
        'pdd_30': [
            (-float('inf'), -4.263, 0.197168),
            (-4.263, -1.449, -0.228463),
            (-1.449, float('inf'), 0.005900),
        ],
        'range_pos_10': [
            (-float('inf'), 0.289, 0.197168),
            (0.289, 0.458, 0.063058),
            (0.458, float('inf'), -0.297456),
        ],
    })





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
    enabled: bool = False
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
    enabled: bool = False
    
    # Volume & Spread Dry-up
    shock_max: float = -0.4            # Deep delivery volume dry-up z-score (relaxed from -0.8)
    esr_max: float = 0.20              # Spread efficiency ceiling (relaxed from 0.10)
    
    # Proximity discount under Swing-Anchored DVWAP (S-DVWAP)
    dist_thresh: float = 0.04          # Maximum distance below S-DVWAP (relaxed from 2.0%)
    
    # Price-DVL Divergence (PDD) safety gates
    pdd_120_min: float = 0.0           # Secular structure floor (relaxed from 2.0)
    pdd_30_min: float = -4.5           # Falling knife limit (relaxed from -3.0)
    
    # Price Slope Momentum Floor
    psz_min: float = -0.50             # Price Slope Z-Score floor (relaxed from -0.25)
    
    # Volatility / Trading Range Width Gate
    rw_252_min: float = 15.0           # Minimum 252-day high-to-low range width in % (relaxed from 20.0%)
    
    # Range Position (avoid high-altitude peaks)
    rp_252_max: float = 0.85           # Lowered ceiling to avoid distribution peaks (relaxed from 0.70)
    
    score: int = 83
    
    # Bayesian adaptive scoring parameters
    bayesian_mode: bool = True
    score_threshold: float = 0.7323
    
    feature_bins: dict = field(default_factory=lambda: {
        'pdd_120': [-float('inf'), 2.936, 6.014, float('inf')],
        'pdd_30': [-float('inf'), -1.393, -0.0731, float('inf')],
        'proximity': [-float('inf'), 0.0058, 0.0161, float('inf')],
        'dv_shock': [-float('inf'), -0.809, -0.581, float('inf')],
        'esr': [-float('inf'), 0.0265, 0.0392, float('inf')],
        'price_slope_z': [-float('inf'), -0.295, -0.182, float('inf')],
    })
    
    feature_weights: dict = field(default_factory=lambda: {
        'pdd_120': [
            (-float('inf'), 2.936, 0.047683),
            (2.936, 6.014, 0.204451),
            (6.014, float('inf'), -0.227593),
        ],
        'pdd_30': [
            (-float('inf'), -1.393, -0.009302),
            (-1.393, -0.0731, 0.204451),
            (-0.0731, float('inf'), -0.174256),
        ],
        'proximity': [
            (-float('inf'), 0.0058, -0.009302),
            (0.0058, 0.0161, -0.191063),
            (0.0161, float('inf'), 0.226430),
        ],
        'dv_shock': [
            (-float('inf'), -0.809, 0.076618),
            (-0.809, -0.581, 0.056815),
            (-0.581, float('inf'), -0.120162),
        ],
        'esr': [
            (-float('inf'), 0.0265, -0.037384),
            (0.0265, 0.0392, -0.083231),
            (0.0392, float('inf'), 0.135459),
        ],
        'price_slope_z': [
            (-float('inf'), -0.295, -0.065212),
            (-0.295, -0.182, 0.000000),
            (-0.182, float('inf'), 0.076618),
        ],
    })





@dataclass
class SpringBoardEntryConfig:
    """SpringBoard entry path configuration to capture structural bottom inflections."""
    enabled: bool = True
    capitulation_threshold: float = -0.75  # CTS capitulation level to look back for
    capitulation_lookback: int = 10         # Lookback window for capitulation (bars)
    range_pos_63_max: float = 0.40         # Price must be in lower portion of range
    cwc_slope_min: float = -0.05           # Coherence must not be degrading rapidly
    psz_v_min: float = -0.16               # Stabilizing price velocity
    spearman_5_min: float = -0.95          # Falling knife protection
    score: int = 84
    
    # Bayesian adaptive scoring parameters
    bayesian_mode: bool = True
    score_threshold: float = 0.5401
    
    feature_bins: dict = field(default_factory=lambda: {
        'range_pos_63': [-float('inf'), 0.2, 0.32, float('inf')],
        'min_cts_10': [-float('inf'), -0.85, -0.78, float('inf')],
        'cts_surge': [-float('inf'), 0.08, 0.18, float('inf')],
        'cwc_slope': [-float('inf'), -0.01, 0.02, float('inf')],
        'psz_v': [-float('inf'), -0.08, 0.02, float('inf')],
        'spearman_5': [-float('inf'), -0.85, -0.6, float('inf')],
    })
    
    feature_weights: dict = field(default_factory=lambda: {
        'range_pos_63': [
            (-float('inf'), 0.2, -0.012579),
            (0.2, 0.32, 0.109699),
            (0.32, float('inf'), -0.083881),
        ],
        'min_cts_10': [
            (-float('inf'), -0.85, 0.014118),
            (-0.85, -0.78, -0.068993),
            (-0.78, float('inf'), -0.223144),
        ],
        'cts_surge': [
            (-float('inf'), 0.08, -0.171148),
            (0.08, 0.18, -0.074108),
            (0.18, float('inf'), 0.220543),
        ],
        'cwc_slope': [
            (-float('inf'), -0.01, -0.219629),
            (-0.01, 0.02, 0.087011),
            (0.02, float('inf'), 0.076540),
        ],
        'psz_v': [
            (-float('inf'), -0.08, 0.251314),
            (-0.08, 0.02, -0.066691),
            (0.02, float('inf'), 0.011905),
        ],
        'spearman_5': [
            (-float('inf'), -0.85, 0.0),
            (-0.85, -0.6, 0.130620),
            (-0.6, float('inf'), -0.037504),
        ],
    })


@dataclass
class OversoldDecelEntryConfig:
    """Oversold Deceleration Path (ODP) entry configuration (highly optimized)."""
    enabled: bool = True
    das_thresh: float = -2.0      # Volatility stretch threshold
    decel_thresh: float = 0.04    # 3-bar price deceleration floor
    cwc_min: float = 0.40         # Minimum cross-window coherence
    fas_min: float = -0.20        # Minimum flow accumulation score
    bt_max: float = 0.38          # Maximum base tightness (consolidated bottom check)
    dv_shock_min: float = 0.0     # Disabled by default
    rdv_min: float = 0.0
    filter_regime: bool = False   # Allow downtrends (No) to capture structural inflections
    score: int = 80







@dataclass
class BayesianSubModelConfig:
    """Regime-specific Bayesian sub-model parameters."""
    score_threshold: float = 0.0
    feature_bins: dict = field(default_factory=dict)
    feature_weights: dict = field(default_factory=dict)


@dataclass
class CustomBayesianEntryConfig:
    """Symbol-specific custom trained Bayesian entry path configuration."""
    enabled: bool = False
    score_threshold: float = 0.0
    feature_bins: dict = field(default_factory=dict)
    feature_weights: dict = field(default_factory=dict)
    accumulation: BayesianSubModelConfig = field(default_factory=BayesianSubModelConfig)
    momentum: BayesianSubModelConfig = field(default_factory=BayesianSubModelConfig)


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

    cdvl_cts: CdvlCtsEntryConfig = field(default_factory=CdvlCtsEntryConfig)
    universal_cross: UniversalCrossEntryConfig = field(default_factory=UniversalCrossEntryConfig)
    trend_pullback_enabled: bool = True
    flow_momentum: FlowMomentumEntryConfig = field(default_factory=FlowMomentumEntryConfig)
    coherent_pullback: CoherentPullbackEntryConfig = field(default_factory=CoherentPullbackEntryConfig)
    anchor_shock_pullback: AnchorShockPullbackEntryConfig = field(default_factory=AnchorShockPullbackEntryConfig)
    springboard: SpringBoardEntryConfig = field(default_factory=SpringBoardEntryConfig)
    oversold_decel: OversoldDecelEntryConfig = field(default_factory=OversoldDecelEntryConfig)
    custom_bayesian: CustomBayesianEntryConfig = field(default_factory=CustomBayesianEntryConfig)

# ---------------------------------------------------------------------------
# Exit path configs
# ---------------------------------------------------------------------------

@dataclass
class UniversalCrossExitConfig:
    """Universal Cross exit: Pure CTS Trailing Logic."""
    enabled: bool = True
    hard_stop_enabled: bool = True
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

    # CTS Near-Miss Rollover Exit parameters
    cts_near_miss_exit_enabled: bool = True
    cts_near_miss_gap: float = 0.10
    cts_near_miss_rollover_level: float = 1.10  # Option D: exit on any rollover of CTS once near-miss is flagged



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

    # Trend Reclaim Logic
    trend_reclaim_enabled: bool = True

    # Expert 5 exits (Regime-Aware Hybrid)
    expert_exits_enabled: bool = True
    peak_pnl_trigger: float = 10.0
    uptrend_atr_mult: float = 3.0
    normal_atr_mult: float = 2.0
    uptrend_cwc_min: float = 0.10
    uptrend_cwc_slope_min: float = -0.06
    normal_cwc_min: float = 0.25
    normal_cwc_slope_min: float = -0.04
    normal_psz_v_min: float = -0.20
    overextended_rp_threshold: float = 0.90
    uptrend_low_break_buffer_atr: float = 0.30
    normal_rp_reversion: float = 0.70
    parabolic_cwc_min: float = 0.35
    parabolic_cwc_slope_min: float = -0.02



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


