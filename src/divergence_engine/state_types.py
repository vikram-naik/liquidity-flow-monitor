"""
Type-safe enums and data model for the Integrated State Matrix.

Replaces magic strings with proper enum types for trajectory quadrants,
value zones, and state names. MarketContext is an immutable snapshot of
one bar's classification inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# Trajectory Quadrants
# ---------------------------------------------------------------------------

class Trajectory(Enum):
    """Which directional quadrant the price/RDV angle pair falls in."""
    BOTH_RISING   = "both_rising"
    PRICE_ONLY    = "price_rising_only"
    RDV_ONLY      = "rdv_rising_only"
    BOTH_FALLING  = "both_falling"
    MIXED         = "mixed"

    @staticmethod
    def from_angles(price_angle: float, rdv_angle: float) -> "Trajectory":
        p_up = price_angle > 0.0
        r_up = rdv_angle > 0.0
        if p_up and r_up:
            return Trajectory.BOTH_RISING
        if p_up and not r_up:
            return Trajectory.PRICE_ONLY
        if not p_up and r_up:
            return Trajectory.RDV_ONLY
        if price_angle < 0.0 and rdv_angle < 0.0:
            return Trajectory.BOTH_FALLING
        return Trajectory.MIXED


# ---------------------------------------------------------------------------
# Value Zones
# ---------------------------------------------------------------------------

class ValueZone(Enum):
    """Price position relative to CWVAP and CPOC."""
    DEEP_DISCOUNT   = "Deep Discount"
    SLIGHT_DISCOUNT = "Slight Discount"
    FAIR_CHOP       = "Fair Value (Chop)"
    FAIR_MIXED      = "Fair Value (Mixed)"
    SLIGHT_PREMIUM  = "Slight Premium"
    HIGH_PREMIUM    = "High Premium"

    @property
    def label(self) -> str:
        return self.value

    @staticmethod
    def classify(
        cwvap_dist: float,
        cpoc_dist: float,
        chop_band: float = 1.5,
        premium_boundary: float = 3.0,
        discount_boundary: float = -3.0,
    ) -> "ValueZone":
        """Determine the value zone from CWVAP/CPOC distances.

        Parameters are thresholds that can be user-tuned.
        """
        cw, cp = cwvap_dist, cpoc_dist

        if abs(cw) <= chop_band and abs(cp) <= chop_band:
            return ValueZone.FAIR_CHOP

        if cw > premium_boundary and cp > premium_boundary:
            return ValueZone.HIGH_PREMIUM
        if cw < discount_boundary and cp < discount_boundary:
            return ValueZone.DEEP_DISCOUNT
        if cw > 0 and cp > 0:
            return ValueZone.SLIGHT_PREMIUM
        if cw < 0 and cp < 0:
            return ValueZone.SLIGHT_DISCOUNT
        return ValueZone.FAIR_MIXED


# ---------------------------------------------------------------------------
# State Names
# ---------------------------------------------------------------------------

class StateName(Enum):
    """All possible integrated state outcomes."""
    # Bullish
    V_BOTTOM_REVERSAL    = "V-Bottom Reversal"
    VALUE_BREAKOUT       = "Value Breakout"
    CONFIRMED_MARKUP     = "Confirmed Markup"
    # Cautionary
    EXHAUSTION_WARNING   = "Exhaustion Warning"
    # Bearish
    DISTRIBUTION_TOP     = "Distribution Top"
    DEAD_CAT_BOUNCE      = "Dead Cat Bounce"
    ACTIVE_DISTRIBUTION  = "Active Distribution"
    VALUE_BREAKDOWN      = "Value Breakdown"
    CONFIRMED_MARKDOWN   = "Confirmed Markdown"
    # Volume divergence
    STEALTH_ACCUMULATION = "Stealth Accumulation"
    # Default
    NEUTRAL_MIXED        = "Neutral / Mixed"

    @property
    def label(self) -> str:
        return self.value

    @property
    def is_bullish(self) -> bool:
        return self in {
            StateName.V_BOTTOM_REVERSAL,
            StateName.VALUE_BREAKOUT,
            StateName.CONFIRMED_MARKUP,
            StateName.STEALTH_ACCUMULATION,
        }

    @property
    def is_bearish(self) -> bool:
        return self in {
            StateName.DISTRIBUTION_TOP,
            StateName.DEAD_CAT_BOUNCE,
            StateName.ACTIVE_DISTRIBUTION,
            StateName.VALUE_BREAKDOWN,
            StateName.CONFIRMED_MARKDOWN,
        }


# ---------------------------------------------------------------------------
# Market Context (immutable bar snapshot)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketContext:
    """Immutable snapshot of one bar's classification inputs."""
    trajectory: Trajectory
    value_zone: ValueZone
    price_z: float
    rdv_z: float
    mfm: float
    coherence: float
