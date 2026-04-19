"""SavgolCTS signal orchestrator.

Dispatches entry checks to per-path modules (priority order) and exit
checks to path-specific exit modules.  Cross-path concerns (cooldown,
ST exit, CWVAP guard) live here.
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
from src.trading.signals.savgol_cts.entries.prt_slope_zero_cross import check_prt_slope_zero_cross
from src.trading.signals.savgol_cts.entries.fas_zero_cross import check_fas_zero_cross

# Exit path checkers
from src.trading.signals.savgol_cts.exits.cwvap_guard import apply_cwvap_guard
from src.trading.signals.savgol_cts.exits.slope_bottom import exit_slope_bottom
from src.trading.signals.savgol_cts.exits.accel_cross import check_exit_accel_cross
from src.trading.signals.savgol_cts.exits.institutional_floor import exit_institutional_floor
from src.trading.signals.savgol_cts.exits.range_reversion import exit_range_reversion
from src.trading.signals.savgol_cts.exits.accel_zero_cross import exit_accel_zero_cross
from src.trading.signals.savgol_cts.exits.prt_slope_zero_cross import exit_prt_slope_zero_cross
from src.trading.signals.savgol_cts.exits.fas_zero_cross import exit_fas_zero_cross


class SavgolCTSSignal(SignalInterface):
    """CTS -1/+1 mean-reversion signal with multiple entry paths."""

    def __init__(self):
        super().__init__()
        self._last_exit_idx: int = -1
        self._last_exit_reason: ExitReason | str = ""

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

        if cfg.cooldown_enabled and self._last_exit_idx != -1:
            bars_since_exit = idx - self._last_exit_idx
            if 0 <= bars_since_exit <= cfg.cooldown_bars:
                if any(r == self._last_exit_reason for r in cfg.cooldown_exit_reasons):
                    return False, 0, {"reason": "Cooldown active", "cooldown": True}

        cts = row.get("cts", np.nan)
        if np.isnan(cts):
            return False, 0, {"reason": "Missing CTS data"}

        rejections = []

        # Path 9: FAS Zero Cross
        passed, intensity, meta = check_fas_zero_cross(row, prev_row, cfg.fas_zero_cross, records, idx)
        if passed: return True, intensity, meta
        if cfg.fas_zero_cross.enabled: rejections.append(f"FASZero: {meta.get('reason', 'Failed')}")

        # Accel Cross
        passed, intensity, meta = check_entry_accel_cross(row, prev_row, cfg.accel_cross, records, idx)
        if passed: return True, intensity, meta
        if cfg.accel_cross.enabled: rejections.append(f"Accel: {meta.get('reason', 'Failed')}")

        # Slope Bottom
        passed, intensity, meta = check_slope_bottom(row, prev_row, cfg)
        if passed: return True, intensity, meta
        if cfg.slope_bottom.enabled: rejections.append(f"Slope: {meta.get('reason', 'Failed')}")

        # Institutional Floor
        passed, intensity, meta = check_institutional_floor(row, prev_row, cfg, records, idx)
        if passed: return True, intensity, meta
        if cfg.institutional_floor.enabled: rejections.append(f"IFloor: {meta.get('reason', 'Failed')}")

        # Range Reversion
        passed, intensity, meta = check_range_reversion(row, prev_row, cfg, records, idx)
        if passed: return True, intensity, meta
        if cfg.range_reversion.enabled: rejections.append(f"Range: {meta.get('reason', 'Failed')}")

        # Accel Zero Cross
        passed, intensity, meta = check_accel_zero_cross(row, prev_row, cfg.accel_zero_cross, records, idx)
        if passed: return True, intensity, meta
        if cfg.accel_zero_cross.enabled: rejections.append(f"AccelZero: {meta.get('reason', 'Failed')}")

        # PRT Slope Zero Cross
        passed, intensity, meta = check_prt_slope_zero_cross(row, prev_row, cfg.prt_slope_zero_cross, records, idx)
        if passed: return True, intensity, meta
        if cfg.prt_slope_zero_cross.enabled: rejections.append(f"PRTSlopeZero: {meta.get('reason', 'Failed')}")

        # FAS Zero Cross
        passed, intensity, meta = check_fas_zero_cross(row, prev_row, cfg.fas_zero_cross, records, idx)
        if passed: return True, intensity, meta
        if cfg.fas_zero_cross.enabled: rejections.append(f"FASZero: {meta.get('reason', 'Failed')}")

        return False, 0, {"reason": " | ".join(rejections)}

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
        if not isinstance(cfg, SavgolCTSExitConfig):
            cfg = SavgolCTSExitConfig()

        from src.trading.signals.savgol_cts.state import SavgolCTSExitState
        st = SavgolCTSExitState.from_int(delivery_bad_count)
        tag = trade.entry_tag if trade is not None else ""
        close = row.get("close", np.nan)
        cwvap = row.get("cwvap", np.nan)
        if tag != EntryTag.INSTITUTIONAL_FLOOR.value:
            if not np.isnan(close) and not np.isnan(cwvap) and close > cwvap:
                st.price_above_cwvap = True

        updated_state_val = st.to_int()

        if tag == EntryTag.SLOPE_BOTTOM.value:
            exit_status = exit_slope_bottom(row, prev_row, trade, peak_close, bars_held, updated_state_val, cfg, records, idx)
        elif tag == EntryTag.ACCEL.value:
            exit_status = check_exit_accel_cross(records, idx, trade, updated_state_val, cfg.accel_cross, cfg.cwvap_guard, cfg)
        elif tag == EntryTag.INSTITUTIONAL_FLOOR.value:
            exit_status = exit_institutional_floor(row, prev_row, trade, peak_close, bars_held, updated_state_val, cfg, records, idx)
        elif tag == EntryTag.RANGE_REVERSION.value:
            exit_status = exit_range_reversion(row, prev_row, trade, peak_close, bars_held, updated_state_val, cfg, records, idx)
        elif tag == EntryTag.ACCEL_ZERO_CROSS.value:
            exit_status = exit_accel_zero_cross(row, prev_row, trade, peak_close, bars_held, updated_state_val, cfg.accel_zero_cross, records, idx)
        elif tag == EntryTag.PRT_SLOPE_ZERO_CROSS.value:
            exit_status = exit_prt_slope_zero_cross(row, prev_row, trade, peak_close, bars_held, updated_state_val, cfg.prt_slope_zero_cross, records, idx)
        elif tag == EntryTag.FAS_ZERO_CROSS.value:
            exit_status = exit_fas_zero_cross(row, prev_row, trade, peak_close, bars_held, updated_state_val, cfg.fas_zero_cross, records, idx)
        else:
            exit_status = (None, updated_state_val)

        res, state_returned = exit_status

        if cfg.st_exit_enabled and res is None:
            st_val, cts_val = row.get("cts_sell_threshold", np.nan), row.get("cts", np.nan)
            prev_st_val, prev_cts_val = prev_row.get("cts_sell_threshold", np.nan), prev_row.get("cts", np.nan)
            if all(not np.isnan(x) for x in [st_val, prev_st_val, cts_val, prev_cts_val]):
                if cts_val < 1.0 and st_val < 1.0 and prev_cts_val >= prev_st_val and cts_val < st_val:
                    res = ExitReason.ST_CROSS

        # Mandatory Exit bypass for bespoke paths
        bespoke_tags = [EntryTag.SLOPE_BOTTOM, EntryTag.ACCEL, EntryTag.INSTITUTIONAL_FLOOR, EntryTag.RANGE_REVERSION, EntryTag.ACCEL_ZERO_CROSS, EntryTag.PRT_SLOPE_ZERO_CROSS]
        is_bespoke = any(tag == t.value for t in bespoke_tags)
        
        if res == ExitReason.BAR3_STOP or is_bespoke:
            final_state = state_returned
        else:
            res, final_state = apply_cwvap_guard(row, trade, res, state_returned, cfg, records, idx)

        if res is not None:
            self._last_exit_idx, self._last_exit_reason = idx, res

        return res, final_state
