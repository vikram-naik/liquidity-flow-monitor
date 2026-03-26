from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TrendDirection(str, Enum):
    """Overall directional position of the series."""
    RISING = "Rising"
    FALLING = "Falling"
    SIDEWAYS = "Sideways"
    STEEP_RISING = "Steep Rising"
    STEEP_FALLING = "Steep Falling"


class BendType(str, Enum):
    """The recent curvature/bend of the series for the latest point."""
    BENDING_UP = "Bending Up"
    BENDING_DOWN = "Bending Down"
    STRAIGHT = "Straight"
    FLATTENING = "Flattening"


class ExtremaType(str, Enum):
    """Type of extremum."""
    PEAK = "Peak"
    TROUGH = "Trough"


@dataclass
class ExtremaPoint:
    """Represents an identified peak or trough in the series."""
    index: int
    value: float
    extrema_type: ExtremaType
    prominence: float
    distance_to_latest: int


@dataclass
class EmpiricalThresholds:
    """Data-driven thresholds extracted from the series characteristics."""
    peak_threshold: float
    trough_threshold: float
    mid_zone_upper: float
    mid_zone_lower: float


@dataclass
class ExtremaAnalysis:
    """Summary of peaks and troughs for a series."""
    nearest_peak: Optional[ExtremaPoint] = None
    nearest_trough: Optional[ExtremaPoint] = None
    most_significant_peak: Optional[ExtremaPoint] = None
    most_significant_trough: Optional[ExtremaPoint] = None
    is_near_peak: bool = False
    is_near_trough: bool = False


@dataclass
class TrendAnalysis:
    """Complete trend inference payload."""
    direction: TrendDirection
    bend_type: BendType
    trend_strength: float  # [0.0, 1.0]
    is_steep: bool
    thresholds: EmpiricalThresholds
    extrema: ExtremaAnalysis
