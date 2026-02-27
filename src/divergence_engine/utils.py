"""
Shared utilities for the Divergence Engine.

Provides data validation, loading helpers, and the sigmoid function used
by the divergence probability scorer.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    pass

from src.divergence_engine.repository import DeliveryRepository

# Minimum number of bars required (120 for longest anchor + 10 buffer)
MIN_BARS = 130

# Rolling anchor windows
WINDOWS = [10, 30, 60, 120]

# Required DataFrame columns after loading
REQUIRED_COLUMNS = {"date", "open", "high", "low", "close", "volume", "delivery_qty"}

# Singleton repository instance
_repo = DeliveryRepository()


def load_symbol_data(
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """
    Load OHLC + delivery data for *symbol* from the database.

    Delegates to :class:`DeliveryRepository` and runs validation before
    returning.

    Raises
    ------
    ValueError
        If the loaded data has fewer than :data:`MIN_BARS` rows.
    """
    df = _repo.fetch_adjusted_data(symbol, start_date, end_date)
    validate_dataframe(df, symbol=symbol)
    return df


def validate_dataframe(df: pd.DataFrame, *, symbol: str = "unknown") -> None:
    """
    Validate that *df* meets the engine's input requirements.

    Checks
    ------
    1. All required columns are present.
    2. Minimum row count (``MIN_BARS``).
    3. Zero-volume rows are forward-filled (volume & delivery_qty).
    4. NaNs in OHLC are forward-filled after the warmup period.

    Raises
    ------
    ValueError
        On missing columns or insufficient data.
    """
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            f"[{symbol}] Missing required columns: {sorted(missing)}"
        )

    if len(df) < MIN_BARS:
        raise ValueError(
            f"[{symbol}] Insufficient data: {len(df)} bars provided, "
            f"minimum {MIN_BARS} required."
        )

    # --- Edge-case handling --------------------------------------------------

    # Zero-volume days: forward-fill volume and delivery_qty so downstream
    # rolling calculations are not distorted.
    for col in ("volume", "delivery_qty"):
        mask = df[col] <= 0
        if mask.any():
            df.loc[mask, col] = np.nan
            df[col] = df[col].ffill()
            # If leading rows are zero, back-fill so we never have NaN at head.
            df[col] = df[col].bfill()

    # Forward-fill any remaining OHLC NaNs (after the initial warmup window).
    ohlc = ["open", "high", "low", "close"]
    df[ohlc] = df[ohlc].ffill()


def sigmoid(x: float | np.ndarray, k: float = 5.0, x0: float = 0.5) -> float | np.ndarray:
    """
    Compute the sigmoid function used for divergence probability scoring.

    .. math::

        \\sigma(x) = \\frac{1}{1 + e^{-k \\cdot (x - x_0)}}

    Parameters
    ----------
    x : float or array-like
        Input value(s).
    k : float
        Steepness parameter (default ``5.0``).
    x0 : float
        Centre/midpoint (default ``0.5``).

    Returns
    -------
    float or np.ndarray
        Output in ``(0, 1)``.
    """
    exponent = -k * (x - x0)
    # Clip to prevent overflow in exp for very large negative exponents
    if isinstance(exponent, np.ndarray):
        exponent = np.clip(exponent, -500, 500)
        return 1.0 / (1.0 + np.exp(exponent))
    else:
        exponent = max(-500.0, min(500.0, exponent))
        return 1.0 / (1.0 + math.exp(exponent))
