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

# Columns the UI actually reads — trim everything else before serialising
UI_COLUMNS = [
    # Chart panels (OHLC, overlays, slopes, coherence)
    "date", "open", "high", "low", "close",
    "cwvap", "cpoc", "delivery_qty", "mfm",
    "price_slope_z", "rdv_slope_z",
    "coherence_raw", "coherence",
    "integrated_state",
    "regime",
    # Unified scoring data
    "signal_strength", "scoring_direction",
    "cwvap_dist", "rdv", "cwc", "cdvl", "gradient_shape",
    "psz_delta_2d", "psz_delta_4d", "psz_delta_9d",
    "rsz_delta_2d", "rsz_delta_4d", "rsz_delta_9d",
    "mcs_delta_2d", "mcs_delta_4d", "mcs_delta_9d", "pdd_30",
    "scoring_details",
    "demand_strength", "supply_strength",
    "demand_details", "supply_details",
    # CEI (Module 7.5)
    "cei_raw", "cei", "cei_slope", "cei_signal",
    "accum_div", "distrib_div",
]


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

