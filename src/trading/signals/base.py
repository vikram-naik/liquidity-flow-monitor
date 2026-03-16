from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class BaseEntryConfig(ABC):
    """Base class for signal entry configuration."""
    pass


@dataclass
class BaseExitConfig(ABC):
    """Base class for signal exit configuration."""
    pass


@dataclass
class Trade:
    """Common Trade construct shared across signals."""
    symbol: str
    entry_date: str
    entry_price: float
    entry_idx: int
    atr_at_entry: float
    soft_filters_passed: int
    # Individual filter flags
    rdv_pass: bool = False
    mcs_pass: bool = False
    cwc_pass: bool = False
    grad_pass: bool = False
    regime_at_entry: str = ""
    psz_at_entry: float = 0.0
    psz_peak: float = 0.0             # track highest PSZ during trade
    # Filled on exit
    exit_date: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_pct: float = 0.0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    duration: int = 0


class SignalInterface(ABC):
    """Interface that all signal strategies must implement."""

    @abstractmethod
    def check_entry(
        self,
        row: dict,
        prev_row: dict,
        cfg: BaseEntryConfig,
        records: list[dict] | None = None,
        idx: int = 0,
    ) -> tuple[bool, int, dict]:
        """
        Check if the current bar qualifies for an entry.
        
        Returns:
            qualifies (bool): True if entry conditions are met
            soft_filter_count (int): Number of soft filters passed
            details (dict): Detailed dictionary of filter results
        """
        pass

    @abstractmethod
    def check_exit(
        self,
        row: dict,
        prev_row:dict,
        trade: Trade,
        peak_close: float,
        bars_held: int,
        delivery_bad_count: int,
        cwvap_values: list[float],
        cfg: BaseExitConfig,
    ) -> tuple[str | None, int]:
        """
        Check if the current bar meets any exit conditions.
        
        Returns:
            reason (str | None): Exit reason if trailing, else None
            delivery_bad_count (int): Updated delivery bad count
        """
        pass
