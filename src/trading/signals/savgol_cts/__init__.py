"""SavgolCTS signal package — CTS mean-reversion with multiple entry/exit paths.

Public API (backwards-compatible with the former single-module):
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
