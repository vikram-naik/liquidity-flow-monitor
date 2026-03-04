"""
OHLC + Delivery Data Aggregator.

Resamples daily bars into weekly or monthly bars *before* the analysis
pipeline runs.  All downstream modules (base_calc, dvl_ledger, cwvap,
cwc, mcs, analysis, rules) operate identically on the resampled frame.

Supported modes
---------------
- ``"daily"``   — no-op, returns the input unchanged.
- ``"weekly"``  — ISO week ending Friday (``W-FRI``).
- ``"monthly"`` — calendar month-end (``ME``).
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# Valid mode strings
VALID_MODES = {"daily", "weekly", "monthly"}

# pandas offset aliases
_FREQ_MAP = {
    "weekly": "W-FRI",
    "monthly": "ME",
}


def resample_ohlc_delivery(
    df: pd.DataFrame,
    mode: str = "daily",
) -> pd.DataFrame:
    """Aggregate daily OHLC + delivery data to a coarser time frame.

    Parameters
    ----------
    df : pd.DataFrame
        Daily data with columns: ``date, open, high, low, close,
        volume, delivery_qty, delivery_pct``.
    mode : str
        One of ``"daily"``, ``"weekly"``, ``"monthly"``.

    Returns
    -------
    pd.DataFrame
        Resampled frame with a clean integer index and ``date`` column
        set to the last trading day of each period.
    """
    if mode not in VALID_MODES:
        raise ValueError(
            f"Invalid agg_mode '{mode}'. Must be one of {sorted(VALID_MODES)}."
        )

    if mode == "daily":
        return df

    freq = _FREQ_MAP[mode]

    # Ensure date is datetime and set as index for resampling
    work = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(work["date"]):
        work["date"] = pd.to_datetime(work["date"])
    work = work.set_index("date").sort_index()

    # OHLC resampling rules
    agg_rules = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "delivery_qty": "sum",
    }

    resampled = work.resample(freq).agg(agg_rules)

    # Drop periods with no trading data (market holidays / weekends)
    resampled = resampled.dropna(subset=["open"])

    # Recompute delivery_pct as weighted mean: total delivery / total volume
    resampled["delivery_pct"] = (
        resampled["delivery_qty"] / resampled["volume"].replace(0, 1) * 100
    ).round(2)

    # Reset index: the resampled date index becomes a column
    resampled = resampled.reset_index()

    # Use the actual last trading day in each period instead of the
    # artificial period-end date (which may be a non-trading day).
    last_dates = work.index.to_series().resample(freq).last().dropna()
    # Align by the resampled index
    resampled["date"] = last_dates.values[: len(resampled)]

    logger.info(
        "Resampled %d daily bars → %d %s bars",
        len(df),
        len(resampled),
        mode,
    )

    return resampled
