import numpy as np
import pandas as pd
from scipy.signal import savgol_filter, argrelextrema, peak_prominences
from typing import Optional

from .models import (
    TrendDirection,
    BendType,
    ExtremaType,
    ExtremaPoint,
    EmpiricalThresholds,
    ExtremaAnalysis,
    TrendAnalysis,
)


class SavitzkyGolayAnalyzer:
    """
    Trend analyzer using Savitzky-Golay polynomial fitting.
    Extracts smoothed trend, velocity, acceleration, empirical thresholds,
    and identifies significant peaks and troughs.
    """

    def __init__(self, window_length: int = 7, polyorder: int = 2):
        """
        :param window_length: The length of the filter window (must be odd).
        :param polyorder: The order of the polynomial used to fit the samples.
        """
        if window_length % 2 == 0:
            window_length += 1
        self.window_length = window_length
        self.polyorder = polyorder

    def analyze(self, series: pd.Series) -> Optional[TrendAnalysis]:
        """
        Analyze a pandas Series to extract trend inferences.
        Returns None if series is too short or contains invalid data.
        """
        series = series.dropna()
        n = len(series)
        if n < self.window_length:
            return None

        # 1. Smoothing and Derivatives
        y = series.values

        # Smoothed series
        y_smooth = savgol_filter(y, self.window_length, self.polyorder, deriv=0)
        # 1st derivative (Velocity)
        velocity = savgol_filter(y, self.window_length, self.polyorder, deriv=1)
        # 2nd derivative (Acceleration)
        acceleration = savgol_filter(y, self.window_length, self.polyorder, deriv=2)

        # 2. Empirical Thresholds
        thresholds = self._calculate_thresholds(y)

        # 3. Extrema Analysis
        extrema = self._analyze_extrema(y_smooth)

        # 4. Trend Inferences for the latest point
        # Calculate dynamic thresholds using the 20th percentile of absolute values
        # to filter out "noise" based on recent price action volatility
        v_threshold = float(np.percentile(np.abs(velocity), 20))
        a_threshold = float(np.percentile(np.abs(acceleration), 20))

        v_latest = velocity[-1]
        a_latest = acceleration[-1]
        
        # Tip-Correction: Polynomial fits at the edges can 'overshoot' (the wiggles).
        # We supplement the SG velocity with a 3-day linear regression of the raw data
        # to ensure the reported direction stays true to the actual chart tip.
        if len(y) >= 3:
            v_tip = np.polyfit(range(3), y[-3:], 1)[0]
            # Use the tip velocity for direction if it's more conservative or confirms the turn
            v_latest = v_tip

        direction = self._determine_direction(v_latest, v_threshold=v_threshold)
        bend_type = self._determine_bend_type(v_latest, a_latest, a_threshold=a_threshold)
        trend_strength = self._calculate_strength(v_latest, velocity)

        return TrendAnalysis(
            direction=direction,
            bend_type=bend_type,
            trend_strength=trend_strength,
            thresholds=thresholds,
            extrema=extrema,
        )

    def _calculate_thresholds(self, y: np.ndarray) -> EmpiricalThresholds:
        """Calculate dynamic thresholds using percentiles."""
        # Using 90th percentile for Peak, 10th for Trough, and 45-55 for mid-zone.
        # These provide a sensible data-driven bound instead of rigid values.
        peak_threshold = np.percentile(y, 90)
        trough_threshold = np.percentile(y, 10)
        mid_zone_upper = np.percentile(y, 60)
        mid_zone_lower = np.percentile(y, 40)

        return EmpiricalThresholds(
            peak_threshold=float(peak_threshold),
            trough_threshold=float(trough_threshold),
            mid_zone_upper=float(mid_zone_upper),
            mid_zone_lower=float(mid_zone_lower),
        )

    def _analyze_extrema(self, y_smooth: np.ndarray) -> ExtremaAnalysis:
        """Find peaks and troughs and compute their distances to the latest point."""
        n = len(y_smooth)
        
        # Local Maxima (Peaks)
        peak_indices = argrelextrema(y_smooth, np.greater)[0]
        peaks = []
        if len(peak_indices) > 0:
            prominences = peak_prominences(y_smooth, peak_indices)[0]
            for idx, prom in zip(peak_indices, prominences):
                peaks.append(
                    ExtremaPoint(
                        index=int(idx),
                        value=float(y_smooth[idx]),
                        extrema_type=ExtremaType.PEAK,
                        prominence=float(prom),
                        distance_to_latest=n - 1 - int(idx),
                    )
                )

        # Local Minima (Troughs)
        trough_indices = argrelextrema(y_smooth, np.less)[0]
        troughs = []
        if len(trough_indices) > 0:
            # For computing trough prominence, we invert the signal
            y_inverted = -y_smooth
            prominences = peak_prominences(y_inverted, trough_indices)[0]
            for idx, prom in zip(trough_indices, prominences):
                troughs.append(
                    ExtremaPoint(
                        index=int(idx),
                        value=float(y_smooth[idx]),
                        extrema_type=ExtremaType.TROUGH,
                        prominence=float(prom),
                        distance_to_latest=n - 1 - int(idx),
                    )
                )

        # Find Nearest & Most Significant
        nearest_peak = min(peaks, key=lambda p: p.distance_to_latest) if peaks else None
        most_sig_peak = max(peaks, key=lambda p: p.prominence) if peaks else None
        
        nearest_trough = min(troughs, key=lambda t: t.distance_to_latest) if troughs else None
        most_sig_trough = max(troughs, key=lambda t: t.prominence) if troughs else None

        # Are we currently at/near a peak or trough? (within last 3 bars)
        is_near_peak = nearest_peak is not None and nearest_peak.distance_to_latest <= 3
        is_near_trough = nearest_trough is not None and nearest_trough.distance_to_latest <= 3

        return ExtremaAnalysis(
            nearest_peak=nearest_peak,
            nearest_trough=nearest_trough,
            most_significant_peak=most_sig_peak,
            most_significant_trough=most_sig_trough,
            is_near_peak=is_near_peak,
            is_near_trough=is_near_trough,
        )

    def _determine_direction(self, v: float, v_threshold: float = 1e-4) -> TrendDirection:
        if v > v_threshold:
            return TrendDirection.RISING
        elif v < -v_threshold:
            return TrendDirection.FALLING
        else:
            return TrendDirection.SIDEWAYS

    def _determine_bend_type(
        self, v: float, a: float, a_threshold: float = 1e-5
    ) -> BendType:
        if abs(a) <= a_threshold:
            return BendType.STRAIGHT
        
        # If velocity and acceleration have same sign, it's accelerating in that direction
        if v > 0 and a < 0:
            return BendType.FLATTENING
        elif v < 0 and a > 0:
            return BendType.FLATTENING
        elif a > 0:
            return BendType.BENDING_UP
        else: # a < 0
            return BendType.BENDING_DOWN

    def _calculate_strength(self, v_latest: float, velocity_array: np.ndarray) -> float:
        """Normalize velocity against recent max velocity to get a 0-1 strength."""
        max_v = np.max(np.abs(velocity_array))
        if max_v == 0:
            return 0.0
        # How strong is the current velocity relative to the highest velocity seen
        return float(min(1.0, abs(v_latest) / max_v))
