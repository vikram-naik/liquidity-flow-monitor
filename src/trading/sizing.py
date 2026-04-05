"""
Position sizing — pluggable interface with equal-weight and Kelly implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


# ── Legacy function (backward compat for walk_forward.py) ──────────────────

def calculate_quantity(
    capital: float,
    max_positions: int,
    entry_price: float,
    atr: float,
    stop_atr_multiple: float = 2.0,
) -> int:
    """Calculate position size capped by allocation and per-trade risk.

    Args:
        capital: Total portfolio capital.
        max_positions: Maximum concurrent positions (for equal-weight split).
        entry_price: Expected entry price per share.
        atr: ATR at entry (absolute, not percentage).
        stop_atr_multiple: Stop distance in ATR multiples.

    Returns:
        Number of shares to buy (integer, >= 0).
    """
    if entry_price <= 0 or atr <= 0 or max_positions <= 0:
        return 0

    per_position = capital / max_positions
    risk_per_share = stop_atr_multiple * atr
    max_risk = capital * 0.02  # 2% portfolio risk cap per trade
    max_shares_by_risk = int(max_risk / risk_per_share)
    shares_by_allocation = int(per_position / entry_price)
    return min(shares_by_allocation, max_shares_by_risk)


# ── Sizing Interface ──────────────────────────────────────────────────────

@dataclass
class SizingContext:
    """Everything a sizing strategy needs to decide position size."""
    equity: float               # current portfolio equity
    cash_available: float       # cash not deployed
    max_positions: int
    open_position_count: int
    entry_price: float
    atr: float
    stop_atr_multiple: float = 2.0
    trade_history: list[dict] = field(default_factory=list)  # recent closed trades for Kelly


class SizingStrategy(ABC):
    """Interface for position sizing strategies."""

    @abstractmethod
    def calculate(self, ctx: SizingContext) -> int:
        """Return number of shares to buy (integer, >= 0)."""

    @abstractmethod
    def get_info(self) -> dict:
        """Return sizing metadata for audit trail (method name, params used)."""


class EqualWeightSizing(SizingStrategy):
    """Equal-weight allocation with ATR risk cap (wraps legacy logic)."""

    def calculate(self, ctx: SizingContext) -> int:
        if ctx.entry_price <= 0 or ctx.atr <= 0 or ctx.max_positions <= 0:
            return 0

        per_position = ctx.equity / ctx.max_positions
        allocation = min(per_position, ctx.cash_available)

        risk_per_share = ctx.stop_atr_multiple * ctx.atr
        max_risk = ctx.equity * 0.02
        max_shares_by_risk = int(max_risk / risk_per_share) if risk_per_share > 0 else 0

        shares_by_allocation = int(allocation / ctx.entry_price)
        return min(shares_by_allocation, max_shares_by_risk)

    def get_info(self) -> dict:
        return {"sizing_method": "equal_weight", "kelly_f": None}


class KellySizing(SizingStrategy):
    """Kelly criterion sizing with fractional multiplier and warm-up fallback."""

    def __init__(
        self,
        kelly_fraction: float = 0.25,
        kelly_window: int = 50,
        min_trades: int = 20,
        max_allocation: float = 0.30,
    ):
        self.kelly_fraction = kelly_fraction
        self.kelly_window = kelly_window
        self.min_trades = min_trades
        self.max_allocation = max_allocation
        self._last_kelly_f = 0.0
        self._fallback = EqualWeightSizing()

    def calculate(self, ctx: SizingContext) -> int:
        if ctx.entry_price <= 0 or ctx.atr <= 0 or ctx.max_positions <= 0:
            return 0

        kelly_f = self._compute_kelly(ctx.trade_history)

        if kelly_f <= 0:
            # Warm-up: not enough trades yet, fall back to equal-weight
            if len(ctx.trade_history) < self.min_trades:
                self._last_kelly_f = 1.0 / ctx.max_positions
                return self._fallback.calculate(ctx)
            # Kelly says no edge — skip trade
            self._last_kelly_f = 0.0
            return 0

        self._last_kelly_f = kelly_f
        allocation = min(ctx.equity * kelly_f, ctx.cash_available)
        shares = int(allocation / ctx.entry_price)

        # ATR risk cap
        risk_per_share = ctx.stop_atr_multiple * ctx.atr
        max_risk = ctx.equity * 0.02
        max_shares_by_risk = int(max_risk / risk_per_share) if risk_per_share > 0 else 0

        return min(shares, max_shares_by_risk)

    def _compute_kelly(self, trade_history: list[dict]) -> float:
        """f* = (b*p - q) / b, then apply fraction and clamp."""
        recent = trade_history[-self.kelly_window:]
        if len(recent) < self.min_trades:
            return 0.0

        pnls = [t.get("final_pnl_pct", 0) or 0 for t in recent]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p <= 0]

        if not winners or not losers:
            return 0.0

        p = len(winners) / len(pnls)
        q = 1.0 - p
        avg_win = float(np.mean(winners))
        avg_loss = abs(float(np.mean(losers)))

        if avg_loss == 0:
            return 0.0

        b = avg_win / avg_loss
        f_star = (b * p - q) / b

        if f_star <= 0:
            return 0.0

        return min(self.kelly_fraction * f_star, self.max_allocation)

    def get_info(self) -> dict:
        return {"sizing_method": "kelly", "kelly_f": round(self._last_kelly_f, 4)}


def get_sizing_strategy(name: str, **kwargs) -> SizingStrategy:
    """Factory — returns the sizing strategy by name."""
    if name == "equal_weight":
        return EqualWeightSizing()
    elif name == "kelly":
        return KellySizing(
            kelly_fraction=kwargs.get("kelly_fraction", 0.25),
            kelly_window=kwargs.get("kelly_window", 50),
            min_trades=kwargs.get("min_trades", 20),
            max_allocation=kwargs.get("max_allocation", 0.30),
        )
    raise ValueError(f"Unknown sizing strategy: {name}")
