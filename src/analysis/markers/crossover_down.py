"""
Crossover Down Marker — DAVWAP Crossover Down (Screener-only).

Detects when price crosses below the DAVWAP:
  - Previous Close > Previous DAVWAP
  - Current Close  < Current DAVWAP

Not a visual chart marker — used exclusively by the screener.
Screener name: ``SCR: CO-D``.
"""

import pandas as pd
from typing import Optional
from src.analysis.markers import MarkerInterface


class CrossoverDownMarker(MarkerInterface):
    """DAVWAP Crossover Down — screener-only marker."""

    _order = 70

    def name(self) -> str:
        return 'crossover_down'

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        # No columns added to the DataFrame; screening uses raw price/davwap.
        return df

    def screen(self, df: pd.DataFrame, latest: pd.Series,
               prev: Optional[pd.Series] = None) -> bool:
        if prev is None:
            return False
        prev_above = prev.get('price_close', 0) > prev.get('davwap', float('-inf'))
        curr_below = latest.get('price_close', 0) < latest.get('davwap', float('-inf'))
        return bool(prev_above and curr_below)

    def metadata(self) -> dict:
        return {
            'id': 'crossover_down',
            'label': 'Crossover Down',
            'is_chart_marker': False,
            'screener_name': 'SCR: CO-D',
        }

    def agg_rules(self) -> dict:
        return {}
