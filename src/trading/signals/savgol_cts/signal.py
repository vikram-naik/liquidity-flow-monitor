"""SavgolCTS signal orchestrator.

Dispatches entry checks to per-path modules (priority order) and exit
checks to path-specific exit modules.  Cross-path concerns (cooldown,
ST exit, CWVAP guard) live here.

The ``delivery_bad_count`` parameter from the base interface is repurposed
as a bitfield tracking per-trade state (see ``state.py``).
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import BaseEntryConfig, BaseExitConfig, SignalInterface, Trade
from src.trading.signals.enums import EntryTag, ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig

# Entry path checkers
from src.trading.signals.savgol_cts.entries.floor_touch import check_floor_touch
from src.trading.signals.savgol_cts.entries.floor_leave import check_floor_leave
from src.trading.signals.savgol_cts.entries.bt_cross import check_bt_crossover
from src.trading.signals.savgol_cts.entries.cwvap_reclaim import check_cwvap_reclaim
from src.trading.signals.savgol_cts.entries.cwvap_cross import check_cwvap_cross

# Exit path checkers
from src.trading.signals.savgol_cts.exits.floor import exit_floor
from src.trading.signals.savgol_cts.exits.bt_cross import exit_bt_cross
from src.trading.signals.savgol_cts.exits.psz_glide import exit_psz_glide
from src.trading.signals.savgol_cts.exits.cwvap_guard import apply_cwvap_guard
from src.trading.signals.savgol_cts.exits.cwvap_reclaim import exit_cwvap_reclaim


class SavgolCTSSignal(SignalInterface):
    """CTS -1/+1 mean-reversion signal with four entry paths.

    Entry paths (priority order):
        0. Floor Touch    — CTS + BT pinned at floor, PSZ deeply oversold.
        1. Floor Leave    — CTS rises above floor after being pinned.
        2. BT-Cross       — CTS crosses BT from below in oversold zone.
        3. CWVAP Reclaim  — cts_slope crosses zero, close > CWVAP, PSZ > 0.

    Exit paths (dispatched by entry tag):
        Floor/Floor-Leave/Floor-Touch → ``exit_floor`` + PSZ glide fallback.
        BT-Cross                      → ``exit_bt_cross``.
        CWVAP Reclaim                 → ``exit_cwvap_reclaim`` (close < CWVAP).

    The CWVAP guard runs after non-CWVAP-Reclaim exits, suppressing or
    releasing based on price position and momentum.
    """

    def __init__(self):
        super().__init__()
        # Cross-trade state for cooldowns
        self._last_exit_idx: int = -1
        self._last_exit_reason: ExitReason | str = ""

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    def check_entry(
        self,
        row: dict,
        prev_row: dict,
        cfg: BaseEntryConfig,
        records: list[dict] | None = None,
        idx: int = 0,
    ) -> tuple[bool, int, dict]:
        if not isinstance(cfg, SavgolCTSEntryConfig):
            cfg = SavgolCTSEntryConfig()

        # Cross-path cooldown
        if cfg.cooldown_enabled and self._last_exit_idx != -1:
            bars_since_exit = idx - self._last_exit_idx
            if 0 <= bars_since_exit <= cfg.cooldown_bars:
                if any(r == self._last_exit_reason for r in cfg.cooldown_exit_reasons):
                    return False, 0, {
                        "reason": (
                            f"Cooldown active ({bars_since_exit}/{cfg.cooldown_bars}"
                            f" bars after {self._last_exit_reason})"
                        ),
                        "cooldown": True,
                    }

        cts = row.get("cts", np.nan)
        if np.isnan(cts):
            return False, 0, {"reason": "Missing CTS data"}

        # Path 0: Floor Touch
        passed, intensity, meta = check_floor_touch(row, prev_row, cfg)
        if passed:
            return True, intensity, meta

        # Path 1: CTS-Floor-Leave
        passed, intensity, meta = check_floor_leave(row, prev_row, cfg)
        if passed:
            return True, intensity, meta

        # Path 2: BT-Cross
        passed, intensity, meta = check_bt_crossover(row, prev_row, cfg, records, idx)
        if passed:
            return True, intensity, meta

        # Path 3: CWVAP Reclaim
        passed, intensity, meta = check_cwvap_reclaim(row, prev_row, cfg)
        if passed:
            return True, intensity, meta

        # Path 4: CWVAP Cross
        passed, intensity, meta = check_cwvap_cross(row, prev_row, cfg)
        if passed:
            return True, intensity, meta

        return False, 0, meta

    # ------------------------------------------------------------------
    # Exit
    # ------------------------------------------------------------------

    def check_exit(
        self,
        row: dict,
        prev_row: dict,
        trade: Trade,
        peak_close: float,
        bars_held: int,
        delivery_bad_count: int,
        cwvap_values: list[float],
        cfg: BaseExitConfig,
        records: list[dict] | None = None,
        idx: int = 0,
    ) -> tuple[str | None, int]:
        """Exit logic, dispatched by entry path then post-processed by CWVAP guard."""
        if not isinstance(cfg, SavgolCTSExitConfig):
            cfg = SavgolCTSExitConfig()

        tag = trade.entry_tag if trade is not None else ""

        # --- Path-specific exit ---
        if tag in (
            EntryTag.CTS_FLOOR_LEAVE.value,
            EntryTag.CTS_BT_FLOOR.value,
            EntryTag.CTS_FLOOR_TOUCH.value,
        ):
            exit_status = exit_floor(
                row, prev_row, trade, peak_close, bars_held,
                delivery_bad_count, cfg, records, idx,
            )
            # If floor exit returned None, check PSZ glide as fallback
            if exit_status[0] is None:
                exit_status = exit_psz_glide(
                    row, prev_row, trade, exit_status[1], cfg, records, idx,
                )
        elif tag == EntryTag.BT_CROSS.value:
            exit_status = exit_bt_cross(
                row, prev_row, trade, peak_close, bars_held,
                delivery_bad_count, cfg, records, idx,
            )
        elif tag in (EntryTag.CWVAP_RECLAIM.value, EntryTag.CWVAP_CROSS.value):
            exit_status = exit_cwvap_reclaim(
                row, prev_row, trade, peak_close, bars_held,
                delivery_bad_count, cfg, records, idx,
            )
        else:
            # Unknown entry tag — no exit logic, hold
            exit_status = (None, delivery_bad_count)

        res, state_returned = exit_status

        # --- Universal Sell Threshold Exit (feature toggle) ---
        if cfg.st_exit_enabled and res is None:
            st_val = row.get("cts_sell_threshold", np.nan)
            prev_st_val = prev_row.get("cts_sell_threshold", np.nan)
            cts_val = row.get("cts", np.nan)
            prev_cts_val = prev_row.get("cts", np.nan)

            if (
                not np.isnan(st_val)
                and not np.isnan(prev_st_val)
                and not np.isnan(cts_val)
                and not np.isnan(prev_cts_val)
            ):
                # Ensure we are not in the ceiling zone
                if cts_val < 1.0 and st_val < 1.0:
                    if prev_cts_val >= prev_st_val and cts_val < st_val:
                        res = ExitReason.ST_CROSS

        # --- CWVAP Guard (suppression / release) ---
        # Bar-3 stop and CWVAP Reclaim exits are unconditional — bypass suppression.
        if res == ExitReason.BAR3_STOP or tag in (EntryTag.CWVAP_RECLAIM.value, EntryTag.CWVAP_CROSS.value):
            final_state = state_returned
        else:
            res, final_state = apply_cwvap_guard(
                row, trade, res, state_returned, cwvap_values, cfg, records, idx,
            )

        # Update cross-trade cooldown state with the FINAL decision
        if res is not None:
            self._last_exit_idx = idx
            self._last_exit_reason = res

        return res, final_state
