"""SavgolCTS signal orchestrator.

Dispatches entry checks to the Universal Master Path and exit
checks to path-specific exit modules. Cross-path concerns (cooldown,
ST exit, CWVAP guard) live here.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import BaseEntryConfig, BaseExitConfig, SignalInterface, Trade
from src.trading.signals.enums import EntryTag, ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig

# Entry path checkers
from src.trading.signals.savgol_cts.entries.cdvl_cts import entry_cdvl_cts
from src.trading.signals.savgol_cts.entries.universal_cross import entry_universal_cross
from src.trading.signals.savgol_cts.entries.trend_pullback import entry_trend_pullback
from src.trading.signals.savgol_cts.entries.flow_momentum import entry_flow_momentum
from src.trading.signals.savgol_cts.entries.coherent_pullback import entry_coherent_pullback
from src.trading.signals.savgol_cts.entries.anchor_shock_pullback import entry_anchor_shock_pullback
from src.trading.signals.savgol_cts.entries.springboard import entry_springboard
from src.trading.signals.savgol_cts.entries.oversold_decel import entry_oversold_decel
from src.trading.signals.savgol_cts.entries.custom_bayesian import entry_custom_bayesian

# Exit path checkers
from src.trading.signals.savgol_cts.exits.cwvap_guard import apply_cwvap_guard
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross
from src.trading.signals.savgol_cts.exits.anchor_shock_pullback import exit_anchor_shock_pullback
from src.trading.signals.savgol_cts.exits.springboard import exit_springboard



class SavgolCTSSignal(SignalInterface):
    """CTS -1/+1 mean-reversion signal with Universal ML Master Path."""

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

        # Path Custom Bayesian (Exclusively routed if enabled for optimized symbols)
        if getattr(cfg, "custom_bayesian", None) and cfg.custom_bayesian.enabled:
            passed, intensity, meta = entry_custom_bayesian(row, prev_row, cfg, records, idx)
            if passed:
                return True, intensity, meta
            else:
                return False, 0, {"reason": f"CustomBayesian: {meta.get('reason', 'Rejected')}"}

        # Path 0: CDVL-CTS Path (Priority High Performance Flow-Momentum)
        cdvl_reason = "CDVL_CTS path disabled"
        if getattr(cfg, "cdvl_cts", None) and cfg.cdvl_cts.enabled:
            passed, intensity, meta = entry_cdvl_cts(row, prev_row, cfg, records, idx)
            if passed:
                return True, intensity, meta
            cdvl_reason = f"CDVL_CTS: {meta.get('reason', 'Rejected')}"

        uc_reason = "Universal path disabled"
        # Path 0: Universal ML Master Path (The primary funnel)
        if getattr(cfg, "universal_cross", None) and cfg.universal_cross.enabled:
            passed, intensity, meta = entry_universal_cross(row, prev_row, cfg, records, idx)
            if passed:
                # Augment reason for UI visibility
                raw = meta.get("raw_ml_score", 0)
                thr = meta.get("ml_guard_threshold", 0)
                meta["reason"] = f"{meta.get('reason')} | RawScore: {raw:.4f} | Threshold: {thr:.2f}"
                return True, intensity, meta
            
            # Augment rejection reason for UI visibility
            raw = meta.get("raw_ml_score")
            thr = meta.get("ml_guard_threshold")
            reason = meta.get("reason", "Rejected")
            if raw is not None:
                reason = f"{reason} | Original ML Score: {raw:.4f} | Threshold: {thr:.2f}"
            uc_reason = f"UniversalCross: {reason}"

        # Path 1: Secular Trend Pullback Path (Independent path)
        if getattr(cfg, "trend_pullback_enabled", True):
            passed, intensity, meta = entry_trend_pullback(row, prev_row, cfg, records, idx)
            if passed:
                return True, intensity, meta
            tp_reason = f"TrendPullback: {meta.get('reason', 'Rejected')}"
        else:
            tp_reason = "TrendPullback path disabled"

        # Path 2: Flow Momentum Path (Highly optimized study-based setup)
        fm_reason = "FlowMomentum path disabled"
        if getattr(cfg, "flow_momentum", None) and cfg.flow_momentum.enabled:
            passed, intensity, meta = entry_flow_momentum(row, prev_row, cfg, records, idx)
            if passed:
                return True, intensity, meta
            fm_reason = f"FlowMomentum: {meta.get('reason', 'Rejected')}"

        # Path 3: Coherent Pullback Path (Early Inflections)
        cp_reason = "CoherentPullback path disabled"
        if getattr(cfg, "coherent_pullback", None) and cfg.coherent_pullback.enabled:
            passed, intensity, meta = entry_coherent_pullback(row, prev_row, cfg, records, idx)
            if passed:
                return True, intensity, meta
            cp_reason = f"CoherentPullback: {meta.get('reason', 'Rejected')}"

        # Path 4: Anchor Shock Pullback Path (Advanced Price-Volume Swing Support)
        asp_reason = "AnchorShockPullback path disabled"
        if getattr(cfg, "anchor_shock_pullback", None) and cfg.anchor_shock_pullback.enabled:
            passed, intensity, meta = entry_anchor_shock_pullback(row, prev_row, cfg, records, idx)
            if passed:
                return True, intensity, meta
            asp_reason = f"AnchorShockPullback: {meta.get('reason', 'Rejected')}"

        # Path 5: SpringBoard Path (Bottom Inflections)
        sb_reason = "SpringBoard path disabled"
        if getattr(cfg, "springboard", None) and cfg.springboard.enabled:
            passed, intensity, meta = entry_springboard(row, prev_row, cfg, records, idx)
            if passed:
                return True, intensity, meta
            sb_reason = f"SpringBoard: {meta.get('reason', 'Rejected')}"

        # Path 6: Oversold Deceleration Path (ODP)
        odp_reason = "OversoldDecel path disabled"
        if getattr(cfg, "oversold_decel", None) and cfg.oversold_decel.enabled:
            passed, intensity, meta = entry_oversold_decel(row, prev_row, cfg, records, idx)
            if passed:
                return True, intensity, meta
            odp_reason = f"OversoldDecel: {meta.get('reason', 'Rejected')}"

        return False, 0, {"reason": f"{cdvl_reason} | {uc_reason} | {tp_reason} | {fm_reason} | {cp_reason} | {asp_reason} | {sb_reason} | {odp_reason}"}

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

        if not np.isnan(close) and not np.isnan(cwvap) and close > cwvap:
            st.price_above_cwvap = True

        # Resolve the UniversalCrossExitConfig to use
        uc_cfg = cfg.universal_cross
        sb_exit_cfg = getattr(cfg, "springboard", None)

        if tag == EntryTag.SPRINGBOARD.value and sb_exit_cfg and sb_exit_cfg.enabled:
            # Route SpringBoard exits through its own dedicated path-specific exit module
            exit_reason, state_val = exit_springboard(
                row, prev_row, trade, peak_close, bars_held, st.to_int(),
                sb_exit_cfg, records, idx
            )
        else:
            # All other SavgolCTS paths route through the Universal Cross exit logic
            # (Standardized Pure CTS Trailing + Hard Stop)
            exit_reason, state_val = exit_universal_cross(
                row, prev_row, trade, peak_close, bars_held, st.to_int(),
                uc_cfg, records, idx
            )
        st = SavgolCTSExitState.from_int(state_val)

        # Apply path-specific overrides for CDVL_CTS trades
        if tag == EntryTag.CDVL_CTS.value:
            # 1. Catastrophic Hard Stop Shield at exactly 10.0%
            pnl_pct = (close / trade.entry_price - 1.0) * 100.0
            if pnl_pct <= -10.0:
                exit_reason = ExitReason.HARD_STOP
            elif exit_reason:
                # 2. Dynamic CDVL Exit Trailing suppression: defer indicator exit if CDVL is positive
                cdvl = row.get("cdvl", np.nan)
                if not np.isnan(cdvl) and cdvl > 0.0:
                    exit_reason = None

        # Apply path-specific fallbacks for Anchor-Shock-Pullback trades
        if not exit_reason and tag == EntryTag.ANCHOR_SHOCK_PULLBACK.value:
            asp_exit_cfg = getattr(cfg, "anchor_shock_pullback", None)
            if asp_exit_cfg and asp_exit_cfg.enabled:
                # 1. Hard Stop Capping
                if asp_exit_cfg.hard_stop_enabled:
                    pnl_pct = (close / trade.entry_price - 1.0) * 100.0
                    if pnl_pct <= -asp_exit_cfg.hard_stop_pct:
                        exit_reason = ExitReason.HARD_STOP

                # 2. Time Decay Limit
                if not exit_reason and asp_exit_cfg.time_decay_enabled:
                    if bars_held >= asp_exit_cfg.max_hold_bars:
                        exit_reason = ExitReason.TIME_DECAY

        # Apply path-specific overrides for Oversold-Decel trades
        if not exit_reason and tag == EntryTag.OVERSOLD_DECEL.value:
            pnl_pct = (close / trade.entry_price - 1.0) * 100.0
            if pnl_pct <= -10.0:
                exit_reason = ExitReason.HARD_STOP


        # Apply common CWVAP guard logic (can suppress or trigger exits)
        final_reason, st_val = apply_cwvap_guard(
            row, trade, exit_reason, st.to_int(),
            cfg, records, idx, tag
        )

        if final_reason:
            self._last_exit_idx = idx
            self._last_exit_reason = final_reason
            return str(final_reason), st_val

        return None, st_val

    def get_default_entry_config(self) -> BaseEntryConfig:
        from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
        return SavgolCTSEntryConfig()

    def get_default_exit_config(self) -> BaseExitConfig:
        from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
        return SavgolCTSExitConfig()

