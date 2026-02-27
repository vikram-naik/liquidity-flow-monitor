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
    if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
        return None
    return val


def ledger_to_json(df: pd.DataFrame) -> list[dict]:
    """Convert the full ledger DataFrame to a JSON-serialisable list."""
    records = []
    for _, row in df.iterrows():
        rec = {}
        for col in df.columns:
            val = row[col]
            if isinstance(val, (pd.Timestamp, np.datetime64)):
                rec[col] = str(val)
            elif isinstance(val, (np.integer,)):
                rec[col] = int(val)
            elif isinstance(val, (np.floating, float)):
                rec[col] = _nan_safe(round(float(val), 6))
            else:
                rec[col] = val
        records.append(rec)
    return records


def state_summary_to_json(result) -> dict:
    """Convert the EngineResult.latest into API-friendly JSON."""
    return result.latest
