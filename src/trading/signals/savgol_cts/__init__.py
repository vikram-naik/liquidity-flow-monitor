"""SavgolCTS signal package — CTS mean-reversion with Universal ML Master Path.

Public API:
    - SavgolCTSEntryConfig / SavgolCTSExitConfig  — configuration
    - SavgolCTSSignal                               — signal implementation
"""

from src.trading.signals.savgol_cts.config import (
    SavgolCTSEntryConfig,
    SavgolCTSExitConfig,
)
from src.trading.signals.savgol_cts.signal import SavgolCTSSignal

__all__ = [
    "SavgolCTSEntryConfig",
    "SavgolCTSExitConfig",
    "SavgolCTSSignal",
]
