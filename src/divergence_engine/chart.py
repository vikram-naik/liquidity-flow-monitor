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

from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSSignal

# Columns the UI actually reads — trim everything else before serialising
UI_COLUMNS = [
    # Chart panels (OHLC, overlays, slopes, coherence)
    "date", "open", "high", "low", "close",
    "cwvap", "delivery_qty", "mfm",
    "price_slope_z", "rdv_slope_z",
    "coherence_raw", "coherence",
    "regime",
    # Delivery metrics
    "cwvap_dist", "rdv", "cwc", "cdvl", "gradient_shape", "pdd_30", "pdd_120", "mcs_composite", "prt", "prt_slope", "prt_accel",
    "range_pos_10", "range_pos_22", "range_pos_63", "range_pos_252", "is_ath",
    "cts", "cts_slope", "cts_accel",
    "velocity_60_norm", "vel_dp5",
    # PSZ (price slope z) raw + thresholds
    "psz_v", "psz_buy_threshold", "psz_sell_threshold",
    # RSZ (rdv slope z) raw + thresholds
    "rsz_v", "rsz_buy_threshold", "rsz_sell_threshold",
    # NextGen gate thresholds (rolling percentiles from trend_participation)
    "cts_buy_threshold", "cts_sell_threshold", "cts_accel_threshold", "pdd_120_threshold",
    "fas_buy_threshold", "fas_sell_threshold",
    # Features
    "fas", "entry_signal_prob",
    # Divergence
    "accum_div", "distrib_div",
    # Delivery-Profile Value Area (Module 3)
    "va_high", "va_low",
    # Signal markers (computed by check_entry/check_exit from src.trading.signals)
    "entry_signal", "entry_reason",
    "exit_signal", "exit_reason",
    "cooldown",
]

_SIGNAL: SavgolCTSSignal = SignalFactory.get_signal("savgol_cts")

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


def ledger_to_json(df: pd.DataFrame) -> list[dict]:
    """Convert the ledger DataFrame to a JSON-serialisable list.

    Trims to UI_COLUMNS and uses vectorised conversion for performance.
    """
    df = _SIGNAL.tag_signals(df)

    # Entry Signal Probability Feature
    # 1 when psz crosses zero (-ve to +ve) and prt < 0 and cts_slope > 0
    # 0 when psz crosses zero and any of the other conditions fire
    # -1 when none of the conditions fire
    if all(col in df.columns for col in ["price_slope_z", "prt", "cts_slope"]):
        psz_cross_up = (df["price_slope_z"] > 0) & (df["price_slope_z"].shift(1) < 0)
        prt_cond = df["prt"] < 0
        cts_cond = df["cts_slope"] > 0

        cond_all = psz_cross_up & prt_cond & cts_cond
        cond_psz_any = psz_cross_up & (prt_cond | cts_cond)

        df["entry_signal_prob"] = np.select(
            [cond_all, cond_psz_any],
            [1.0, 0.0],
            default=-1.0
        )

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
