"""
Divergence Engine — Orchestrator.

Wires analysis modules in sequence and produces the final result object
containing the DVL ledger, integrated state classifications, and chart data.

Usage::

    from src.divergence_engine.engine import DivergenceEngine

    engine = DivergenceEngine(ticker='RELIANCE')
    results = engine.run()

    results.ledger     # full DVL ledger DataFrame
    results.states     # state + probability per bar
    results.export()   # saves ledger CSV
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import logging

import pandas as pd

from src.divergence_engine.base_calc import BaseCalculator
from src.divergence_engine.cwc import CrossWindowCoherence
from src.divergence_engine.cwvap import CompositeVWAP
from src.divergence_engine.dvl_ledger import DVLLedger
from src.divergence_engine.mcs import MoneyCompositeScore
from src.divergence_engine.analysis import compute_trend_participation
from src.divergence_engine.analysis_integrated import apply_integrated_matrix
from src.divergence_engine.utils import load_symbol_data, validate_dataframe, WINDOWS
from src.cache import get_cache

logger = logging.getLogger(__name__)

# Cache TTL: 6 hours — EOD data is stable within the trading day
_RESULT_CACHE_TTL = 21600


@dataclass
class EngineResult:
    """Container for divergence engine output.

    Attributes
    ----------
    ticker : str
        NSE symbol analysed.
    ledger : pd.DataFrame
        Full DVL ledger with all computed columns.
    states : pd.DataFrame
        Subset with date, integrated_state, and coherence.
    """

    ticker: str
    ledger: pd.DataFrame
    states: pd.DataFrame
    _start_date: str = ""
    _end_date: str = ""

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    @property
    def latest(self) -> dict:
        """Return the most recent bar's full state summary."""
        row = self.ledger.iloc[-1]
        return {
            "date": str(row["date"]),
            "mcs_composite": round(float(row.get("mcs_composite", 0)), 4),
            "cwc": round(float(row.get("cwc", 0)), 4),
            "cwvap": round(float(row.get("cwvap", 0)), 2),
            "cpoc": round(float(row.get("cpoc", 0)), 2),
            "poc_spread": round(float(row.get("poc_spread", 0)), 2),
            "gradient_shape": row.get("gradient_shape", ""),
            "coherence_raw": round(float(row.get("coherence_raw", 0)), 4),
            "coherence": round(float(row.get("coherence", 0)), 4),
            "price_slope_z": round(float(row.get("price_slope_z", 0)), 4),
            "price_slope_angle": round(float(row.get("price_slope_angle", 0)), 4),
            "rdv_slope_z": round(float(row.get("rdv_slope_z", 0)), 4),
            "rdv_slope_angle": round(float(row.get("rdv_slope_angle", 0)), 4),
            "value_zone": row.get("value_zone", "N/A"),
            "coherence_stamp": row.get("coherence_stamp", ""),
            "integrated_state": row.get("integrated_state", "N/A"),
        }

    def export(self, path: str | None = None) -> str:
        """Export the ledger to CSV. Returns the file path."""
        if path is None:
            start = self._start_date or str(self.ledger["date"].iloc[0].date())
            end = self._end_date or str(self.ledger["date"].iloc[-1].date())
            path = f"{self.ticker}_{start}_{end}_dvl_ledger.csv"

        self.ledger.to_csv(path, index=False)
        return path


class DivergenceEngine:
    """Orchestrator — runs all analysis modules in sequence.

    Parameters
    ----------
    ticker : str
        NSE symbol (e.g. ``'RELIANCE'``).
    df : pd.DataFrame, optional
        Pre-loaded OHLC + delivery DataFrame.  If ``None``, data is
        loaded from the database automatically.
    start_date : str, optional
        ISO date string for data loading (only used when *df* is None).
    end_date : str, optional
        ISO date string for data loading (only used when *df* is None).
    """

    def __init__(
        self,
        ticker: str,
        df: pd.DataFrame | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> None:
        self.ticker = ticker
        self._df = df
        self._start_date = start_date
        self._end_date = end_date

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> EngineResult:
        """Execute the full analysis pipeline and return results.

        Checks Redis cache first. On HIT, skips all computation.
        On MISS, runs the full pipeline and caches the result.

        Pipeline order (strict):
        1. Base Calculations (ATR, RDV, MFM, TP, MFM_TP)
        2. DVL Ledger (DVL, Velocity, ARS, PDD, Gradient)
        3. Composite VWAP (DVWAP, POC, CWVAP, CPOC, Value Area)
        4. Cross-Window Coherence (CWC)
        5. Money Composite Score (MCS)
        6. Trend Participation Analysis
        7. Integrated State Matrix (rules-based)
        """
        # --- Cache lookup (only for DB-loaded data) ---
        use_cache = self._df is None
        cache_key = None
        if use_cache:
            cache = get_cache()
            cache_key = f"de:result:{self.ticker}:{self._start_date or 'all'}:{self._end_date or 'all'}"
            cached_df = cache.get(cache_key)
            if cached_df is not None:
                logger.info("Engine cache HIT for %s", cache_key)
                return self._build_result(cached_df)

        # --- Full pipeline ---
        if self._df is not None:
            df = self._df.copy()
            validate_dataframe(df, symbol=self.ticker)
        else:
            df = load_symbol_data(self.ticker, self._start_date, self._end_date)

        # Reset index for clean row-based access
        df = df.reset_index(drop=True)

        # Module 1 — Base Calculations
        base = BaseCalculator()
        df = base.compute_all(df)

        # Module 2 — DVL Ledger
        dvl = DVLLedger()
        df = dvl.compute_all(df)

        # Module 3 — Composite VWAP
        cwvap = CompositeVWAP()
        df = cwvap.compute_all(df)

        # Module 4 — Cross-Window Coherence
        cwc = CrossWindowCoherence()
        df = cwc.compute_all(df)

        # Module 5 — Money Composite Score
        mcs = MoneyCompositeScore()
        df = mcs.compute_all(df)

        # Module 6 — Trend Participation Analysis
        df = compute_trend_participation(df)

        # Module 7 — Integrated State Matrix (rules-based)
        df = apply_integrated_matrix(df)

        # --- Cache result ---
        if use_cache and cache_key:
            cache.set(cache_key, df, ttl=_RESULT_CACHE_TTL)
            logger.info("Engine cache SET for %s", cache_key)

        return self._build_result(df)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_result(self, df: pd.DataFrame) -> EngineResult:
        """Build an EngineResult from a computed ledger DataFrame."""
        state_cols = ["date", "integrated_state", "coherence"]
        states_df = df[[c for c in state_cols if c in df.columns]].copy()

        return EngineResult(
            ticker=self.ticker,
            ledger=df,
            states=states_df,
            _start_date=self._start_date or "",
            _end_date=self._end_date or "",
        )

