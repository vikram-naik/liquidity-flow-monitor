"""
LiveResolver — live market quotes via Kite API (pykiteconnect).

Will use LTP / bid-ask quotes to determine limit prices.
Requires daily access token from Kite Connect login flow.
"""

from __future__ import annotations

from src.trading.price_resolver.base import PriceContext, PriceResolver, OrderPrice


class LiveResolver(PriceResolver):
    """Live market quotes via Kite API (pykiteconnect).

    Will use LTP / bid-ask quotes to determine limit prices.
    Requires daily access token from Kite Connect login flow.
    """

    def __init__(self):
        raise NotImplementedError(
            "LiveResolver is not yet implemented. "
            "Set price_resolver=historical in trading_config to use HistoricalResolver."
        )

    def resolve(self, ctx: PriceContext) -> OrderPrice:
        raise NotImplementedError
