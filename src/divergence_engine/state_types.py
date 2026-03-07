"""
Type-safe enums and data model for the Conviction-Gated Marker System.

Two markers (Demand / Supply) with conviction scoring, replacing the
11-state Integrated State Matrix.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StateName(Enum):
    """Conviction-gated marker outcomes."""
    DEMAND = "Demand"
    SUPPLY = "Supply"
    NO_SIGNAL = "No Signal"

    @property
    def label(self) -> str:
        return self.value

    @property
    def is_bullish(self) -> bool:
        return self is StateName.DEMAND

    @property
    def is_bearish(self) -> bool:
        return self is StateName.SUPPLY


@dataclass(frozen=True)
class MarketContext:
    """Immutable snapshot of one bar's classification inputs."""
    cwvap_dist: float       # sign = direction (neg=Demand, pos=Supply)
    cwc: float              # gate: must be <= threshold
    rdv: float              # gate: must be >= threshold
    rdv_consistency: int    # gate: days with above-avg delivery in last 5
    delivery_pct: float     # scoring input
    pdd_30: float           # scoring input
    coherence: float        # kept for display
