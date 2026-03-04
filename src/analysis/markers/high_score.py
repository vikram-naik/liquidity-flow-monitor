"""
High Score Marker — 70+ Score Detection (Screener-only).

Detects when any scored marker (Ignition or Coil) has a score exceeding 90.
Used to surface the highest-conviction setups in a dedicated screener list.

Not a visual chart marker — used exclusively by the screener.
Screener name: ``SCR: 90UP``.
"""

import pandas as pd
from typing import Optional
from src.analysis.markers import MarkerInterface


class HighScoreMarker(MarkerInterface):
    """90+ Score screener — screener-only marker."""

    _order = 80

    def name(self) -> str:
        return 'high_score'

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        # No-op: depends on score columns from other markers (coil_score,
        # ignition_score) which are computed earlier in the pipeline.
        return df

    def screen(self, df: pd.DataFrame, latest: pd.Series,
               prev: Optional[pd.Series] = None) -> bool:
        return (
            latest.get('ignition_score', 0) >= 70 or
            latest.get('coil_score', 0) >= 70
        )

    def metadata(self) -> dict:
        return {
            'id': 'high_score',
            'label': '90+ Score',
            'is_chart_marker': False,
            'marker_type': 'neutral',
            'screener_name': 'SCR: 90UP',
        }

    def agg_rules(self) -> dict:
        return {}
