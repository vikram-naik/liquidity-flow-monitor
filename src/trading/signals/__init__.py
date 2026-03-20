from src.trading.signals.base import BaseEntryConfig, BaseExitConfig, SignalInterface, Trade
from src.trading.signals.divergence import (
    LongDivergenceEntryConfig,
    LongDivergenceExitConfig,
    LongDivergenceSignal,
    FAVORABLE_SHAPES,
)
from src.trading.signals.price_divergence import (
    PriceDivergenceEntryConfig,
    PriceDivergenceExitConfig,
    PriceDivergenceSignal,
)
from src.trading.signals.nextgen import (
    NextGenEntryConfig,
    NextGenExitConfig,
    NextGenSignal,
)
from src.trading.signals.savgol_cts import (
    SavgolCTSEntryConfig,
    SavgolCTSExitConfig,
    SavgolCTSSignal,
)
from src.trading.signals.factory import SignalFactory

__all__ = [
    "BaseEntryConfig",
    "BaseExitConfig",
    "SignalInterface",
    "Trade",
    "LongDivergenceEntryConfig",
    "LongDivergenceExitConfig",
    "LongDivergenceSignal",
    "PriceDivergenceEntryConfig",
    "PriceDivergenceExitConfig",
    "PriceDivergenceSignal",
    "NextGenEntryConfig",
    "NextGenExitConfig",
    "NextGenSignal",
    "SavgolCTSEntryConfig",
    "SavgolCTSExitConfig",
    "SavgolCTSSignal",
    "FAVORABLE_SHAPES",
    "SignalFactory",
]
