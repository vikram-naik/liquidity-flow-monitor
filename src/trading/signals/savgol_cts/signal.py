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
from src.trading.signals.savgol_cts.entries.slope_bottom import check_slope_bottom

# Exit path checkers
from src.trading.signals.savgol_cts.exits.cwvap_guard import apply_cwvap_guard
from src.trading.signals.savgol_cts.exits.slope_bottom import exit_slope_bottom


class SavgolCTSSignal(SignalInterface):
    """CTS -1/+1 mean-reversion signal with one entry path.

    Entry paths:
        1. Slope Bottom   — cts_slope inflects from deep negative in a downtrend.

    Exit paths (dispatched by entry tag):
        Slope Bottom                  → ``exit_slope_bottom``.

    The CWVAP guard runs after exits, suppressing or
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

        # Path 5: Slope Bottom
        passed, intensity, meta = check_slope_bottom(row, prev_row, cfg)
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

        from src.trading.signals.savgol_cts.state import SavgolCTSExitState
        st = SavgolCTSExitState.from_int(delivery_bad_count)

        # Update universal states
        close = row.get("close", np.nan)
        cwvap = row.get("cwvap", np.nan)
        if not np.isnan(close) and not np.isnan(cwvap) and close > cwvap:
            st.price_above_cwvap = True

        updated_state_val = st.to_int()
        tag = trade.entry_tag if trade is not None else ""

        # --- Path-specific exit ---
        if tag == EntryTag.SLOPE_BOTTOM.value:
            exit_status = exit_slope_bottom(
                row, prev_row, trade, peak_close, bars_held,
                updated_state_val, cfg, records, idx,
            )
        else:
            # Unknown entry tag — no exit logic, hold
            exit_status = (None, updated_state_val)

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
        # PnL cap and trailing stop bypass for Slope-Bottom only — these trades
        # crash through CWVAP too fast for suppression to help. CWVAP-Reclaim
        # benefits from suppression (CWVAP_EXHAUSTION runs > PNL_CAP).
        sb_hard_exit = (
            tag == EntryTag.SLOPE_BOTTOM.value
            and res in (ExitReason.PNL_CAP, ExitReason.TRAIL_STOP)
        )
        if res == ExitReason.BAR3_STOP or sb_hard_exit:            final_state = state_returned
        else:
            res, final_state = apply_cwvap_guard(
                row, trade, res, state_returned, cwvap_values, cfg, records, idx,
            )

        # Update cross-trade cooldown state with the FINAL decision
        if res is not None:
            self._last_exit_idx = idx
            self._last_exit_reason = res

        return res, final_state
