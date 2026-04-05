"""
Trade expense calculator — pluggable interface for brokerage, STT, and statutory charges.

Supports Indian equity delivery trading cost models.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ChargesBreakdown:
    """Itemised expense breakdown for a single order leg (₹ absolute)."""
    brokerage: float
    stt: float
    exchange_txn: float
    gst: float
    sebi_fee: float
    stamp_duty: float
    total: float


class ChargesCalculator(ABC):
    """Interface for computing trade charges on a single order leg."""

    @abstractmethod
    def compute(self, side: str, quantity: int, price: float) -> ChargesBreakdown:
        """Compute charges for one leg of a trade.

        Args:
            side: 'BUY' or 'SELL'.
            quantity: Number of shares.
            price: Execution price per share.

        Returns:
            ChargesBreakdown with all charge components.
        """


class ZerodhaDeliveryCharges(ChargesCalculator):
    """Zerodha equity delivery charge model (as of 2025).

    Rates:
        Brokerage      : ₹0 for delivery trades
        STT            : 0.1% of turnover (buy + sell)
        Exchange txn   : 0.00345% of turnover (NSE)
        GST            : 18% on (brokerage + exchange txn)
        SEBI fee       : 0.0001% of turnover
        Stamp duty     : 0.015% of turnover (buy side only)
    """

    BROKERAGE_RATE = 0.0            # ₹0 for delivery
    STT_RATE = 0.001                # 0.1%
    EXCHANGE_TXN_RATE = 0.0000345   # 0.00345%
    GST_RATE = 0.18                 # 18% on (brokerage + exchange txn)
    SEBI_FEE_RATE = 0.000001        # 0.0001%
    STAMP_DUTY_RATE = 0.00015       # 0.015% — buy side only

    def compute(self, side: str, quantity: int, price: float) -> ChargesBreakdown:
        turnover = quantity * price

        brokerage = turnover * self.BROKERAGE_RATE
        stt = turnover * self.STT_RATE
        exchange_txn = turnover * self.EXCHANGE_TXN_RATE
        gst = (brokerage + exchange_txn) * self.GST_RATE
        sebi_fee = turnover * self.SEBI_FEE_RATE
        stamp_duty = turnover * self.STAMP_DUTY_RATE if side == "BUY" else 0.0

        total = brokerage + stt + exchange_txn + gst + sebi_fee + stamp_duty

        return ChargesBreakdown(
            brokerage=round(brokerage, 2),
            stt=round(stt, 2),
            exchange_txn=round(exchange_txn, 2),
            gst=round(gst, 2),
            sebi_fee=round(sebi_fee, 2),
            stamp_duty=round(stamp_duty, 2),
            total=round(total, 2),
        )


def get_charges_calculator(model: str = "zerodha") -> ChargesCalculator:
    """Factory — returns the charges calculator for the given brokerage model."""
    if model == "zerodha":
        return ZerodhaDeliveryCharges()
    raise ValueError(f"Unknown brokerage model: {model}")
