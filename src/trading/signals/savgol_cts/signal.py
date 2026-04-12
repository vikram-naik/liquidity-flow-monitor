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
from src.trading.signals.savgol_cts.entries.accel_cross import check_entry_accel_cross
from src.trading.signals.savgol_cts.entries.institutional_floor import check_institutional_floor
from src.trading.signals.savgol_cts.entries.range_reversion import check_range_reversion
from src.trading.signals.savgol_cts.entries.accel_zero_cross import check_accel_zero_cross

# Exit path checkers
from src.trading.signals.savgol_cts.exits.cwvap_guard import apply_cwvap_guard
from src.trading.signals.savgol_cts.exits.slope_bottom import exit_slope_bottom
from src.trading.signals.savgol_cts.exits.accel_cross import check_exit_accel_cross
from src.trading.signals.savgol_cts.exits.institutional_floor import exit_institutional_floor
from src.trading.signals.savgol_cts.exits.range_reversion import exit_range_reversion
from src.trading.signals.savgol_cts.exits.accel_zero_cross import exit_accel_zero_cross


class SavgolCTSSignal(SignalInterface):
    """CTS -1/+1 mean-reversion signal with multiple entry paths.

    Entry paths:
        1. Accel Cross    — Triple-trend momentum cross with institutional alignment.
        2. Slope Bottom   — cts_slope inflects from deep negative in a downtrend.
        3. Institutional Floor — Sustained PSZ recovery with institutional alignment.
        4. Range Reversion — Price range mean-reversion with institutional alignment.

    Exit paths (dispatched by entry tag):
        Accel Cross            → ``check_exit_accel_cross``.
        Slope Bottom           → ``exit_slope_bottom``.
        Institutional Floor    → ``exit_institutional_floor``.
        Range Reversion        → ``exit_range_reversion``.

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

        rejections = []

        # Path 2: Accel Cross
        passed, intensity, meta = check_entry_accel_cross(row, prev_row, cfg.accel_cross, records, idx)
        if passed:
            return True, intensity, meta
        if cfg.accel_cross.enabled:
            rejections.append(f"Accel: {meta.get('reason', 'Failed')}")

        # Path 5: Slope Bottom
        passed, intensity, meta = check_slope_bottom(row, prev_row, cfg)
        if passed:
            return True, intensity, meta
        if cfg.slope_bottom.enabled:
            rejections.append(f"Slope: {meta.get('reason', 'Failed')}")

        # Path 3: Institutional Floor
        passed, intensity, meta = check_institutional_floor(row, prev_row, cfg, records, idx)
        if passed:
            return True, intensity, meta
        if cfg.institutional_floor.enabled:
            rejections.append(f"IFloor: {meta.get('reason', 'Failed')}")

        # Path 6: Range Reversion
        passed, intensity, meta = check_range_reversion(row, prev_row, cfg, records, idx)
        if passed:
            return True, intensity, meta
        if cfg.range_reversion.enabled:
            rejections.append(f"Range: {meta.get('reason', 'Failed')}")

        # Path 7: Accel Zero Cross
        passed, intensity, meta = check_accel_zero_cross(row, prev_row, cfg.accel_zero_cross, records, idx)
        if passed:
            return True, intensity, meta
        if cfg.accel_zero_cross.enabled:
            rejections.append(f"AccelZero: {meta.get('reason', 'Failed')}")

        return False, 0, {"reason": " | ".join(rejections)}

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

        # Update universal states (Inst-Floor manages its own bit semantics)
        tag = trade.entry_tag if trade is not None else ""
        close = row.get("close", np.nan)
        cwvap = row.get("cwvap", np.nan)
        if tag != EntryTag.INSTITUTIONAL_FLOOR.value:
            if not np.isnan(close) and not np.isnan(cwvap) and close > cwvap:
                st.price_above_cwvap = True

        updated_state_val = st.to_int()

        # --- Path-specific exit ---
        if tag == EntryTag.SLOPE_BOTTOM.value:
            exit_status = exit_slope_bottom(
                row, prev_row, trade, peak_close, bars_held,
                updated_state_val, cfg, records, idx,
            )
        elif tag == EntryTag.ACCEL.value:
            exit_status = check_exit_accel_cross(
                records, idx, trade, updated_state_val, cfg.accel_cross, cfg.cwvap_guard, cfg
            )
        elif tag == EntryTag.INSTITUTIONAL_FLOOR.value:
            exit_status = exit_institutional_floor(
                row, prev_row, trade, peak_close, bars_held,
                updated_state_val, cfg, records, idx,
            )
        elif tag == EntryTag.RANGE_REVERSION.value:
            exit_status = exit_range_reversion(
                row, prev_row, trade, peak_close, bars_held,
                updated_state_val, cfg, records, idx,
            )
        elif tag == EntryTag.ACCEL_ZERO_CROSS.value:
            exit_status = exit_accel_zero_cross(
                row, prev_row, trade, peak_close, bars_held,
                updated_state_val, cfg.accel_zero_cross, records, idx,
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
        accel_zero_hard_exit = (
            tag == EntryTag.ACCEL_ZERO_CROSS.value
            and res in (ExitReason.PNL_CAP, ExitReason.TRAIL_STOP)
        )
        if_bespoke_exit = tag == EntryTag.INSTITUTIONAL_FLOOR.value
        accel_bespoke_exit = tag == EntryTag.ACCEL.value
        rr_bespoke_exit = tag == EntryTag.RANGE_REVERSION.value
        
        if res == ExitReason.BAR3_STOP or sb_hard_exit or accel_zero_hard_exit or if_bespoke_exit or accel_bespoke_exit or rr_bespoke_exit:
            final_state = state_returned
        else:
            res, final_state = apply_cwvap_guard(
                row, trade, res, state_returned, cfg, records, idx,
            )

        # Update cross-trade cooldown state with the FINAL decision
        if res is not None:
            self._last_exit_idx = idx
            self._last_exit_reason = res

        return res, final_state
