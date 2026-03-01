"""
Intensity Marker — Trend Intensity & Velocity Status.

Not a visual chart marker (no icon on candles).  Instead it computes derived
columns used in the dashboard header metrics:

  - ledger_angle  (0°–90°)  — normalised DVL slope vs global baseline
  - ledger_velocity         — Accelerating / Steady / Weakening / Reversing
  - mcs_angle     (0°–90°)  — normalised MCS slope

These columns are consumed by the frontend ``updateMetrics()`` function and
surfaced in the API ``meta`` object.
"""

import numpy as np
import pandas as pd
from src.analysis.markers import MarkerInterface


class IntensityMarker(MarkerInterface):
    """Trend Intensity & Velocity Status (non-visual)."""

    _order = 50  # runs after all chart markers

    def name(self) -> str:
        return 'intensity'

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        # GLS (Global Ledger Slope) — use cumulative slope near anchor,
        # otherwise the 5-day rolling slope.
        # cum_dvl_slope_anchor is passed in via the '_cum_dvl_slope' column
        # that data.py computes before the marker loop.
        cum_dvl_slope = df.get('_cum_dvl_slope')

        if cum_dvl_slope is not None:
            gls = df['dvl_slope_5'].where(
                df['days_since_anchor'] <= 5, cum_dvl_slope
            )
        else:
            gls = df['dvl_slope_5']

        # Ledger ratio: current pace / trend average pace
        l_ratio = (df['dvl_slope_5'] / gls.abs().replace(0, np.nan)).fillna(0)
        df['ledger_angle'] = np.degrees(np.arctan(l_ratio.clip(0))).round(1)

        # Velocity status
        df['ledger_velocity'] = 'Steady'
        df.loc[l_ratio > 1.0, 'ledger_velocity'] = 'Accelerating'
        df.loc[(l_ratio <= 1.0) & (l_ratio >= 0.7), 'ledger_velocity'] = 'Steady'
        df.loc[(l_ratio < 0.7) & (l_ratio >= 0), 'ledger_velocity'] = 'Weakening'
        df.loc[l_ratio < 0, 'ledger_velocity'] = 'Reversing'

        # MCS intensity: normalised vs 0.1 shift/day
        m_intensity = (df['mcs_slope_5'] / 0.1).fillna(0)
        df['mcs_angle'] = np.degrees(np.arctan(m_intensity.clip(0))).round(1)

        return df

    def screen(self, df: pd.DataFrame, latest: pd.Series, prev=None) -> bool:
        # SCR: HH/HL - Captures 'High Conviction' row of the UI Power Matrix
        # (Both HH and HL categories have Ledger Tilt >= 45)
        angle = latest.get('ledger_angle')
        if angle is not None:
            return angle >= 45
        return False

    def metadata(self) -> dict:
        return {
            'id': 'intensity',
            'label': 'Trend Intensity',
            'is_chart_marker': False,
            'marker_type': 'neutral',
            'screener_name': 'SCR: HH/HL',
        }

    def agg_rules(self) -> dict:
        return {
            'ledger_angle': 'last',
            'mcs_angle': 'last',
            'ledger_velocity': 'last',
        }
