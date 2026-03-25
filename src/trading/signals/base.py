from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd


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
    entry_tag: str = ""               # entry path identifier (e.g. "PSZ", "BT-cross")
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
    # CWVAP context on signal day
    entry_cwvap_bullish: bool = False
    exit_cwvap_bullish: bool = False


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
        records: list[dict] | None = None,
        idx: int = 0,
    ) -> tuple[str | None, int]:
        """
        Check if the current bar meets any exit conditions.
        
        Returns:
            reason (str | None): Exit reason if trailing, else None
            delivery_bad_count (int): Updated delivery bad count
        """
        pass

    def tag_signals(
        self,
        df: pd.DataFrame,
        entry_cfg: BaseEntryConfig | None = None,
        exit_cfg: BaseExitConfig | None = None,
    ) -> pd.DataFrame:
        """Run the full EOD-lag simulation over a ledger DataFrame.

        Returns a copy of df with four columns added:
            entry_signal  — intensity int (>0) on the signal bar, else 0
            entry_reason  — reason string on every evaluated bar, else None
            exit_signal   — 1 on the exit bar, else 0
            exit_reason   — reason string on the exit bar, else None
        """
        records = df.to_dict("records")
        n = len(records)

        entry_flags   = [0]    * n
        entry_reasons = [None] * n
        cooldown_flags = [False] * n
        exit_flags    = [0]    * n
        exit_reasons  = [None] * n

        in_trade = False
        trade: Trade | None = None
        peak_close = 0.0
        delivery_bad_count = 0
        pending_entry: dict | None = None
        cwvap_values = [records[0].get("cwvap", np.nan)]

        for i in range(1, n):
            row  = records[i]
            prev = records[i - 1]
            close = row.get("close", np.nan)
            cwvap_values.append(row.get("cwvap", np.nan))

            if isinstance(close, float) and np.isnan(close):
                continue

            if in_trade and trade is not None:
                if close > peak_close:
                    peak_close = close
                bars_held = i - trade.entry_idx
                reason, delivery_bad_count = self.check_exit(
                    row, prev, trade, peak_close, bars_held,
                    delivery_bad_count, cwvap_values, exit_cfg,
                    records, i,
                )
                if reason:
                    exit_flags[i]   = 1
                    exit_reasons[i] = reason
                    in_trade = False
                    trade = None
                    delivery_bad_count = 0

            elif pending_entry is not None:
                atr = row.get("atr_20", 0) or 0
                if not atr or np.isnan(float(atr)):
                    atr = close * 0.02
                trade = Trade(
                    symbol="",
                    entry_date=str(row.get("date", ""))[:10],
                    entry_price=close,
                    entry_idx=i,
                    atr_at_entry=atr,
                    soft_filters_passed=pending_entry["intensity"],
                    regime_at_entry=str(row.get("regime", "")),
                    entry_tag=pending_entry.get("entry_tag", ""),
                    psz_at_entry=row.get("price_slope_z", 0.0) or 0.0,
                )
                peak_close = close
                delivery_bad_count = 0
                in_trade = True
                pending_entry = None

            else:
                ok, intensity, det = self.check_entry(row, prev, entry_cfg, records, i)
                entry_reasons[i] = det.get("reason")
                cooldown_flags[i] = det.get("cooldown", False)
                if ok:
                    entry_flags[i] = intensity
                    pending_entry = {"intensity": intensity, "entry_tag": det.get("entry_tag", "")}

        df = df.copy()
        df["entry_signal"] = entry_flags
        df["entry_reason"] = entry_reasons
        df["cooldown"]     = cooldown_flags
        df["exit_signal"]  = exit_flags
        df["exit_reason"]  = exit_reasons
        return df
