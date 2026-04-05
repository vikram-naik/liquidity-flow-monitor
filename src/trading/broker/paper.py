"""
PaperBroker — simulated broker for paper trading.
"""

from __future__ import annotations

import logging
from datetime import datetime

from src.trading.broker.base import Broker

logger = logging.getLogger(__name__)


class PaperBroker(Broker):
    """Simulated broker — logs orders, returns structured order dicts."""

    def place_order(self, symbol: str, qty: int, side: str, price: float | None = None) -> dict:
        ts = datetime.now()
        order_id = f"PAPER-{symbol}-{side}-{ts.strftime('%Y%m%d%H%M%S')}"
        turnover = qty * price if price else 0.0

        logger.info(
            "PAPER ORDER: %s %d x %s @ %s -> %s",
            side.upper(), qty, symbol,
            f"₹{price:.2f}" if price else "MARKET",
            order_id,
        )

        return {
            "order_id": order_id,
            "symbol": symbol,
            "side": side.upper(),
            "quantity": qty,
            "price": price or 0.0,
            "turnover": round(turnover, 2),
            "status": "COMPLETE",
            "executed_at": ts.isoformat(),
        }

    def get_order_status(self, order_id: str) -> dict:
        return {"order_id": order_id, "status": "COMPLETE", "broker": "paper"}
