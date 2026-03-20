from src.trading.signals.base import SignalInterface
from src.trading.signals.divergence import LongDivergenceSignal
from src.trading.signals.price_divergence import PriceDivergenceSignal
from src.trading.signals.nextgen import NextGenSignal
from src.trading.signals.savgol_cts import SavgolCTSSignal


class SignalFactory:
    """Factory to instantiate trading signals."""

    @staticmethod
    def get_signal(name: str) -> SignalInterface:
        """
        Get a signal implementation by name.

        Supported signals:
            - 'long_divergence': The original divergence-based long entry/exit strategy.
            - 'price_divergence': CTS slope + PSZ crossing heuristic signal.
            - 'nextgen': 4-gate empirically-grounded signal.
            - 'savgol_cts': CTS -1/+1 mean-reversion signal with coherence/pdd/regime gates.
        """
        if name == "long_divergence":
            return LongDivergenceSignal()
        elif name == "price_divergence":
            return PriceDivergenceSignal()
        elif name == "nextgen":
            return NextGenSignal()
        elif name == "savgol_cts":
            return SavgolCTSSignal()

        raise ValueError(f"Unknown signal type: {name}")
