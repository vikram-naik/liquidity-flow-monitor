"""
Module 8 — Visualisation data helpers.

Provides data serialisation utilities for the divergence engine API:
- ``ledger_to_json()``: converts the full ledger to JSON-safe records
- ``state_summary_to_json()``: extracts the latest state summary

The actual chart rendering is handled by static web assets in
``src/web/`` (HTML + CSS + JS using lightweight-charts v5).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from src.trading.signals import SavgolCTSEntryConfig, SavgolCTSExitConfig, SignalFactory
from src.trading.signals.base import Trade

# Columns the UI actually reads — trim everything else before serialising
UI_COLUMNS = [
    # Chart panels (OHLC, overlays, slopes, coherence)
    "date", "open", "high", "low", "close",
    "cwvap", "delivery_qty", "mfm",
    "price_slope_z", "rdv_slope_z",
    "coherence_raw", "coherence",
    "regime",
    # Delivery metrics
    "cwvap_dist", "rdv", "cwc", "cdvl", "gradient_shape", "pdd_30", "pdd_120", "mcs_composite",
    "cts", "cts_slope", "cts_accel",
    "velocity_60_norm", "vel_dp5",
    # NextGen gate thresholds (rolling percentiles from trend_participation)
    "cts_buy_threshold", "cts_sell_threshold", "cts_accel_threshold", "pdd_120_threshold",
    # Divergence
    "accum_div", "distrib_div",
    # Delivery-Profile Value Area (Module 3)
    "va_high", "va_low",
    # Signal markers (computed by check_entry/check_exit from src.trading.signals)
    "entry_signal", "entry_reason",
    "exit_signal", "exit_reason",
]

_ENTRY_CFG = SavgolCTSEntryConfig()
_EXIT_CFG = SavgolCTSExitConfig()
_SIGNAL = SignalFactory.get_signal("savgol_cts")

def _nan_safe(val: Any) -> Any:
    """Convert NaN / Inf to None for JSON serialisation."""
    if isinstance(val, (float, np.floating)) and (np.isnan(val) or np.isinf(val)):
        return None
    return val


def _clean(obj: Any) -> Any:
    """Recursively clean objects for JSON compliance (NaN/Inf -> None)."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(x) for x in obj]
    if isinstance(obj, (pd.Timestamp, np.datetime64)):
        if hasattr(obj, "strftime"):
             return obj.strftime("%Y-%m-%d")
        return str(obj)[:10]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return _nan_safe(round(float(obj), 6))
    if pd.isna(obj):  # Catch-all for pd.NA, np.nan, etc.
        return None
    return obj


def _tag_entry_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Add an ``entry_signal`` column using the canonical check_entry logic.
    Stores the intensity (soft filter count) if the signal qualifies, else 0.
    """
    records = df.to_dict("records")
    flags = [0] * len(records)
    reasons = [None] * len(records)
    for i in range(1, len(records)):
        ok, soft, det = _SIGNAL.check_entry(
            records[i], records[i - 1], _ENTRY_CFG, records=records, idx=i,
        )
        if ok:
            flags[i] = soft
        reasons[i] = det.get("reason")
    df = df.copy()
    df["entry_signal"] = flags
    df["entry_reason"] = reasons
    return df


def _tag_exit_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``exit_signal`` and ``exit_reason`` columns by simulating trades from entry markers."""
    records = df.to_dict("records")
    n = len(records)
    exit_flags = [0] * n
    exit_reasons = [None] * n

    cwvap_values = [records[0].get("cwvap", np.nan)]
    in_trade = False
    trade: Trade | None = None
    peak_close = 0.0
    delivery_bad_count = 0

    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        cwvap_values.append(row.get("cwvap", np.nan))

        if np.isnan(close) if isinstance(close, float) else False:
            continue

        if in_trade and trade is not None:
            if close > peak_close:
                peak_close = close
            bars_held = i - trade.entry_idx

            reason, delivery_bad_count = _SIGNAL.check_exit(
                row, prev, trade, peak_close, bars_held, delivery_bad_count, cwvap_values, _EXIT_CFG,
            )
            if reason:
                exit_flags[i] = 1
                exit_reasons[i] = reason
                in_trade = False
                trade = None
                delivery_bad_count = 0
        else:
            # Start a new trade if an entry signal fired on this bar
            if records[i].get("entry_signal", 0):
                atr = row.get("atr_20", 0) or row.get("atr", 0)
                if not atr or (isinstance(atr, float) and np.isnan(atr)):
                    atr = close * 0.02  # fallback 2%
                trade = Trade(
                    symbol="",
                    entry_date=str(row.get("date", ""))[:10],
                    entry_price=close,
                    entry_idx=i,
                    atr_at_entry=atr,
                    soft_filters_passed=records[i].get("entry_signal", 0),
                    regime_at_entry=str(row.get("regime", "")),
                    psz_at_entry=row.get("price_slope_z", 0.0) or 0.0,
                )
                peak_close = close
                delivery_bad_count = 0
                in_trade = True

    df = df.copy()
    df["exit_signal"] = exit_flags
    df["exit_reason"] = exit_reasons
    return df


def ledger_to_json(df: pd.DataFrame) -> list[dict]:
    """Convert the ledger DataFrame to a JSON-serialisable list.

    Trims to UI_COLUMNS and uses vectorised conversion for performance.
    """
    # Tag entry signals using the canonical signal logic
    df = _tag_entry_signals(df)
    df = _tag_exit_signals(df)

    # Trim to only the columns the UI needs
    cols = [c for c in UI_COLUMNS if c in df.columns]
    slim = df[cols].copy()

    # Pre-convert datetime columns to strings
    for col in slim.select_dtypes(include=["datetime64"]).columns:
        slim[col] = slim[col].dt.strftime("%Y-%m-%d")

    # Round float columns for cleaner output
    float_cols = slim.select_dtypes(include=["float64", "float32"]).columns
    slim[float_cols] = slim[float_cols].round(6)

    # Vectorised dict conversion
    records = slim.to_dict(orient="records")

    # Single-pass NaN → None cleanup
    return [
        {k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()}
        for r in records
    ]


def state_summary_to_json(result) -> dict:
    """Convert the EngineResult.latest into API-friendly JSON."""
    return _clean(result.latest)
