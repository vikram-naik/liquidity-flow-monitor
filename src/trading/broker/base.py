"""
Broker abstraction — base class for all broker implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Broker(ABC):
    @abstractmethod
    def place_order(self, symbol: str, qty: int, side: str, price: float | None = None) -> dict:
        """Place an order. Returns order dict with at least 'order_id'."""

    @abstractmethod
    def get_order_status(self, order_id: str) -> dict:
        """Get order status by order_id."""
