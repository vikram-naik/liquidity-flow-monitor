"""
Divergence Engine — Orchestrator.

Wires analysis modules in sequence and produces the final result object
containing the DVL ledger, regime classification, and chart data.

Usage::

    from src.divergence_engine.engine import DivergenceEngine

    engine = DivergenceEngine(ticker='RELIANCE')
    results = engine.run()

    results.ledger     # full DVL ledger DataFrame
    results.states     # state + probability per bar
    results.export()   # saves ledger CSV
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import logging

import pandas as pd

from src.divergence_engine.base_calc import BaseCalculator
from src.divergence_engine.cwc import CrossWindowCoherence
from src.divergence_engine.cwvap import CompositeVWAP
from src.divergence_engine.dvl_ledger import DVLLedger
from src.divergence_engine.mcs import MoneyCompositeScore
from src.divergence_engine.analysis import compute_trend_participation, SavitzkyGolayAnalyzer, TrendAnalysis
from src.divergence_engine.regime import classify_market_regime
from src.divergence_engine.utils import load_symbol_data, validate_dataframe, WINDOWS
from src.divergence_engine.aggregator import resample_ohlc_delivery, VALID_MODES
from src.cache import get_cache

logger = logging.getLogger(__name__)

# Cache TTL: 6 hours — EOD data is stable within the trading day
_RESULT_CACHE_TTL = 21600

# Value Area percentage for delivery-profile boundaries
_VA_PCT = 0.70

# Intermediate columns to drop before returning results.
# These are needed during computation but not in the final ledger.
_DROP_COLS = [
    # Base calc intermediates (feed ATR / MCS only)
    "true_high", "true_low", "tr", "tp", "mfm_tp",
    # Per-window VWAP/POC/VA (feed composites only)
    "dvwap_10", "dvwap_30", "dvwap_60", "dvwap_120",
    "poc_10", "poc_30", "poc_60", "poc_120",
    "va_high_10", "va_high_30", "va_high_60", "va_high_120",
    "va_low_10", "va_low_30", "va_low_60", "va_low_120",
    # CPOC + POC Spread (removed from UI)
    "cpoc", "poc_spread",
    # Old Bollinger-style VA (replaced by delivery-profile VA)
    "cvah", "cval", "va_width",
    # CWVAP intermediates
    "cwvap_slope", "cwvap_slope_norm", "price_location",
    "cwvap_ema_5", "cwvap_ema_8", "cwvap_ema_14", "cwvap_ema_21",
    # CWC pairwise intermediates
    "c_10_30", "c_30_60", "c_60_120", "cwc_delta",
    # MCS sub-components (feed mcs_composite only)
    "mcs", "mcs_mfm",
]


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
        Subset with date and regime.
    """

    ticker: str
    ledger: pd.DataFrame
    states: pd.DataFrame
    cts_trend_analysis: Optional[TrendAnalysis] = None
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
        latest_dict = {
            "date": str(row["date"]),
            "close": _safe(row.get("close"), decimals=2),
            "cwc": _safe(row.get("cwc", 0), decimals=4),
            "rdv": _safe(row.get("rdv", 0), decimals=4),
            "cwvap_dist": _safe(row.get("cwvap_dist", 0), decimals=4),
            "delivery_pct": _safe(row.get("delivery_pct", 0), decimals=2),
            "cwvap": _safe(row.get("cwvap", 0), decimals=2),
            "coherence_raw": _safe(row.get("coherence_raw", 0), decimals=4),
            "coherence": _safe(row.get("coherence", 0), decimals=4),
            "price_slope_z": _safe(row.get("price_slope_z", 0), decimals=4),
            "psz_smooth": _safe(row.get("psz_smooth", 0), decimals=4),
            "psz_v": _safe(row.get("psz_v", 0), decimals=6),
            "rdv_slope_z": _safe(row.get("rdv_slope_z", 0), decimals=4),
            "pdd_120": _safe(row.get("pdd_120", 0), decimals=4),
            "mcs_composite": _safe(row.get("mcs_composite", 0), decimals=4),
            "cts": _safe(row.get("cts", 0), decimals=4),
            "cts_slope": _safe(row.get("cts_slope", 0), decimals=6),
            "cts_accel": _safe(row.get("cts_accel", 0), decimals=6),
            "gradient_shape": row.get("gradient_shape", "—"),
            "regime": row.get("regime", "notrend"),
        }
        
        # Merge trend analysis into latest output
        if self.cts_trend_analysis:
            import dataclasses
            latest_dict["cts_trend_analysis"] = dataclasses.asdict(self.cts_trend_analysis)
        
        return latest_dict

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
        1.5. Market Regime Classification (ADX/DMI)
        2. DVL Ledger (DVL, Velocity, ARS, PDD, Gradient)
        3. Composite VWAP (DVWAP, POC, CWVAP, Value Area)
        4. Cross-Window Coherence (CWC)
        5. Money Composite Score (MCS)
        6. Trend Participation Analysis
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
                
                # Module 7 — CTS Trend Analysis (on cache hit)
                analyzer = SavitzkyGolayAnalyzer()
                trend_analysis = None
                if "cts" in cached_df.columns:
                    trend_analysis = analyzer.analyze(cached_df["cts"])
                    
                return self._build_result(cached_df, trend_analysis)

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

        # Module 1 — Base Calculations
        base = BaseCalculator()
        df = base.compute_all(df)

        # Module 1.5 — Market Regime Classification (ADX/DMI)
        df["regime"] = classify_market_regime(df)

        # Module 2 — DVL Ledger
        dvl = DVLLedger()
        df = dvl.compute_all(df)

        # Module 3 — Composite VWAP (fast CTS, window=15)
        cwvap = CompositeVWAP(va_pct=_VA_PCT, cts_strategy="causal_savgol")
        df = cwvap.compute_all(df)

        # Module 4 — Cross-Window Coherence
        cwc = CrossWindowCoherence()
        df = cwc.compute_all(df)

        # Module 5 — Money Composite Score
        mcs = MoneyCompositeScore()
        df = mcs.compute_all(df)

        # Module 6 — Trend Participation Analysis
        df = compute_trend_participation(df)

        # Module 7 — CTS Trend Analysis
        analyzer = SavitzkyGolayAnalyzer()
        trend_analysis = None
        if "cts" in df.columns:
            # 7a. Comprehensive analysis for the entire ledger (causal)
            trend_df = analyzer.analyze_series_full(df["cts"])
            df["cts_direction"] = trend_df["direction"]
            df["cts_bend"] = trend_df["bend"]
            df["cts_strength"] = trend_df["strength"]
            df["cts_is_steep"] = trend_df["is_steep"]

            # 7b. Detailed summary for the latest bar
            trend_analysis = analyzer.analyze(df["cts"])

        # --- Drop intermediate columns ---
        df = df.drop(columns=[c for c in _DROP_COLS if c in df.columns])

        # --- Cache result ---
        if use_cache and cache_key:
            cache.set(cache_key, df, ttl=_RESULT_CACHE_TTL)
            logger.info("Engine cache SET for %s", cache_key)

        return self._build_result(df, trend_analysis)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_result(self, df: pd.DataFrame, trend_analysis: Optional[TrendAnalysis] = None) -> EngineResult:
        """Build an EngineResult from a computed ledger DataFrame."""
        state_cols = ["date", "regime"]
        states_df = df[[c for c in state_cols if c in df.columns]].copy()

        return EngineResult(
            ticker=self.ticker,
            ledger=df,
            states=states_df,
            cts_trend_analysis=trend_analysis,
            _start_date=self._start_date or "",
            _end_date=self._end_date or "",
        )
