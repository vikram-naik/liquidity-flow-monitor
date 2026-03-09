"""
Module 7 — Unified Weighted Scoring (v2).

Delegates to the scoring package for continuous factor scoring.
Replaces the previous binary-gate conviction system.
"""

from __future__ import annotations

import pandas as pd

from src.divergence_engine import config_manager
from src.divergence_engine.scoring import compute_signal_strength


def apply_integrated_matrix(df: pd.DataFrame, db_conn=None) -> pd.DataFrame:
    """Apply unified weighted scoring to the DVL ledger.

    Parameters
    ----------
    df : pd.DataFrame
        The DVL ledger with all prior calculations computed.
    db_conn : sqlite3.Connection, optional
        If provided, loads user-tuned overrides from the database.
    """
    if db_conn is None:
        import sqlite3
        from src.database import DB_PATH
        _conn = sqlite3.connect(DB_PATH, timeout=10)
        _conn.row_factory = sqlite3.Row
        try:
            config = config_manager.get_config(_conn)
        finally:
            _conn.close()
    else:
        config = config_manager.get_config(db_conn)

    return compute_signal_strength(df, config)
