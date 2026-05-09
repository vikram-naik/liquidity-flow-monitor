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
class InstitutionalFloorEntryConfig:
    """Path 3: Institutional Floor — Sustained PSZ recovery with institutional alignment."""
    enabled: bool = False
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
    conviction_enabled: bool = True
    conviction_min_score: int = 5
    conv_cwvap_deep: float = -3.0
    conv_cwvap_mid: float = -1.5
    conv_psz_delta_strong: float = 0.02
    conv_psz_delta_mid: float = 0.01
    conv_spread_strong: float = 0.60
    conv_spread_mid: float = 0.45
    ml_guard_enabled: bool = True
    min_ml_score: float = 35.0


@dataclass
class InstitutionalFloorExitConfig:
    """Institutional Floor exit: target CWVAP reclaim and PSZ peak trail."""
    enabled: bool = True
    pnl_cap_enabled: bool = True
    pnl_cap_pct: float = 8.0
    hard_stop_enabled: bool = True
    hard_stop_pct: float = 8.0
    psz_peak_threshold: float = 0.25
    psz_exit_threshold: float = 0.0


@dataclass
class AccelCrossEntryConfig:
    """Path 2: Accel Cross — Triple-trend momentum cross with institutional alignment."""
    enabled: bool = False
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
    ml_guard_enabled: bool = True
    min_ml_score: float = 70.0


@dataclass
class AccelCrossExitConfig:
    """Accel Cross exit: Two-Phase PSZ/CTS Glide."""
    enabled: bool = True
    pnl_cap_enabled: bool = False
    pnl_cap_pct: float = 8.0
    hard_stop_enabled: bool = True
    hard_stop_pct: float = 8.0
    psz_peak_threshold: float = 0.25
    psz_exit_threshold: float = 0.0


@dataclass
class RangeReversionEntryConfig:
    """Path 6: Range Reversion — Mean-reversion on oversold NIFTY 50 stocks."""
    enabled: bool = False
    rp252_max: float = 0.25
    rp63_max: float = 0.30
    bars_at_base_min: int = 5
    rw10_atrs_max: float = 2.0
    cts_max: float = -0.50
    cts_slope_min: float = -0.05
    ml_guard_enabled: bool = True
    min_ml_score: float = 80.0


@dataclass
class RangeReversionExitConfig:
    """Range Reversion exit: PSZ zero-cross cycle."""
    enabled: bool = True
    patience_bars: int = 8
    hard_stop_pct: float = 8.0


@dataclass
class FasZeroCrossEntryConfig:
    """Path: FAS Zero Cross — FAS crosses above zero with active institutional engine."""
    enabled: bool = False
    lookback_size: int = 10
    sensitivity: float = 0.15
    psz_v_min: float = 0.0
    pdd_min: float = 0.3
    coherence_min: float = 0.4
    gap_pct_max: float = 1.0
    cts_max: float = 0.0
    min_score: float = 15.0
    ml_guard_enabled: bool = True
    min_ml_score: float = 30.0
    telemetry_enabled: bool = False


@dataclass
class FasZeroCrossExitConfig:
    """FAS Zero Cross exit: Pure CTS Trailing Logic."""
    enabled: bool = True
    fas_climax_threshold: float = 1.0
    fas_climax_tolerance: float = 0.1
    cts_st_tolerance: float = 0.05


@dataclass
class FasFloorReversionEntryConfig:
    """Path 10: FAS Floor Reversion — FAS deep floor with extreme exhaustion."""
    enabled: bool = False
    fas_max: float = -1.15
    prt_slope_min: float = 0.0
    cts_max: float = -0.99
    cts_slope_max: float = 0.0
    ml_guard_enabled: bool = True
    min_ml_score: float = 40.0


@dataclass
class FasFloorReversionExitConfig:
    """FAS Floor Reversion exit: CTS trailing (ST-cross down)."""
    enabled: bool = True
    hard_stop_enabled: bool = True
    hard_stop_pct: float = 8.0


