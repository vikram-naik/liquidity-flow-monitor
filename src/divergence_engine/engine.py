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
from src.divergence_engine.cei import compute_cei
from src.divergence_engine.regime import classify_market_regime
from src.divergence_engine import config_manager as _config_mgr
from src.divergence_engine.utils import load_symbol_data, validate_dataframe, WINDOWS
from src.divergence_engine.aggregator import resample_ohlc_delivery, VALID_MODES
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
        import math

        def _safe(val, default=0, decimals=4):
            """Round a numeric value; return None if NaN/Inf."""
            v = float(val) if val is not None else default
            if math.isnan(v) or math.isinf(v):
                return None
            return round(v, decimals)

        row = self.ledger.iloc[-1]
        return {
            "date": str(row["date"]),
            "integrated_state": row.get("integrated_state", "No Signal"),
            "signal_strength": _safe(row.get("signal_strength"), decimals=1),
            "cwc": _safe(row.get("cwc", 0), decimals=4),
            "rdv": _safe(row.get("rdv", 0), decimals=4),
            "rdv_consistency": int(row.get("rdv_consistency", 0)),
            "cwvap_dist": _safe(row.get("cwvap_dist", 0), decimals=4),
            "delivery_pct": _safe(row.get("delivery_pct", 0), decimals=2),
            "cwvap": _safe(row.get("cwvap", 0), decimals=2),
            "cpoc": _safe(row.get("cpoc", 0), decimals=2),
            "poc_spread": _safe(row.get("poc_spread", 0), decimals=2),
            "coherence_raw": _safe(row.get("coherence_raw", 0), decimals=4),
            "coherence": _safe(row.get("coherence", 0), decimals=4),
            "price_slope_z": _safe(row.get("price_slope_z", 0), decimals=4),
            "rdv_slope_z": _safe(row.get("rdv_slope_z", 0), decimals=4),
            "regime": row.get("regime", "notrend"),
            "scoring_details": row.get("scoring_details", []),
            "scoring_direction": row.get("scoring_direction", "None"),
            "demand_strength": _safe(row.get("demand_strength"), decimals=1),
            "supply_strength": _safe(row.get("supply_strength"), decimals=1),
            "demand_details": row.get("demand_details", []),
            "supply_details": row.get("supply_details", []),
            # CEI module
            "close": _safe(row.get("close"), decimals=2),
            "cei": _safe(row.get("cei"), decimals=5),
            "cei_slope": _safe(row.get("cei_slope"), decimals=6),
            "cei_signal": row.get("cei_signal"),
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
        agg_mode: str = "daily",
    ) -> None:
        if agg_mode not in VALID_MODES:
            raise ValueError(f"Invalid agg_mode '{agg_mode}'. Must be one of {sorted(VALID_MODES)}.")
        self.ticker = ticker
        self._df = df
        self._start_date = start_date
        self._end_date = end_date
        self._agg_mode = agg_mode

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
            cache_key = f"de:result:{self.ticker}:{self._start_date or 'all'}:{self._end_date or 'all'}:{self._agg_mode}"
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

        # --- Aggregation (weekly / monthly) ---
        df = resample_ohlc_delivery(df, self._agg_mode)

        # Reset index for clean row-based access
        df = df.reset_index(drop=True)

        # --- Read delta window settings from scoring config (incl. user overrides) ---
        import sqlite3
        from src.database import DB_PATH
        _conn = sqlite3.connect(DB_PATH, timeout=10)
        _conn.row_factory = sqlite3.Row
        try:
            scoring_cfg = _config_mgr.get_config(_conn)
        finally:
            _conn.close()
        delta_windows = scoring_cfg.get("settings", {}).get("delta_windows", {})
        psz_delta_w = delta_windows.get("psz_delta", [2, 4, 9])
        rsz_delta_w = delta_windows.get("rsz_delta", [2, 4, 9])
        mcs_delta_w = delta_windows.get("mcs_delta", [2, 4, 9])

        # Module 1 — Base Calculations
        base = BaseCalculator()
        df = base.compute_all(df)

        # Module 1.5 — Market Regime Classification (ADX/DMI)
        df["regime"] = classify_market_regime(df)

        # Module 2 — DVL Ledger
        dvl = DVLLedger()
        df = dvl.compute_all(df)

        # Module 3 — Composite VWAP
        va_pct = scoring_cfg.get("settings", {}).get("va_pct", 0.70)
        cwvap = CompositeVWAP(va_pct=va_pct)
        df = cwvap.compute_all(df)

        # Module 4 — Cross-Window Coherence
        cwc = CrossWindowCoherence()
        df = cwc.compute_all(df)

        # Module 5 — Money Composite Score
        mcs = MoneyCompositeScore(delta_windows=mcs_delta_w)
        df = mcs.compute_all(df)

        # Module 6 — Trend Participation Analysis
        df = compute_trend_participation(
            df,
            psz_delta_windows=psz_delta_w,
            rsz_delta_windows=rsz_delta_w,
        )

        # Module 7 — Integrated State Matrix (rules-based)
        df = apply_integrated_matrix(df)

        # Module 7.5 — Cumulative Evidence Index (CEI)
        df = compute_cei(df, scoring_cfg)

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

