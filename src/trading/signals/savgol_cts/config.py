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
    """Path 5: Slope Bottom — cts_slope rising from deep negative in downtrend."""
    enabled: bool = True
    slope_threshold: float = -0.1
    slope_delta_min: float = 0.002
    slope_delta_max: float = 0.02
    cwvap_dist_min: float = -3.0
    cwvap_dist_max: float = 0.5
    slope_exhaustion_min: float = -0.22
    open_cwvap_guard: bool = True
    accel_rising_guard: bool = True
    cts_max: float = -0.85
    pdd_guard: bool = True
    pdd_max: float = 0.0
    pure_bear_guard: bool = True
    shallow_guard_enabled: bool = True
    shallow_slope_min: float = -0.12
    shallow_dist_max: float = -1.0


@dataclass
class SlopeBottomExitConfig:
    """Slope Bottom exit: pure slope zero-cross cycle."""
    pnl_cap_enabled: bool = True
    pnl_cap_pct: float = 8.0
    trail_enabled: bool = False
    trail_activation_pct: float = 3.0
    trail_lock_ratio: float = 0.50


@dataclass
class InstitutionalFloorEntryConfig:
    """Path 3: Institutional Floor — Sustained PSZ recovery with institutional alignment."""
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
    """Accel Cross exit: Two-Phase PSZ/CTS Glide."""
    enabled: bool = True
    pnl_cap_enabled: bool = True
    pnl_cap_pct: float = 8.0
    hard_stop_enabled: bool = True
    hard_stop_pct: float = 8.0
    psz_peak_threshold: float = 0.25
    psz_exit_threshold: float = 0.0


@dataclass
class RangeReversionEntryConfig:
    """Path 6: Range Reversion — Mean-reversion on oversold NIFTY 50 stocks."""
    enabled: bool = True
    rp252_max: float = 0.25
    rp63_max: float = 0.30
    bars_at_base_min: int = 5
    rw10_atrs_max: float = 2.0
    cts_max: float = -0.50
    cts_slope_min: float = -0.05


@dataclass
class RangeReversionExitConfig:
    """Range Reversion exit: PSZ zero-cross cycle."""
    enabled: bool = True
    patience_bars: int = 8
    hard_stop_pct: float = 8.0


@dataclass
class AccelZeroCrossEntryConfig:
    """Path 7: Accel Zero Cross — cts_accel crosses zero."""
    enabled: bool = False


@dataclass
class AccelZeroCrossExitConfig:
    """Accel Zero Cross exit: Two-Phase PSZ/CTS Glide with Trailing Stop."""
    enabled: bool = True
    pnl_cap_enabled: bool = False
    pnl_cap_pct: float = 15.0
    hard_stop_enabled: bool = False
    hard_stop_pct: float = 6.0
    trail_enabled: bool = True
    trail_activation_pct: float = 5.0
    trail_lock_ratio: float = 0.50
    psz_peak_threshold: float = 0.25
    psz_exit_threshold: float = 0.0


@dataclass
class PrtSlopeZeroCrossEntryConfig:
    """Path: PRT Slope Zero Cross — PRT slope crosses above zero with FAS alignment."""
    enabled: bool = True
    prt_slope_min: float = 0.04
    fas_min: float = -1.5
    fas_max: float = 0.0
    psz_threshold: float = -0.1
    psz_v_min: float = 0.00
    prt_accel_max: float = 0.1
    cts_accel_delta_min: float = 0.01
    min_score: float = 8.0
    telemetry_enabled: bool = False


@dataclass
class PrtSlopeZeroCrossExitConfig:
    """PRT Slope Zero Cross exit: CWVAP reclaim + FAS trailing."""
    enabled: bool = True
    cwvap_timeout_bars: int = 8
    fas_exit_min: float = -0.1
    fas_exit_max: float = 1.0


@dataclass
class FasZeroCrossEntryConfig:
    """Path: FAS Zero Cross — FAS crosses above zero with active institutional engine."""
    enabled: bool = True
    lookback_size: int = 10
    sensitivity: float = 0.15
    min_score: float = 10.0
    telemetry_enabled: bool = False


@dataclass
class FasZeroCrossExitConfig:
    """FAS Zero Cross exit: Phase-Shift Exit (Anchor + CTS Trail)."""
    enabled: bool = True
    anchor_bars: int = 5
    anchor_fas_floor: float = -0.3


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

    slope_bottom: SlopeBottomEntryConfig = field(default_factory=SlopeBottomEntryConfig)
    accel_cross: AccelCrossEntryConfig = field(default_factory=AccelCrossEntryConfig)
    institutional_floor: InstitutionalFloorEntryConfig = field(default_factory=InstitutionalFloorEntryConfig)
    range_reversion: RangeReversionEntryConfig = field(default_factory=RangeReversionEntryConfig)
    accel_zero_cross: AccelZeroCrossEntryConfig = field(default_factory=AccelZeroCrossEntryConfig)
    prt_slope_zero_cross: PrtSlopeZeroCrossEntryConfig = field(default_factory=PrtSlopeZeroCrossEntryConfig)
    fas_zero_cross: FasZeroCrossEntryConfig = field(default_factory=FasZeroCrossEntryConfig)


# ---------------------------------------------------------------------------
# Exit path configs
# ---------------------------------------------------------------------------

@dataclass
class CwvapGuardConfig:
    """CWVAP price guard logic."""
    tolerance_pct: float = 0.50
    tolerance_bars: int = 1


# ---------------------------------------------------------------------------
# Composite exit config
# ---------------------------------------------------------------------------

@dataclass
class SavgolCTSExitConfig(BaseExitConfig):
    """Configuration for CTS mean-reversion exit signal."""
    st_exit_enabled: bool = False
    st_crossover_tolerance: float = 0.03

    slope_bottom: SlopeBottomExitConfig = field(default_factory=SlopeBottomExitConfig)
    accel_cross: AccelCrossExitConfig = field(default_factory=AccelCrossExitConfig)
    institutional_floor: InstitutionalFloorExitConfig = field(default_factory=InstitutionalFloorExitConfig)
    range_reversion: RangeReversionExitConfig = field(default_factory=RangeReversionExitConfig)
    accel_zero_cross: AccelZeroCrossExitConfig = field(default_factory=AccelZeroCrossExitConfig)
    prt_slope_zero_cross: PrtSlopeZeroCrossExitConfig = field(default_factory=PrtSlopeZeroCrossExitConfig)
    fas_zero_cross: FasZeroCrossExitConfig = field(default_factory=FasZeroCrossExitConfig)
    cwvap_guard: CwvapGuardConfig = field(default_factory=CwvapGuardConfig)
