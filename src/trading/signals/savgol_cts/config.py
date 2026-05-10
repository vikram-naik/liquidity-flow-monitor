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


# ---------------------------------------------------------------------------
# Exit path configs
# ---------------------------------------------------------------------------

@dataclass
class UniversalCrossExitConfig:
    """Universal Cross exit: Pure CTS Trailing Logic."""
    enabled: bool = True
    hard_stop_enabled: bool = True
    hard_stop_pct: float = 8.0


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
