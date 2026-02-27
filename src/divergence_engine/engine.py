"""
Divergence Engine — Orchestrator.

Wires all 8 modules in sequence and produces the final result object
containing the DVL ledger, state classifications, and chart renderer.

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

import pandas as pd

from src.divergence_engine.base_calc import BaseCalculator
from src.divergence_engine.classifier import StateClassifier
from src.divergence_engine.cwc import CrossWindowCoherence
from src.divergence_engine.cwvap import CompositeVWAP
from src.divergence_engine.divergence import DivergenceDetector
from src.divergence_engine.dvl_ledger import DVLLedger
from src.divergence_engine.mcs import MoneyCompositeScore
from src.divergence_engine.utils import load_symbol_data, validate_dataframe, WINDOWS


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
        Subset with date, state, confidence, and per-state probabilities.
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
            "state": row["state"],
            "confidence": round(float(row["state_confidence"]), 4),
            "mcs_composite": round(float(row.get("mcs_composite", 0)), 4),
            "cwc": round(float(row.get("cwc", 0)), 4),
            "cwvap": round(float(row.get("cwvap", 0)), 2),
            "cpoc": round(float(row.get("cpoc", 0)), 2),
            "poc_spread": round(float(row.get("poc_spread", 0)), 2),
            "divergence": f"{row.get('divergence_direction', 'none')} "
                          f"{round(float(row.get('divergence_probability', 0)), 2)}",
            "gradient_shape": row.get("gradient_shape", ""),
            "probabilities": {
                state: round(float(row.get(f"prob_{state}", 0)), 4)
                for state in [
                    "UPTREND", "DOWNTREND", "ACCUMULATION",
                    "DISTRIBUTION", "SIDEWAYS", "RECOVERY",
                ]
            },
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

        Pipeline order (strict):
        1. Base Calculations (ATR, RDV, MFM, TP, MFM_TP)
        2. DVL Ledger (DVL, Velocity, ARS, PDD, Gradient)
        3. Composite VWAP (DVWAP, POC, CWVAP, CPOC, Value Area)
        4. Cross-Window Coherence (CWC)
        5. Money Composite Score (MCS)
        6. Divergence Detection
        7. State Classification
        """
        # Load data
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

        # Module 6 — Divergence Detection
        div = DivergenceDetector()
        df = div.compute_all(df)

        # Module 7 — State Classification
        cls = StateClassifier()
        df = cls.compute_all(df)

        # Build state summary DataFrame
        state_cols = ["date", "state", "state_confidence"] + [
            f"prob_{s}" for s in [
                "UPTREND", "DOWNTREND", "ACCUMULATION",
                "DISTRIBUTION", "SIDEWAYS", "RECOVERY",
            ]
        ]
        states_df = df[state_cols].copy()

        return EngineResult(
            ticker=self.ticker,
            ledger=df,
            states=states_df,
            _start_date=self._start_date or "",
            _end_date=self._end_date or "",
        )
