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
class FloorTouchEntryConfig:
    """Path 0: Floor Touch — enter while CTS and BT are both pinned at floor.

    Fires when both CTS and BT are at or below the floor zone and PSZ is
    deeply oversold (psz < psz_max).  Captures entries earlier than
    Floor-Leave.
    Empirically (NIFTY 500): 64.8% WR, +2.50% avg, >10% winner rate 30%.
    """
    enabled: bool = False
    psz_max: float = -0.25       # PSZ must be below this (deeply oversold)


@dataclass
class FloorLeaveEntryConfig:
    """Path 1: CTS-floor-leave — CTS rising above floor after being pinned.

    Fires on the first bar CTS clears the floor zone (cts > floor) after
    having been at or below it (prev_cts <= floor).  No BT constraint.
    Empirically (NIFTY 500): 63.9% WR, +2.52% avg, 73.1% ceiling exits.
    """
    cts_max: float = -0.6        # Vertical Jump Guard ceiling
    pszv_min: float = 0.05       # Conviction Gate: minimum |PSZV|
    pszv_direction_gate: bool = True  # Require psz_v > 0 (momentum turning up)
    cwvap_trap_hi: float = -5.0  # Signal-Day Trap Floor (% below CWVAP)
    enabled: bool = False


@dataclass
class BtCrossEntryConfig:
    """Path 3: BT-cross — CTS crosses BT from below in oversold zone.

    Catches V-bottoms where CTS hits floor but BT hasn't caught up.
    """
    enabled: bool = True
    oversold_threshold: float = -0.50
    # Dead-cat bounce gate: reject when psz_v is strongly positive (price bouncing)
    # but price is deep below CWVAP (institutional supply overhead).
    # Empirically: 39% floor-hit rate, 32.7% WR in this combo.
    dead_cat_gate_enabled: bool = True
    dead_cat_pszv_min: float = 0.05    # psz_v above this = price bouncing
    dead_cat_cwvap_max: float = -3.0   # cwvap_dist below this = deep under supply
    # Flat psz_v gate: reject when psz_v has been quiescent (straight-line
    # fall, no bend).  Study: scripts/study_pszv_flat_lookback.py.
    flat_gate_enabled: bool = True
    flat_gate_threshold: float = 0.02
    flat_gate_lookback: int = 3


@dataclass
class CwvapReclaimEntryConfig:
    """Path 4: CWVAP Reclaim — cts_slope turns positive while close > CWVAP.

    Entry fires when:
    - cts_slope crosses zero from below (prev <= 0, now > 0)
    - close > CWVAP (price has reclaimed institutional average)
    - PSZ > psz_min (momentum confirmation)
    """
    enabled: bool = True
    psz_min: float = 0.0  # minimum PSZ at entry


@dataclass
class CwvapReclaimExitConfig:
    """CWVAP Reclaim exit: close drops below CWVAP."""
    pass


# ---------------------------------------------------------------------------
# Composite entry config
# ---------------------------------------------------------------------------

@dataclass
class SavgolCTSEntryConfig(BaseEntryConfig):
    """Configuration for CTS mean-reversion entry signal.

    Three entry paths evaluated in priority order:

    0. Floor Touch  — CTS + BT both pinned at floor, PSZ deeply oversold.
    1. Floor Leave  — CTS rises above floor after being pinned.
    2. BT-Cross     — CTS crosses BT from below while oversold.

    Shared parameters live here; path-specific parameters are nested in the
    per-path config dataclass.
    """
    # --- Shared across multiple entry paths ---
    cts_floor: float = -1.0
    floor_zone_tolerance: float = 0.02   # tolerance around cts_floor
    psz_min_threshold: float = -0.25     # late-entry PSZ gate (floor_leave, bt_cross)
    cwvap_max_dist: float = -5.0         # CWVAP distance guard (floor_touch)
    dvwap_bear_stack_gate_enabled: bool = True  # DVWAP bearish alignment gate

    # Cooldown: prevent entry within N bars after specific exit reasons.
    cooldown_enabled: bool = True
    cooldown_bars: int = 10
    cooldown_exit_reasons: tuple[ExitReason, ...] = (
        ExitReason.SUPPRESSED_EXIT,
        ExitReason.FLOOR_HIT,
        ExitReason.BT_HIT,
        ExitReason.BAR3_STOP,
    )

    # --- Per-path configs ---
    floor_touch: FloorTouchEntryConfig = field(default_factory=FloorTouchEntryConfig)
    floor_leave: FloorLeaveEntryConfig = field(default_factory=FloorLeaveEntryConfig)
    bt_cross: BtCrossEntryConfig = field(default_factory=BtCrossEntryConfig)
    cwvap_reclaim: CwvapReclaimEntryConfig = field(default_factory=CwvapReclaimEntryConfig)


# ---------------------------------------------------------------------------
# Exit path configs
# ---------------------------------------------------------------------------

@dataclass
class BtCrossExitConfig:
    """BT-cross specific exit parameters."""
    floor_tolerance: float = 0.10    # floor zone protection width
    psz_stall_enabled: bool = False  # early exit when PSZ momentum stalls
    psz_stall_check_bar: int = 2     # bar at which to check for stall
    # Bar-3 PnL stop: exit if trade PnL < threshold at exactly bar 3.
    # Empirically (NIFTY 500): 81% save rate, +0.24x payoff improvement.
    bar3_stop_enabled: bool = True
    bar3_stop_bar: int = 3           # bar at which to check
    bar3_stop_threshold: float = -1.0  # exit if PnL% below this


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
    - Ceiling-leave: CTS drops below (1.0 - tolerance) after reaching it.
    - Floor hit:     CTS returns to -1.0 after having risen (safety net).
    - BT hit:        CTS drops back to buy threshold (mean-reversion done).
    - PSZ glide:     PSZ drops below threshold after having been above it.

    The CWVAP guard runs *after* all path-specific exits and may suppress
    or release the proposed exit based on price/momentum context.
    """
    # --- Shared exit parameters ---
    ceiling_leave_tolerance: float = 0.02
    floor_hit_min_bars: int = 5      # grace period before floor exit
    bt_hit_min_bars: int = 5         # grace period before BT exit
    psz_glide_threshold: float = 0.30
    st_exit_enabled: bool = False    # universal Sell Threshold toggle
    st_crossover_tolerance: float = 0.03

    # --- Per-path configs ---
    bt_cross: BtCrossExitConfig = field(default_factory=BtCrossExitConfig)
    cwvap_reclaim: CwvapReclaimExitConfig = field(default_factory=CwvapReclaimExitConfig)
    cwvap_guard: CwvapGuardConfig = field(default_factory=CwvapGuardConfig)
