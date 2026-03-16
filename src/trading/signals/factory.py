from src.trading.signals.base import SignalInterface
from src.trading.signals.divergence import LongDivergenceSignal
from src.trading.signals.price_divergence import PriceDivergenceSignal


class SignalFactory:
    """Factory to instantiate trading signals."""

    @staticmethod
    def get_signal(name: str) -> SignalInterface:
        """
        Get a signal implementation by name.

        Supported signals:
            - 'long_divergence': The original divergence-based long entry/exit strategy.
        """
        if name == "long_divergence":
            return LongDivergenceSignal()
        elif name == "price_divergence":
            return PriceDivergenceSignal()
        
        raise ValueError(f"Unknown signal type: {name}")
