"""
Price resolution — base class and data structures.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PriceContext:
    """All data a resolver needs to determine a limit price."""
    side: str                       # "BUY" or "SELL"
    symbol: str
    # Signal context
    entry_tag: str = ""             # for entries: the entry path tag
    exit_reason: str = ""           # for exits: the exit reason
    # Execution-day bar data (from DivergenceEngine ledger)
    close: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    cwvap: float = 0.0
    atr: float = 0.0
    # Position info (for exits)
    entry_price: float = 0.0
    capital_deployed: float = 0.0


@dataclass
class OrderPrice:
    """Resolved order price with audit trail."""
    limit_price: float
    rationale: str                  # human-readable, stored alongside order


class PriceResolver(ABC):
    """Base class for all price resolution strategies."""

    @abstractmethod
    def resolve(self, ctx: PriceContext) -> OrderPrice:
        """Determine the LIMIT price for an order.

        Args:
            ctx: Market data and signal context for the order.

        Returns:
            OrderPrice with the resolved limit price and rationale.
        """
