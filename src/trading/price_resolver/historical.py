"""
HistoricalResolver — backtest-consistent price resolution using ledger bar data.
"""

from __future__ import annotations

from src.trading.price_resolver.base import PriceContext, PriceResolver, OrderPrice


class HistoricalResolver(PriceResolver):
    """Matches walk_forward.py backtest execution logic.

    - BUY (entry):  execution-day close  (walk_forward.py line ~146)
    - SELL (exit):   execution-day open   (walk_forward.py line ~99)

    When the executor runs during market hours, the engine returns the
    most recent complete bar — which is the "execution day" bar from
    the backtest's perspective.
    """

    def resolve(self, ctx: PriceContext) -> OrderPrice:
        if ctx.side == "BUY":
            return OrderPrice(
                limit_price=round(ctx.close, 2),
                rationale="Historical: execution-day close",
            )
        # SELL — manual exit uses close price
        if ctx.exit_reason == "manual_exit":
            return OrderPrice(
                limit_price=round(ctx.close, 2),
                rationale="Historical: manual exit at close",
            )
        # SELL — prefer open, fall back to close
        if ctx.open > 0:
            return OrderPrice(
                limit_price=round(ctx.open, 2),
                rationale="Historical: execution-day open",
            )
        return OrderPrice(
            limit_price=round(ctx.close, 2),
            rationale="Historical: fallback to close (open unavailable)",
        )
