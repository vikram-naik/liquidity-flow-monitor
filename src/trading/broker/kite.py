"""
KiteBroker — Zerodha Kite Connect broker (stub).
"""

from __future__ import annotations

from src.trading.broker.base import Broker


class KiteBroker(Broker):
    """Zerodha Kite broker — not yet implemented."""

    def __init__(self):
        raise NotImplementedError(
            "KiteBroker is not yet implemented. "
            "Set execution_mode=paper in trading_config to use PaperBroker."
        )

    def place_order(self, symbol: str, qty: int, side: str, price: float | None = None) -> dict:
        raise NotImplementedError

    def get_order_status(self, order_id: str) -> dict:
        raise NotImplementedError