@dataclass
class FasBuyCrossEntryConfig:
    """Path 11: FAS Buy Cross — FAS crosses BT while CTS is in deep floor."""
    enabled: bool = False
    cts_max: float = -0.80
    cts_bt_max: float = -0.80
    min_score: float = 9.0
    price_spearman_lookback: int = 10
    price_spearman_max: float = -0.85
    prt_slope_min: float = -0.02
    prt_structural_min: float = -0.45
    prt_slope_max: float = 0.10
    ml_guard_enabled: bool = True
    min_ml_score: float = 60.0
    telemetry_enabled: bool = False


@dataclass
class FasBuyCrossExitConfig:
    """FAS Buy Cross exit: Trail CTS or FAS, whichever crosses ST from above first."""
    enabled: bool = True
    hard_stop_enabled: bool = False
    hard_stop_pct: float = 8.0
    cwvap_fail_threshold: int = 13


@dataclass
class CtsFloorReversionEntryConfig:
    """Path 12: CTS Floor Reversion — CTS and BT stuck at floor for 5 days, followed by snapback."""
    enabled: bool = False
    min_score: float = 9.0
    range_guard_enabled: bool = True
    shallow_drop_guard_enabled: bool = True
    prt_structural_min: float = -0.45
    prt_slope_min: float = -0.02
    ml_guard_enabled: bool = True
    min_ml_score: float = 80.0
    telemetry_enabled: bool = False


@dataclass
class CtsFloorReversionExitConfig:
    """CTS Floor Reversion exit: CTS trailing (ST-cross down)."""
    enabled: bool = True
    hard_stop_enabled: bool = False
    hard_stop_pct: float = 8.0
    pnl_cap_enabled: bool = False
    pnl_cap_pct: float = 10.0


@dataclass
class CtsAccelCrossEntryConfig:
    """Path 13: CTS Accel Cross — High-conviction crossover with robust structural guards."""
    enabled: bool = False
    # Range Guards
    rp10_max: float = 0.60
    rp252_max: float = 0.70
    rp10_deep_thr: float = 0.40
    rp252_deep_thr: float = 0.50
    # Institutional Dislocation
    cts_max: float = -0.20
    prior_reset_lookback: int = 10
    # Momentum / Accel
    accel_spread_min: float = 0.02
    accel_spread_prt_bypass_delta: float = 0.20 # Delta required in prt_slope to bypass spread min
    accel_lookback: int = 5
    accel_peak_guard_type: str = "proximity" # Approved Refinement (Scenario 2)
    accel_peak_proximity_limit: float = 0.15 # 15% retracement allowed
    psz_v_peak_guard_type: str = "proximity" # Approved Refinement
    psz_v_peak_proximity_limit: float = 0.30 # 30% retracement allowed (NESTLEIND)
    psz_v_lookback: int = 5
    accum_div_max: float = 0.010 # Tightened from 0.015 to catch AXISBANK trap
    spearman_threshold: float = -0.3
    # Price Guard
    price_spearman_lookback: int = 10
    price_spearman_max: float = -0.85
    prt_slope_min: float = -0.02
    prt_structural_min: float = -0.50
    dist_high_10_max: float = -2.0 # Minimum 2% correction from 10-day high (NESTLEIND)
    # Scoring
    min_score: float = 15.0
    ml_guard_enabled: bool = True
    min_ml_score: float = 50.0
    telemetry_enabled: bool = False


@dataclass
class CtsAccelCrossExitConfig:
    """Path 13: CTS Accel Cross exit — Standard cross-down + Bare-Touch persistence."""
    enabled: bool = True
    bare_touch_tolerance: float = 0.05
    cwvap_rejection_limit: int = 5
    hard_stop_enabled: bool = True
    hard_stop_pct: float = 8.0
    pnl_cap_enabled: bool = False
    pnl_cap_pct: float = 10.0


@dataclass
class PrtZeroCrossEntryConfig:
    """Path: PRT Zero Cross with ML Guard."""
    enabled: bool = False
    min_ml_score: float = 80.0


@dataclass
class PrtZeroCrossExitConfig:
    """PRT Zero Cross exit: Pure CTS Trailing Logic."""
    enabled: bool = True
    hard_stop_enabled: bool = True
    hard_stop_pct: float = 8.0


@dataclass
class UniversalCrossEntryConfig:
    """Universal ML Master Path — Catches any structural inflection and relies purely on ML Guard."""
    enabled: bool = True
    min_ml_score: float = 85.0


