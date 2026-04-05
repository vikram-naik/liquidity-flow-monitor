"""
Price resolver package — pluggable LIMIT price determination for orders.

Usage:
    from src.trading.price_resolver import PriceContext, OrderPrice, get_price_resolver
    resolver = get_price_resolver("historical")
    order = resolver.resolve(PriceContext(side="BUY", symbol="RELIANCE", close=2400.0))
"""

from src.trading.price_resolver.base import PriceContext, OrderPrice, PriceResolver
from src.trading.price_resolver.historical import HistoricalResolver
from src.trading.price_resolver.live import LiveResolver

__all__ = [
    "PriceContext", "OrderPrice", "PriceResolver",
    "HistoricalResolver", "LiveResolver",
    "get_price_resolver",
]


def get_price_resolver(name: str) -> PriceResolver:
    """Instantiate a PriceResolver by config name.

    Supported:
        - "historical" — HistoricalResolver (backtest-consistent, default)
        - "live"       — LiveResolver (Kite API, not yet implemented)
    """
    if name == "historical":
        return HistoricalResolver()
    if name == "live":
        return LiveResolver()
    raise ValueError(f"Unknown price resolver: '{name}'. Use 'historical' or 'live'.")
