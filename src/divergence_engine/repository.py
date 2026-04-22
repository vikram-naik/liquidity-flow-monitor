"""
Repository pattern for Divergence Engine data access.

Centralises all SQL queries for the divergence engine, keeping analytics
modules free of raw SQL.  Uses ``src.database.get_db_connection()`` for
connection management and ``src.cache.get_cache()`` for Redis caching.

Corporate-action adjustment
----------------------------
Raw OHLC data from ``nse_delivery_log`` is adjusted for splits, bonuses,
and rights issues using the ``corporate_actions`` table.  For each action
(processed in descending ex_date order), all rows **before** the ex_date
get:
- OHLC prices multiplied by ``ratio_factor``
- Volume divided by ``ratio_factor``

This produces backward-adjusted prices consistent with the latest trading
price level.
"""

import logging
from typing import Optional

import pandas as pd

from src.database import get_db_connection
from src.cache import get_cache

logger = logging.getLogger(__name__)


class DeliveryRepository:
    """Read-only repository for NSE delivery data from ``nse_delivery_log``."""

    # Column mapping: DB column → canonical engine column
    _COLUMN_MAP = {
        "record_date": "date",
        "price_open": "open",
        "price_high": "high",
        "price_low": "low",
        "price_close": "close",
        "volume_total": "volume",
        "delivery_qty": "delivery_qty",
        "delivery_pct": "delivery_pct",
    }

    _SELECT_COLS = ", ".join(_COLUMN_MAP.keys())

    # Cache TTL: 6 hours — EOD data is stable within the trading day
    _CACHE_TTL = 21600

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_ohlc_delivery(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """Load raw OHLC + delivery data for *symbol* from the database."""
        query = f"SELECT {self._SELECT_COLS} FROM nse_delivery_log WHERE symbol = ?"
        params: list = [symbol]

        if start_date:
            query += " AND record_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND record_date <= ?"
            params.append(end_date)

        query += " ORDER BY record_date ASC"

        conn = get_db_connection()
        try:
            df = pd.read_sql_query(query, conn, params=params)
        finally:
            conn.close()

        df.rename(columns=self._COLUMN_MAP, inplace=True)
        df["date"] = pd.to_datetime(df["date"])
        return df

    def fetch_adjusted_data(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """Load OHLC + delivery data adjusted for corporate actions, with caching.

        Checks Redis cache first. On miss, fetches raw data from DB,
        applies corporate-action adjustments, caches the result, and
        returns the adjusted DataFrame.
        """
        # --- Cache lookup ---
        cache = get_cache()
        
        # Get latest record date to ensure cache freshness if data was recently synced
        conn = get_db_connection()
        try:
            last_row = conn.execute(
                "SELECT MAX(record_date) FROM nse_delivery_log WHERE symbol = ?", 
                (symbol,)
            ).fetchone()
            last_date = last_row[0] if last_row and last_row[0] else "none"
        finally:
            conn.close()

        cache_key = f"de:adjusted:{symbol}:{start_date or 'all'}:{end_date or 'all'}:{last_date}"

        cached = cache.get(cache_key)
        if cached is not None:
            logger.info("Cache HIT for %s", cache_key)
            return cached

        logger.info("Cache MISS for %s — fetching from DB", cache_key)

        # --- Fetch raw data ---
        df = self.fetch_ohlc_delivery(symbol, start_date, end_date)
        if df.empty:
            return df

        # --- Apply corporate actions ---
        actions = self._fetch_corporate_actions(symbol)
        if not actions.empty:
            df = self._adjust_for_corporate_actions(df, actions)

        # --- Cache result ---
        cache.set(cache_key, df, ttl=self._CACHE_TTL)
        return df

    # ------------------------------------------------------------------
    # Corporate action helpers
    # ------------------------------------------------------------------

    def _fetch_corporate_actions(self, symbol: str) -> pd.DataFrame:
        """Fetch all corporate actions for *symbol*, ordered by ex_date DESC."""
        query = (
            "SELECT ex_date, ca_type, ratio_factor "
            "FROM corporate_actions "
            "WHERE symbol = ? "
            "ORDER BY ex_date DESC"
        )
        conn = get_db_connection()
        try:
            df = pd.read_sql_query(query, conn, params=[symbol])
        finally:
            conn.close()

        if not df.empty:
            df["ex_date"] = pd.to_datetime(df["ex_date"])
        return df

    @staticmethod
    def _adjust_for_corporate_actions(
        df: pd.DataFrame,
        actions: pd.DataFrame,
    ) -> pd.DataFrame:
        """Backward-adjust OHLC prices and volumes for corporate actions.

        For a split with ratio_factor=2.0 (1 share → 2 shares):
        - Pre-split prices are **divided** by ratio_factor (e.g. ₹2656 → ₹1328)
        - Pre-split volumes are **multiplied** by ratio_factor (more shares)

        Processed in descending ex_date order so adjustments compound correctly.
        """
        price_cols = ["open", "high", "low", "close"]

        for _, action in actions.iterrows():
            ex_date = action["ex_date"]
            factor = action["ratio_factor"]

            if factor is None or factor == 1.0 or factor == 0.0:
                continue

            mask = df["date"] < ex_date

            # Prices: divide by factor (backward-adjust to post-action level)
            for col in price_cols:
                df.loc[mask, col] = df.loc[mask, col] / factor

            # Volume/delivery: multiply by factor (more shares post-action)
            df.loc[mask, "volume"] = (df.loc[mask, "volume"] * factor).astype(int)
            df.loc[mask, "delivery_qty"] = (df.loc[mask, "delivery_qty"] * factor).astype(int)

            logger.info(
                "Applied %s adjustment (factor=%.4f) at %s: %d rows adjusted",
                action["ca_type"], factor, ex_date.date(), mask.sum(),
            )

        return df

