"""Entry path checkers for SavgolCTS signal."""

from src.trading.signals.savgol_cts.entries.universal_cross import entry_universal_cross
from src.trading.signals.savgol_cts.entries.flow_momentum import entry_flow_momentum

__all__ = [
    "entry_universal_cross",
    "entry_flow_momentum",
]
