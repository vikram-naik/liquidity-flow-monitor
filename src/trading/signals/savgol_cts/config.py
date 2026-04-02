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
class StructuralDivergenceEntryConfig:
    """Path 2: Structural Divergence — Volume exhaustion during price drop.
    
    Entry fires when:
    - Exhaustion: price_slope_z <= psz_max AND cts <= cts_max
    - Divergence: (rdv_slope_z - price_slope_z) >= spread_min OR accum_div > accum_div_min
    - Inflection: cts_slope < 0 AND cts_accel > accel_min
    - Anti-capitulation: cwc <= cwc_max (avoid unified institutional dumping)
    - Below CWVAP: cwvap_dist <= cwvap_dist_max
    """
    enabled: bool = True
    psz_max: float = -0.20
    cts_max: float = -0.50
    spread_min: float = 0.35
    accum_div_min: float = 0.04
    accel_min: float = 0.0
    cwc_max: float = 0.50
    cwvap_dist_max: float = 0.5


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
class StructuralDivergenceExitConfig:
    """Structural Divergence exit: Smart path for counter-trend entries.
    
    Exits when:
    - Hard stop is hit (e.g. price falls significantly further).
    - Time decay: If trade goes nowhere for N bars.
    - PnL cap: Takes profit early on mean-reversion pops.
    """
    pnl_cap_enabled: bool = True
    pnl_cap_pct: float = 6.0        # slightly tighter cap for counter-trend
    time_decay_bars: int = 8        # exit if it doesn't bounce in 8 bars
    time_decay_min_pnl: float = 1.0 # only hold past 8 bars if PnL > 1%
    hard_stop_pct: float = 5.0      # max acceptable loss


# ---------------------------------------------------------------------------
# Composite entry config
# ---------------------------------------------------------------------------

@dataclass
class SavgolCTSEntryConfig(BaseEntryConfig):
    """Configuration for CTS mean-reversion entry signal.

    Entry paths evaluated:
    1. Slope Bottom   — cts_slope inflects from deep negative in a downtrend.
    2. Structural Div — Volume exhaustion and delivery divergence during sharp drop.
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
    structural_divergence: StructuralDivergenceEntryConfig = field(default_factory=StructuralDivergenceEntryConfig)


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
    structural_divergence: StructuralDivergenceExitConfig = field(default_factory=StructuralDivergenceExitConfig)
    cwvap_guard: CwvapGuardConfig = field(default_factory=CwvapGuardConfig)
