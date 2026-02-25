"""
Crossover Up Marker — DAVWAP Crossover Up (Screener-only).

Detects when price crosses above the DAVWAP:
  - Previous Close < Previous DAVWAP
  - Current Close  > Current DAVWAP

Not a visual chart marker — used exclusively by the screener.
Screener name: ``SCR: CO-U``.
"""

import pandas as pd
from typing import Optional
from src.analysis.markers import MarkerInterface


class CrossoverUpMarker(MarkerInterface):
    """DAVWAP Crossover Up — screener-only marker."""

    _order = 60

    def name(self) -> str:
        return 'crossover_up'

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        # No columns added to the DataFrame; screening uses raw price/davwap.
        return df

    def screen(self, df: pd.DataFrame, latest: pd.Series,
               prev: Optional[pd.Series] = None) -> bool:
        if prev is None:
            return False
        prev_below = prev.get('price_close', 0) < prev.get('davwap', float('inf'))
        curr_above = latest.get('price_close', 0) > latest.get('davwap', float('inf'))
        return bool(prev_below and curr_above)

    def metadata(self) -> dict:
        return {
            'id': 'crossover_up',
            'label': 'Crossover Up',
            'is_chart_marker': False,
            'screener_name': 'SCR: CO-U',
        }

    def agg_rules(self) -> dict:
        return {}
