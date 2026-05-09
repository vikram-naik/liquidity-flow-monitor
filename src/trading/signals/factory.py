from src.trading.signals.base import SignalInterface
from src.trading.signals.savgol_cts import SavgolCTSSignal


class SignalFactory:
    """Factory to instantiate trading signals."""

    @staticmethod
    def get_signal(name: str) -> SignalInterface:
        """
        Get a signal implementation by name.

        Supported signals:
            - 'savgol_cts': CTS -1/+1 mean-reversion signal with coherence/pdd/regime gates.
        """
        if name == "savgol_cts":
            return SavgolCTSSignal()

        raise ValueError(f"Unknown signal type: {name}")