# ---------------------------------------------------------------------------
# Composite entry config
# ---------------------------------------------------------------------------

@dataclass
class SavgolCTSEntryConfig(BaseEntryConfig):
    """Configuration for CTS mean-reversion entry signal."""
    cooldown_enabled: bool = True
    cooldown_bars: int = 10
    cooldown_exit_reasons: tuple[ExitReason, ...] = (
        ExitReason.SUPPRESSED_EXIT,
        ExitReason.BAR3_STOP,
        ExitReason.BAR5_STOP,
    )

    universal_cross: UniversalCrossEntryConfig = field(default_factory=UniversalCrossEntryConfig)
    cts_floor_reversion: CtsFloorReversionEntryConfig = field(default_factory=CtsFloorReversionEntryConfig)
    accel_cross: AccelCrossEntryConfig = field(default_factory=AccelCrossEntryConfig)
    institutional_floor: InstitutionalFloorEntryConfig = field(default_factory=InstitutionalFloorEntryConfig)
    range_reversion: RangeReversionEntryConfig = field(default_factory=RangeReversionEntryConfig)
    fas_zero_cross: FasZeroCrossEntryConfig = field(default_factory=FasZeroCrossEntryConfig)
    fas_floor_reversion: FasFloorReversionEntryConfig = field(default_factory=FasFloorReversionEntryConfig)
    fas_buy_cross: FasBuyCrossEntryConfig = field(default_factory=FasBuyCrossEntryConfig)
    cts_accel_cross: CtsAccelCrossEntryConfig = field(default_factory=CtsAccelCrossEntryConfig)
    prt_zero_cross: PrtZeroCrossEntryConfig = field(default_factory=PrtZeroCrossEntryConfig)


# ---------------------------------------------------------------------------
# Exit path configs
# ---------------------------------------------------------------------------

@dataclass
class CwvapGuardConfig:
    """CWVAP price guard logic."""
    tolerance_pct: float = 0.50
    tolerance_bars: int = 1
    candle_guard_enabled: bool = True
    max_upper_wick_pct: float = 0.65  # Max wick size relative to full range before rejection exit
    min_ibs_rejection: float = 0.15   # IBS floor for rejection
    vol_lookback: int = 20            # Lookback for average volume
    inside_bar_guard_enabled: bool = True
    inside_bar_vol_mult: float = 1.5  # Volume multiplier for inside bar rejection
    climax_guard_enabled: bool = True
    climax_rp_threshold: float = 0.95    # Requires RP_63 and RP_252 > 0.95
    climax_cwvap_dist: float = 10.0      # Requires distance > 10%
    climax_fas_threshold: float = 1.11    # OR FAS > 1.0


# ---------------------------------------------------------------------------
# Composite exit config
# ---------------------------------------------------------------------------

@dataclass
class SavgolCTSExitConfig(BaseExitConfig):
    """Configuration for CTS mean-reversion exit signal."""
    st_exit_enabled: bool = False
    st_crossover_tolerance: float = 0.03

    cts_floor_reversion: CtsFloorReversionExitConfig = field(default_factory=CtsFloorReversionExitConfig)
    accel_cross: AccelCrossExitConfig = field(default_factory=AccelCrossExitConfig)
    institutional_floor: InstitutionalFloorExitConfig = field(default_factory=InstitutionalFloorExitConfig)
    range_reversion: RangeReversionExitConfig = field(default_factory=RangeReversionExitConfig)
    fas_zero_cross: FasZeroCrossExitConfig = field(default_factory=FasZeroCrossExitConfig)
    fas_floor_reversion: FasFloorReversionExitConfig = field(default_factory=FasFloorReversionExitConfig)
    fas_buy_cross: FasBuyCrossExitConfig = field(default_factory=FasBuyCrossExitConfig)
    cts_accel_cross: CtsAccelCrossExitConfig = field(default_factory=CtsAccelCrossExitConfig)
    prt_zero_cross: PrtZeroCrossExitConfig = field(default_factory=PrtZeroCrossExitConfig)
    cwvap_guard: CwvapGuardConfig = field(default_factory=CwvapGuardConfig)
