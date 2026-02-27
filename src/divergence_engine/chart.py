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
        return str(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return _nan_safe(round(float(obj), 6))
    if pd.isna(obj):  # Catch-all for pd.NA, np.nan, etc.
        return None
    return obj


def ledger_to_json(df: pd.DataFrame) -> list[dict]:
    """Convert the full ledger DataFrame to a JSON-serialisable list."""
    records = []
    for _, row in df.iterrows():
        records.append(_clean(row.to_dict()))
    return records


def state_summary_to_json(result) -> dict:
    """Convert the EngineResult.latest into API-friendly JSON."""
    return _clean(result.latest)
