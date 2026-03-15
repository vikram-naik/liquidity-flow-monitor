"""
Broker abstraction — PaperBroker for simulation, KiteBroker stub for live.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime

logger = logging.getLogger(__name__)


class Broker(ABC):
    @abstractmethod
    def place_order(self, symbol: str, qty: int, side: str, price: float | None = None) -> str:
        """Place an order. Returns order_id string."""

    @abstractmethod
    def get_order_status(self, order_id: str) -> dict:
        """Get order status by order_id."""


class PaperBroker(Broker):
    """Simulated broker — logs orders, returns synthetic IDs."""

    def place_order(self, symbol: str, qty: int, side: str, price: float | None = None) -> str:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        order_id = f"PAPER-{symbol}-{side}-{ts}"
        logger.info(
            "PAPER ORDER: %s %d x %s @ %s → %s",
            side.upper(), qty, symbol,
            f"₹{price:.2f}" if price else "MARKET",
            order_id,
        )
        return order_id

    def get_order_status(self, order_id: str) -> dict:
        return {"order_id": order_id, "status": "COMPLETE", "broker": "paper"}


class KiteBroker(Broker):
    """Zerodha Kite broker — Phase 2 stub."""

    def __init__(self):
        raise NotImplementedError(
            "KiteBroker is not yet implemented. "
            "Set execution_mode=paper in trading_config to use PaperBroker."
        )

    def place_order(self, symbol: str, qty: int, side: str, price: float | None = None) -> str:
        raise NotImplementedError

    def get_order_status(self, order_id: str) -> dict:
        raise NotImplementedError
