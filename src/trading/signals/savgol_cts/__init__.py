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
from src.trading.signals.savgol_cts.symbol_configs import (
    get_symbol_entry_config,
    get_symbol_exit_config,
)

__all__ = [
    "SavgolCTSEntryConfig",
    "SavgolCTSExitConfig",
    "SavgolCTSSignal",
    "get_symbol_entry_config",
    "get_symbol_exit_config",
]
