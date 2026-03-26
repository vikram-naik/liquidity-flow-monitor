from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.trading.signals.base import BaseEntryConfig, BaseExitConfig, SignalInterface, Trade
from src.trading.signals.enums import EntryTag, ExitReason


@dataclass
class SavgolCTSEntryConfig(BaseEntryConfig):
    """Configuration for CTS -1/+1 mean-reversion entry signal.

    Three entry paths evaluated in priority order:
    1. CTS-floor-leave: CTS rises above -1.0 after being pinned there
       (prev_cts <= -0.98, cts > -0.98). No BT constraint. Fires on the lift
       bar, not the touch bar — confirms floor held before entry.
    2. PSZ bend: PSZ velocity quiescent base + upward turn (structural PSZ
       recovery, independent of CTS level).
    3. BT-cross: CTS crosses above BT from below while still oversold
       (V-bottom recovery, bt_max gate filters shallow entries).
    """
    cts_floor: float = -1.0
    # Floor touch entry: tolerance around -1.0 for CTS and BT to be "at floor".
    # Empirically (NIFTY 500): 64.8% WR, +2.50% avg, >10% winner rate 30%.
    floor_touch_tolerance: float = 0.02
    # PSZ raw crossover: signal fires when psz_raw crosses above this level
    # while CTS and BT are pinned at floor. Replaces psz_v/bend gates.
    psz_cross_threshold: float = -0.25
    # PSZ bend quality gate: require PSZ velocity to have been quiescent
    # (flat base) before crossover, filtering out V-bounce "bumps".
    psz_bend_enabled: bool = False
    psz_bend_lookback: int = 8
    psz_bend_quiescence_threshold: float = 0.05
    psz_bend_quiescence_min_bars: int = 5
    psz_bend_v_floor: float = -0.05
    psz_bend_v_min_at_signal: float = 0.02
    psz_bend_cts_oversold_threshold: float = -0.50
    # Mean psz_v gate: if the average psz_v over the lookback is above this,
    # PSZ was already drifting upward (not a fresh base) — empirically (NIFTY 50
    # 2024+) mean_v >= 0.010 drops WR below 50% and avg PnL near zero.
    psz_bend_mean_v_max: float = 0.010
    # BT crossover complement: CTS crosses BT from below in oversold zone.
    # Catches V-bottoms where CTS hits floor but BT hasn't caught up.
    bt_cross_enabled: bool = True
    bt_cross_oversold_threshold: float = -0.50
    # BT depth gate: removed (2026-03-23). Restudy on NIFTY 500 showed trades with
    # bt > -0.71 are net positive (+0.91 avg, 1.39 payoff). The oversold_threshold
    # at -0.50 already caps BT-cross to the oversold zone.
    # Study: scripts/study_bt_cross_depth_gate.py
    # BT-cross flat psz_v gate: reject BT-cross when psz_v has been flat
    # (|psz_v| < threshold) for N consecutive bars before signal.
    # Study: scripts/study_pszv_flat_lookback.py — flat BT-cross: -1.28% avg, 0.80x payoff.
    bt_cross_flat_gate_enabled: bool = True
    bt_cross_flat_gate_threshold: float = 0.02
    bt_cross_flat_gate_lookback: int = 3
    psz_min_threshold: float = -0.25
    floor_leave_cwvap_max_dist: float = -5.0
    # Path 0 (Floor Touch): enter while CTS and BT are both pinned at floor.
    floor_touch_enabled: bool = True
    floor_touch_psz_max: float = -0.25  # PSZ must be below this (deeply oversold)
    floor_touch_tolerance: float = 0.02
    # Path 4 (PSZv flat): PSZv in (0, 0.04) and PSZ <= -0.3 for last 3 bars.
    pszv_flat_enabled: bool = False
    pszv_flat_v_min: float = 0.0
    pszv_flat_v_max: float = 0.04
    pszv_flat_psz_max: float = -0.3
    pszv_flat_lookback: int = 3
    # Cooldown period: prevent entry within N bars after specific exit reasons.
    cooldown_enabled: bool = True
    cooldown_bars: int = 10
    cooldown_exit_reasons: tuple[ExitReason, ...] = (
        ExitReason.SUPPRESSED_EXIT, 
        ExitReason.FLOOR_HIT,
        ExitReason.BT_HIT
    )
    # Vertical Jump Guard: Reject if CTS jumps too far from floor (Cliff is at -0.6).
    floor_leave_cts_max: float = -0.6
    # Conviction Gate: Required absolute PSZV for standard entries.
    floor_leave_pszv_min: float = 0.05
    # Signal-Day Trap Floor: Hard rejection for anything deeper than this.
    floor_leave_cwvap_trap_hi: float = -5.0
    cts_direction_gate_enabled: bool = True
    

@dataclass
class SavgolCTSExitConfig(BaseExitConfig):
    """Configuration for CTS -1/+1 mean-reversion exit signal.

    Exit triggers when:
    - prev_cts >= 0.98 and cts < 0.98 (ceiling-leave exit, mirrors floor-leave entry), OR
    - CTS drops back to buy_threshold or -1.0 (floor exit).
    """
    st_crossover_tolerance: float = 0.03
    floor_tolerance: float = 0.10
    # Ceiling-leave tolerance: exit fires when CTS drops below (1.0 - tolerance)
    # after having been at or above it. Mirrors floor_touch_tolerance = 0.02.
    ceiling_leave_tolerance: float = 0.02
    # Sell Threshold (ST) Exit enabled: applies to all entry types
    st_exit_enabled: bool = False
    # PSZ stall early exit: at bar N after entry, if psz_v is non-positive
    # the momentum has not materialised — exit.
    psz_stall_enabled: bool = False
    psz_stall_check_bar: int = 2
    # BT-cross PSZ glide exit: hold until PSZ drops below this after having been above it.
    # Only applies to BT-cross entries. Safety nets: hit_bt / hit_floor still active.
    bt_cross_psz_glide_threshold: float = 0.30
    # CWVAP Price Guard Tolerance: allowable percentage dip below CWVAP while suppressed
    # 0.0 disables this feature (strict baseline).
    cwvap_tolerance_pct: float = 1.00
    # CWVAP Price Guard Time Stop: max bars to hold while below CWVAP within tolerance
    cwvap_tolerance_bars: int = 3
    # Grace period (bars) before structural exits are allowed.
    # Prevents "one-day trades" on minor post-entry fluctuations.
    bt_hit_min_bars: int = 5
    floor_hit_min_bars: int = 5


@dataclass
class SavgolCTSExitState:
    """Helper to manage the delivery_bad_count bitfield state."""
    cts_rose: bool = False
    psz_was_above: bool = False
    cts_above_bt: bool = False
    exit_suppressed: bool = False
    suppressed_this_bar: bool = False

    @classmethod
    def from_int(cls, val: int) -> SavgolCTSExitState:
        return cls(
            cts_rose=bool(val & 1),
            psz_was_above=bool((val >> 1) & 1),
            cts_above_bt=bool((val >> 2) & 1),
            exit_suppressed=bool((val >> 3) & 1),
            suppressed_this_bar=bool((val >> 4) & 1)
        )

    def to_int(self) -> int:
        return (
            (int(self.suppressed_this_bar) << 4) |
            (int(self.exit_suppressed) << 3) |
            (int(self.cts_above_bt) << 2) |
            (int(self.psz_was_above) << 1) |
            int(self.cts_rose)
        )


class SavgolCTSSignal(SignalInterface):
    """CTS -1/+1 mean-reversion signal with three entry paths (priority order).

    Path 1 (CTS-floor-leave): CTS rises above -1.0 after being pinned there.
        prev_cts <= -0.98, cts > -0.98. No BT constraint. Fires on lift bar,
        not touch bar — confirms floor held. Ceiling exits dominate.
    Path 2 (PSZ bend):        PSZ velocity quiescent base + upward turn.
        Fires independent of CTS level.
    Path 3 (BT-cross):        CTS crosses BT from below in oversold zone.
        BT depth gate (bt_max=-0.71) filters shallow entries.
    Path 4 (PSZv flat):        PSZv flat (0,0.04) and last 3 bars of PSZ <= -0.3

    Exit:  CTS drops to buy_threshold OR -1.0 (after having risen above -1).
           Floor-leave trades skip sell threshold — ceiling and BT exits only.

    The ``delivery_bad_count`` parameter from the base interface is repurposed
    as a bitfield tracking per-trade state (see SIGNAL_FLOW.md).
    """

    def __init__(self):
        super().__init__()
        # Cross-trade state for cooldowns
        self._last_exit_idx: int = -1
        self._last_exit_reason: ExitReason | str = ""
        # Import TrendDirection here to avoid circular imports if any
        from src.divergence_engine.analysis.models import TrendDirection
        self._TrendDirection = TrendDirection

    def _compute_intensity(
        self, cts: float, coh: float, pdd: float, regime: str, tag: EntryTag,
        extra_parts: list[str] | None = None,
    ) -> tuple[int, dict]:
        """Shared intensity scoring and reason string for both entry paths."""
        intensity = 60.0

        # Coherence bonus: lower = more divergence = better setup (0–15)
        if not np.isnan(coh):
            coh_bonus = max(0.0, (0.5 - coh) / 0.5) * 15.0
            intensity += coh_bonus

        # PDD bonus: closer to zero = less exhaustion = better quality (0–15)
        if not np.isnan(pdd):
            pdd_bonus = max(0.0, (pdd + 10.0) / 10.0) * 15.0
            intensity += pdd_bonus

        # Regime bonus: downtrend/notrend preferred for mean-reversion (0–10)
        if regime == "downtrend":
            intensity += 10.0
        elif regime == "notrend":
            intensity += 5.0

        intensity = min(100.0, max(0.0, float(np.nan_to_num(intensity))))
        intensity_int = int(round(intensity))

        # Build reason string
        parts = [f"CTS={cts:.2f}"]
        if not np.isnan(coh):
            parts.append(f"coh={coh:.2f}")
        if not np.isnan(pdd):
            parts.append(f"pdd={pdd:.1f}")
        parts.append(regime)
        if extra_parts:
            parts.extend(extra_parts)

        if intensity >= 80:
            reason = f"SavgolCTS {tag.value}: STRONG [{', '.join(parts)}]"
        elif intensity >= 65:
            reason = f"SavgolCTS {tag.value}: good [{', '.join(parts)}]"
        else:
            reason = f"SavgolCTS {tag.value}: [{', '.join(parts)}]"

        return intensity_int, {"reason": reason, "entry_tag": tag}

    def _is_psz_bending(
        self, records: list[dict] | None, idx: int, cfg: SavgolCTSEntryConfig,
    ) -> bool:
        """Check whether PSZ formed a flat base (bend) before crossover.

        A bend-up requires four conditions:
        1. A quiescent base: PSZ velocity near zero for several bars while
           PSZ is in the oversold zone (at or below psz_cross_threshold).
        2. No plunge: psz_v never dropped below v_floor in the lookback
           (rules out V-bounce "bumps").
        3. Upward turn: at the signal bar, psz_v must be positive and
           greater than the previous bar's psz_v (velocity increasing —
           PSZ is accelerating upward, not just flat or still falling).
        4. Fresh base: mean psz_v over the lookback must be below
           psz_bend_mean_v_max (0.010) — if PSZ was already slowly drifting
           upward the move is stale, not a fresh breakout from a flat base.

        Returns True when all conditions are met (or gate is disabled).
        """
        if not cfg.psz_bend_enabled or records is None:
            return False

        lookback = cfg.psz_bend_lookback
        start = max(0, idx - lookback)
        if start >= idx:
            return False  # not enough history block

        # Condition 3: upward turn at signal bar while PSZ is still negative
        # (if PSZ is already positive we're entering late — the move has run)
        psz_raw_now = records[idx].get("price_slope_z", np.nan)
        psz_v_now = records[idx].get("psz_v", np.nan)
        psz_v_prev = records[idx - 1].get("psz_v", np.nan) if idx > 0 else np.nan
        if np.isnan(psz_v_now) or np.isnan(psz_v_prev) or np.isnan(psz_raw_now):
            return False
        if psz_raw_now >= 0:
            return False
        # psz_v must be positive and greater than the previous bar's psz_v
        if psz_v_now < cfg.psz_bend_v_min_at_signal or psz_v_now <= psz_v_prev:
            return False

        # Conditions 1, 2 & 4: quiescent base + no plunge + fresh base mean
        quiescent_count = 0
        v_floor_ok = True
        lb_v_vals = []
        for i in range(start, idx):
            psz_v = records[i].get("psz_v", np.nan)
            psz_raw = records[i].get("price_slope_z", np.nan)
            if np.isnan(psz_v):
                continue
            lb_v_vals.append(psz_v)
            if abs(psz_v) < cfg.psz_bend_quiescence_threshold and not np.isnan(psz_raw) and psz_raw <= cfg.psz_cross_threshold:
                quiescent_count += 1
            if psz_v < cfg.psz_bend_v_floor:
                v_floor_ok = False

        if not (quiescent_count >= cfg.psz_bend_quiescence_min_bars and v_floor_ok):
            return False

        # Condition 4: mean psz_v must be below threshold — rejects slow drifters
        if lb_v_vals:
            mean_v = sum(lb_v_vals) / len(lb_v_vals)
            if mean_v >= cfg.psz_bend_mean_v_max:
                return False

        return True

    def _is_psz_stalled(
        self, records: list[dict] | None, trade: Trade | None,
        idx: int, bars_held: int, cfg: SavgolCTSExitConfig,
    ) -> tuple[bool, str]:
        """Check whether PSZ momentum has stalled at the onset of a trade.

        At exactly bar N (psz_stall_check_bar) after entry, if psz_v is
        non-positive the move has failed to follow through.  Empirically
        (NIFTY 500), trades with psz_v <= 0 at bar 2 have 42% win rate
        and -2.2% mean PnL — clear early kills.

        Returns (is_stalled, reason_string).
        """
        if not cfg.psz_stall_enabled or records is None or trade is None or bars_held != cfg.psz_stall_check_bar:
            return False, ""

        psz_v = records[idx].get("psz_v", np.nan)
        if np.isnan(psz_v):
            return False, ""

        if psz_v <= 0:
            return True, ExitReason.PSZ_STALL
        return False, None

    def _check_floor_leave(
        self, row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
    ) -> tuple[bool, int, dict]:
        """Path 1: CTS-floor-leave — CTS rising above floor after being pinned.

        Fires on the first bar CTS clears the floor zone (cts > -0.98) after
        having been at or below it (prev_cts <= -0.98). No BT constraint —
        BT-cross handles non-floor crossovers independently.
        Empirically (NIFTY 500): 63.9% WR, +2.52% avg, 73.1% ceiling exits.
        """
        cts      = row.get("cts", np.nan)
        prev_cts = prev_row.get("cts", np.nan)
        if np.isnan(cts) or np.isnan(prev_cts):
            return False, 0, {"reason": "Missing CTS data"}

        psz_raw = row.get("price_slope_z", np.nan)
        if not np.isnan(psz_raw) and (psz_raw >= 0 or psz_raw >= cfg.psz_min_threshold):
            return False, 0, {"reason": f"PSZ: ({psz_raw:.3f}), late entry"}

        floor = cfg.cts_floor + cfg.floor_touch_tolerance
        if not (prev_cts <= floor and cts > floor):
            return False, 0, {"reason": "CTS not leaving floor"}

        # 1. Data Validation Gate
        cwvap = row.get("cwvap", np.nan)
        close = row.get("close", np.nan)
        psz_v = row.get("psz_v", np.nan)
        if np.isnan(cwvap) or np.isnan(close) or np.isnan(psz_v) or cwvap <= 0:
            return False, 0, {"reason": "Insufficient data"}

        # 2. Signal-Day Trap Floor: Hard rejection for entries deeper than -5% 
        dist_pct = ((close - cwvap) / cwvap) * 100.0
        if dist_pct <= cfg.floor_leave_cwvap_trap_hi:
            return False, 0, {"reason": f"Signal-Day Trap: {dist_pct:.1f}%"}

        # 3. Vertical Jump Ceiling: Reject spikes already in exhaustion cliff
        if cts > cfg.floor_leave_cts_max:
            return False, 0, {"reason": f"Vertical Jump Ceiling: CTS {cts:.3f}"}

        # 4. Conviction Gate: Reject "Dead Momentum" signals (ABS(PSZV) < 0.05)
        if abs(psz_v) < cfg.floor_leave_pszv_min:
            return False, 0, {"reason": f"Dead Momentum: PSZV {psz_v:.4f}"}

        return True, 0, {"entry_tag": EntryTag.CTS_FLOOR_LEAVE}

        coh    = row.get("coherence", np.nan)
        pdd    = row.get("pdd_120", np.nan)
        regime = row.get("regime", "")
        intensity_int, meta = self._compute_intensity(
            cts, coh, pdd, regime, EntryTag.CTS_FLOOR_LEAVE,
            [f"prev_cts={prev_cts:.3f}"],
        )
        return True, intensity_int, meta

    def _check_floor_touch(
        self, row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
    ) -> tuple[bool, int, dict]:
        """Path 0: Floor Touch — enter while CTS and BT are both pinned at floor.

        Fires when both CTS and BT are at or below -0.98 and PSZ is deeply
        oversold (psz < -0.25). Captures entries earlier than Floor-Leave.
        """
        if not cfg.floor_touch_enabled:
            return False, 0, {"reason": "Floor Touch disabled"}

        cts = row.get("cts", np.nan)
        bt = row.get("cts_buy_threshold", np.nan)
        if np.isnan(cts) or np.isnan(bt):
            return False, 0, {"reason": "Missing CTS/BT data"}

        floor = cfg.cts_floor + cfg.floor_touch_tolerance
        if not (cts <= floor and bt <= floor):
            return False, 0, {"reason": f"CTS/BT not pinned: CTS={cts:.3f}, BT={bt:.3f}"}

        psz_raw = row.get("price_slope_z", np.nan)
        if np.isnan(psz_raw) or psz_raw >= cfg.floor_touch_psz_max:
            return False, 0, {"reason": f"PSZ [{psz_raw:.3f}] >= {cfg.floor_touch_psz_max}"}

        # Reuse CWVAP distance guard from Floor-Leave
        if cfg.floor_leave_cwvap_max_dist < 0.0:
            cwvap = row.get("cwvap", np.nan)
            close = row.get("close", np.nan)
            if not np.isnan(cwvap) and not np.isnan(close) and cwvap > 0:
                cwvap_dist_pct = (close - cwvap) / cwvap * 100.0
                if cwvap_dist_pct < cfg.floor_leave_cwvap_max_dist:
                    return False, 0, {"reason": f"CWVAP dist {cwvap_dist_pct:.1f}% < {cfg.floor_leave_cwvap_max_dist:.1f}%"}

        coh    = row.get("coherence", np.nan)
        pdd    = row.get("pdd_120", np.nan)
        regime = row.get("regime", "")
        intensity_int, meta = self._compute_intensity(
            cts, coh, pdd, regime, EntryTag.CTS_FLOOR_TOUCH,
            [f"bt={bt:.3f}"],
        )
        return True, intensity_int, meta

    def _check_psz_bend(
        self, row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
        records: list[dict] | None = None, idx: int = 0,
    ) -> tuple[bool, int, dict]:
        """Path 1: PSZ bend entry — independent of CTS level.

        Fires when PSZ velocity has been quiescent (flat base) near the
        oversold zone and PSZ is now turning up.
        """
        if not cfg.psz_bend_enabled:
            return False, 0, {"reason": "PSZ bend disabled"}
        psz_raw = row.get("price_slope_z", np.nan)
        psz_prev = prev_row.get("price_slope_z", np.nan)
        if np.isnan(psz_raw) or np.isnan(psz_prev):
            return False, 0, {"reason": "Missing PSZ data"}

        # Check CTS is in the oversold zone

        cts = row.get("cts", np.nan)
        if not np.isnan(cts) and cts > cfg.psz_bend_cts_oversold_threshold:
            return False, 0, {"reason": f"CTS [{cts:.3f} > {cfg.psz_bend_cts_oversold_threshold:.3f}] not in oversold zone"}

        if not self._is_psz_bending(records, idx, cfg):
            return False, 0, {"reason": "PSZ not bending (psz_v not quiescent)"}

        cts = row.get("cts", np.nan)
        coh = row.get("coherence", np.nan)
        pdd = row.get("pdd_120", np.nan)
        regime = row.get("regime", "")
        intensity_int, meta = self._compute_intensity(
            cts, coh, pdd, regime, EntryTag.PSZ,
            [f"psz={psz_raw:.3f}", f"prev_psz={psz_prev:.3f}"],
        )
        return True, intensity_int, meta

    def _check_bt_crossover(
        self, row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
        records: list[dict] | None = None, idx: int = 0,
    ) -> tuple[bool, int, dict]:
        """Path 3: CTS crosses BT from below while in oversold territory."""
        if not cfg.bt_cross_enabled:
            return False, 0, {"reason": "BT cross disabled"}

        cts = row.get("cts", np.nan)
        bt = row.get("cts_buy_threshold", np.nan)
        prev_cts = prev_row.get("cts", np.nan)
        prev_bt = prev_row.get("cts_buy_threshold", np.nan)

        if np.isnan(prev_cts) or np.isnan(prev_bt) or np.isnan(bt):
            return False, 0, {"reason": "Missing CTS/BT data"}

        # Must cross BT from below
        if not (prev_cts <= prev_bt and cts > bt):
            return False, 0, {"reason": "No BT crossover"}

        # BT must be above floor — unless prev_bt was also at floor.
        # If BT was already pinned at -1.0 (both CTS and BT at floor together),
        # CTS leaving first IS a genuine recovery signal.
        # Only block when BT just dropped to floor (prev_bt was above floor).
        if bt <= cfg.cts_floor and prev_bt > cfg.cts_floor:
            return False, 0, {"reason": f"BT at floor ({bt:.3f}), not a genuine crossover"}

        # Must still be in oversold territory
        if cts > cfg.bt_cross_oversold_threshold:
            return False, 0, {"reason": f"CTS {cts:.3f} above oversold threshold {cfg.bt_cross_oversold_threshold}"}

        # PSZ must still be negative — if already positive we're entering late
        psz_raw = row.get("price_slope_z", np.nan)
        if not np.isnan(psz_raw) and (psz_raw >= 0 or psz_raw >= cfg.psz_min_threshold):
            return False, 0, {"reason": f"PSZ: ({psz_raw:.3f}), late entry"}

        # BT depth gate: removed — oversold_threshold already caps BT-cross zone

        # Flat psz_v gate: reject when psz_v has been quiescent (straight-line fall, no bend)
        if cfg.bt_cross_flat_gate_enabled and records and idx >= cfg.bt_cross_flat_gate_lookback:
            all_flat = True
            for j in range(idx - cfg.bt_cross_flat_gate_lookback, idx):
                v = records[j].get("psz_v", np.nan)
                if np.isnan(v) or abs(v) >= cfg.bt_cross_flat_gate_threshold:
                    all_flat = False
                    break
            if all_flat:
                return False, 0, {"reason": f"BT-cross flat psz_v gate ({cfg.bt_cross_flat_gate_lookback} bars)"}

        coh = row.get("coherence", np.nan)
        pdd = row.get("pdd_120", np.nan)
        regime = row.get("regime", "")
        intensity_int, meta = self._compute_intensity(
            cts, coh, pdd, regime, EntryTag.BT_CROSS,
            [f"bt={bt:.3f}", f"prev_cts={prev_cts:.3f}"],
        )
        return True, intensity_int, meta

    def _check_pszv_flat(
        self, row: dict, cfg: SavgolCTSEntryConfig,
        records: list[dict] | None = None, idx: int = 0,
    ) -> tuple[bool, int, dict]:
        """Path 4: PSZv flat (0,0.01) and last 3 bars of PSZ <= -0.3.
        
        Fires when PSZ velocity is quiescent (flat) and PSZ has been pinned
        in the deep oversold zone for several bars.
        """
        if not cfg.pszv_flat_enabled:
            return False, 0, {"reason": "PSZv flat disabled"}

        psz_v = row.get("psz_v", np.nan)
        psz_raw = row.get("price_slope_z", np.nan)
        
        if np.isnan(psz_v) or np.isnan(psz_raw):
            return False, 0, {"reason": "Missing PSZv/PSZ data"}
            
        if not (cfg.pszv_flat_v_min <= psz_v <= cfg.pszv_flat_v_max and psz_raw <= cfg.pszv_flat_psz_max):
            return False, 0, {"reason": f"PSZv [{psz_v:.4f}] not flat or PSZ [{psz_raw:.3f}] > {cfg.pszv_flat_psz_max}"}

        if records is None or idx < cfg.pszv_flat_lookback:
            return False, 0, {"reason": "Insufficient history for PSZv flat check"}

        # Ensure cts is floored.
        cts = row.get("cts", np.nan)
        if np.isnan(cts) or cts > cfg.cts_floor:
            return False, 0, {"reason": f"PSZv Flat: CTS {cts:.3f} not at floor"}

        # Perform lookback check
        for i in range(1, cfg.pszv_flat_lookback + 1):
            psz_prev = records[idx-i].get("price_slope_z", np.nan)
            if np.isnan(psz_prev) or psz_prev > cfg.pszv_flat_psz_max:
                return False, 0, {"reason": f"PSZ at bar -{i} [{psz_prev:.3f}] > {cfg.pszv_flat_psz_max}"}

        cts = row.get("cts", np.nan)
        coh = row.get("coherence", np.nan)
        pdd = row.get("pdd_120", np.nan)
        regime = row.get("regime", "")
        
        intensity_int, meta = self._compute_intensity(
            cts, coh, pdd, regime, EntryTag.PSZV_FLAT,
            [f"psz={psz_raw:.3f}", f"v={psz_v:.4f}"],
        )
        return True, intensity_int, meta

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

        # Check cooldown period
        if cfg.cooldown_enabled and self._last_exit_idx != -1:
            bars_since_exit = idx - self._last_exit_idx
            if 0 <= bars_since_exit <= cfg.cooldown_bars:
                if any(r == self._last_exit_reason for r in cfg.cooldown_exit_reasons):
                    return False, 0, {"reason": f"Cooldown active ({bars_since_exit}/{cfg.cooldown_bars} bars after {self._last_exit_reason})", "cooldown": True}

        cts = row.get("cts", np.nan)
        if np.isnan(cts):
            return False, 0, {"reason": "Missing CTS data"}

        # CTS Direction Gate: Filter out Sideways/Falling entries for all paths
        if cfg.cts_direction_gate_enabled:
            direction = row.get("cts_direction")
            if direction in (self._TrendDirection.SIDEWAYS, self._TrendDirection.FALLING, self._TrendDirection.STEEP_FALLING):
                return False, 0, {"reason": f"CTS Direction Gate: {direction}", "direction": direction}

        # Path 0: Floor Touch — enter while CTS+BT pinned at floor
        passed, intensity, meta = self._check_floor_touch(row, prev_row, cfg)
        if passed:
            return True, intensity, meta

        # Path 1: CTS-floor-leave — CTS rising above floor after being pinned
        passed, intensity, meta = self._check_floor_leave(row, prev_row, cfg)
        if passed:
            return True, intensity, meta

        # Path 2: PSZ bend — quiescent base + upward PSZ turn
        passed, intensity, meta = self._check_psz_bend(row, prev_row, cfg, records, idx)
        if passed:
            return True, intensity, meta

        # Path 3: BT-cross — CTS crosses BT from below in oversold zone
        passed, intensity, meta = self._check_bt_crossover(row, prev_row, cfg, records, idx)
        if passed:
            return True, intensity, meta

        # Path 4: PSZv flat — flat base and deeply oversold
        passed, intensity, meta = self._check_pszv_flat(row, cfg, records, idx)
        if passed:
            return True, intensity, meta

        return False, 0, meta

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
        """Exit logic, branched by entry path."""
        if not isinstance(cfg, SavgolCTSExitConfig):
            cfg = SavgolCTSExitConfig()

        tag = trade.entry_tag if trade is not None else ""

        # Dispatch to specific indicator logic
        if tag in (EntryTag.CTS_FLOOR_LEAVE.value, EntryTag.CTS_BT_FLOOR.value, EntryTag.CTS_FLOOR_TOUCH.value):
            exit_status = self._exit_floor(
                row, prev_row, trade, peak_close, bars_held,
                delivery_bad_count, cfg, records, idx,
            )
            # If floor indicator allows, check PSZ glide
            if exit_status[0] is None:
                exit_status = self._exit_psz_glide(
                    row, prev_row, trade, exit_status[1], cfg, records, idx
                )
        elif tag == EntryTag.BT_CROSS.value:
            exit_status = self._exit_bt_cross(
                row, prev_row, trade, peak_close, bars_held,
                delivery_bad_count, cfg, records, idx,
            )
        else:
            exit_status = self._exit_psz(
                row, prev_row, trade, peak_close, bars_held,
                delivery_bad_count, cfg, records, idx,
            )

        # Refined Stateful Price Guard
        res, state_returned = exit_status

        # Universal Sell Threshold Exit Path (feature toggle)
        if cfg.st_exit_enabled and res is None:
            st_val = row.get("cts_sell_threshold", np.nan)
            prev_st_val = prev_row.get("cts_sell_threshold", np.nan)
            cts_val = row.get("cts", np.nan)
            prev_cts_val = prev_row.get("cts", np.nan)
            
            if not np.isnan(st_val) and not np.isnan(prev_st_val) and not np.isnan(cts_val) and not np.isnan(prev_cts_val):
                # Ensure we are not in the ceiling zone (cts < 1.0 and st < 1.0)
                if cts_val < 1.0 and st_val < 1.0:
                    if prev_cts_val >= prev_st_val and cts_val < st_val:
                        res = ExitReason.ST_CROSS

        # Update cross-trade cooldown state
        if res is not None:
            self._last_exit_idx = idx
            self._last_exit_reason = res

        st = SavgolCTSExitState.from_int(state_returned)
        # Clear per-bar flag from previous bar
        st.suppressed_this_bar = False
        
        # If an indicator proposed an exit, mark it as suppressed for memory
        if res is not None:
            st.exit_suppressed = True
            st.suppressed_this_bar = True

        close = row.get("close", np.nan)
        psz_raw = row.get("price_slope_z", np.nan)
        cts = row.get("cts", np.nan)

        if cwvap_values and not np.isnan(close):
            cwvap = cwvap_values[-1]
            if not np.isnan(cwvap):
                if close > cwvap:
                    # Rule A: Suppress exit while momentum positive above CWVAP.
                    # Extended glide: hold as long as PSZ or CTS is positive.
                    # Study: scripts/study_cwvap_extended_glide.py (NIFTY 500)
                    # Rule A: Extreme over-extension OR sustained extreme momentum
                    psz_strong = not np.isnan(psz_raw) and psz_raw > 0.00
                    cts_strong = not np.isnan(cts) and cts > 0.00
                    is_strong_momentum = psz_strong or cts_strong

                    if is_strong_momentum:
                        res, final_state = None, st.to_int()
                    
                    # Rule B: Release a previously suppressed exit now that momentum faded
                    elif res is not None or st.exit_suppressed:
                        # If we are releasing, clear hit_this_bar since it's now a REAL exit
                        st.suppressed_this_bar = False
                        res, final_state = (res if res else ExitReason.CWVAP_EXHAUSTION), st.to_int()
                    else:
                        st.suppressed_this_bar = False
                        res, final_state = None, st.to_int()
                
                # Below CWVAP
                elif st.exit_suppressed:
                    # Propagate "suppressed this bar" if we stay within tolerance
                    st.suppressed_this_bar = True
                    if cfg.cwvap_tolerance_pct > 0.0 and cfg.cwvap_tolerance_bars > 0:
                        dist_pct = (close - cwvap) / cwvap * 100.0
                        if dist_pct >= -cfg.cwvap_tolerance_pct:
                            # Check consecutive bars below CWVAP dynamically
                            bars_below = 1  # 1 for current bar
                            if records is not None and idx > 0:
                                for j in range(1, cfg.cwvap_tolerance_bars + 1):
                                    check_idx = idx - j
                                    if check_idx <= trade.entry_idx:
                                        break
                                    prev_close = records[check_idx].get("close", np.nan)
                                    prev_cwvap = records[check_idx].get("cwvap", np.nan)
                                    if not np.isnan(prev_close) and not np.isnan(prev_cwvap):
                                        if prev_close <= prev_cwvap:
                                            bars_below += 1
                                        else:
                                            break
                            
                            if bars_below > cfg.cwvap_tolerance_bars:
                                res, final_state = (res if res else f"CWVAP time stop ({cfg.cwvap_tolerance_bars} bars)"), st.to_int()
                            else:
                                res, final_state = None, st.to_int()  # Suppress and give chance
                        else:
                            # Dropped below tolerance
                            res, final_state = (res if res else ExitReason.SUPPRESSED_EXIT), st.to_int()
                    else:
                        # Baseline: no tolerance enabled
                        res, final_state = (res if res else ExitReason.SUPPRESSED_EXIT), st.to_int()
                else:
                    res, final_state = res, st.to_int()
            else:
                res, final_state = res, st.to_int()
        else:
            res, final_state = res, st.to_int()

        # Update cross-trade cooldown state with the FINAL decision
        if res is not None:
            self._last_exit_idx = idx
            self._last_exit_reason = res

        return res, final_state

    def _exit_floor(
        self,
        row: dict, prev_row: dict, trade: Trade,
        peak_close: float, bars_held: int, state_val: int,
        cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
    ) -> tuple[str | None, int]:
        """Exit logic for CTS-floor-leave entries (also handles legacy CTS-BT-floor tag)."""
        cts = row.get("cts", np.nan)
        bt = row.get("cts_buy_threshold", np.nan)
        psz_raw = row.get("price_slope_z", np.nan)

        st = SavgolCTSExitState.from_int(state_val)

        if np.isnan(cts):
            return None, st.to_int()

        if cts > -1.0:
            st.cts_rose = True
        if not np.isnan(bt) and cts > bt:
            st.cts_above_bt = True
        
        # Track if PSZ raw ever rose above glide threshold.
        if not np.isnan(psz_raw) and psz_raw >= cfg.bt_cross_psz_glide_threshold:
            st.psz_was_above = True

        # Safety: CTS hit -1.0 (after having risen)
        if st.cts_rose and cts <= -1.0:
            if bars_held >= cfg.floor_hit_min_bars:
                return ExitReason.FLOOR_HIT, st.to_int()

        # Floor-leave specific: Hit BT (mean reversion complete)
        if st.cts_above_bt and not np.isnan(bt) and cts <= bt:
            if bars_held >= cfg.bt_hit_min_bars:
                return ExitReason.BT_HIT, st.to_int()
        
        # Ceiling-leave exit: CTS drops below ceiling after having been there
        ceiling = 1.0 - cfg.ceiling_leave_tolerance
        prev_cts = prev_row.get("cts", np.nan)
        if not np.isnan(prev_cts) and prev_cts >= ceiling and cts < ceiling:
            return ExitReason.CEILING_HIT, st.to_int()

        return None, st.to_int()


    def _exit_psz(
        self,
        row: dict, prev_row: dict, trade: Trade,
        peak_close: float, bars_held: int, state_val: int,
        cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
    ) -> tuple[str | None, int]:
        """Core mean-reversion exit logic (shared by Path 2 and Path 4).

        PSZ glide exit: track psz_was_above, exit when PSZ drops below
        glide threshold (0.30) after having been above it.
        Study: scripts/study_psz_entry_glide_exit.py
        PSZ path:  64.0% WR, +2.24% avg, 12.3 bars
        PSZv-flat: 70.9% WR, +2.31% avg, 15.8 bars
        """
        cts = row.get("cts", np.nan)
        bt = row.get("cts_buy_threshold", np.nan)
        psz_raw = row.get("price_slope_z", np.nan)

        st = SavgolCTSExitState.from_int(state_val)

        if np.isnan(cts):
            return None, st.to_int()

        if cts > -1.0:
            st.cts_rose = True
        if not np.isnan(bt) and cts > bt:
            st.cts_above_bt = True

        # Track PSZ above glide threshold
        if not np.isnan(psz_raw) and psz_raw >= cfg.bt_cross_psz_glide_threshold:
            st.psz_was_above = True

        # Safety: CTS hit -1.0 (after having risen)
        if st.cts_rose and cts <= -1.0:
            if bars_held >= cfg.floor_hit_min_bars:
                return ExitReason.FLOOR_HIT, st.to_int()

        # Ceiling-leave exit: CTS drops below ceiling after having been there
        ceiling = 1.0 - cfg.ceiling_leave_tolerance
        prev_cts = prev_row.get("cts", np.nan)
        if not np.isnan(prev_cts) and prev_cts >= ceiling and cts < ceiling:
            return ExitReason.CEILING_HIT, st.to_int()

        # PSZ glide exit: once PSZ was above threshold, exit when it drops below
        if st.psz_was_above:
            res, _ = self._exit_psz_glide(row, prev_row, trade, st.to_int(), cfg, records, idx)
            if res:
                return res, st.to_int()

        return None, st.to_int()

    def _exit_bt_cross(
        self,
        row: dict, prev_row: dict, trade: Trade,
        peak_close: float, bars_held: int, state_val: int,
        cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
    ) -> tuple[str | None, int]:
        """BT-cross specific exit logic.

        Hold until PSZ raw drops below glide threshold after having been above it.
        Safety nets: hit_bt / hit_floor still active.
        """
        cts = row.get("cts", np.nan)
        psz_raw = row.get("price_slope_z", np.nan)

        st = SavgolCTSExitState.from_int(state_val)

        if np.isnan(cts):
            return None, st.to_int()

        if cts > -1.0:
            st.cts_rose = True

        # Track if PSZ raw ever rose above glide threshold.
        if not np.isnan(psz_raw) and psz_raw >= cfg.bt_cross_psz_glide_threshold:
            st.psz_was_above = True

        # PSZ stall early exit
        stalled, stall_reason = self._is_psz_stalled(records, trade, idx, bars_held, cfg)
        if stalled:
            return stall_reason, st.to_int()

        if not st.cts_rose:
            return None, st.to_int()

        # Safety: CTS hit -1.0
        if cts <= -1.0:
            if bars_held >= cfg.floor_hit_min_bars:
                return ExitReason.FLOOR_HIT, st.to_int()

        # Floor zone protection
        floor_zone = -1.0 + cfg.floor_tolerance
        bt = row.get("cts_buy_threshold", np.nan)
        if cts <= floor_zone and not np.isnan(bt) and bt <= floor_zone:
            return None, st.to_int()

        # Ceiling-leave exit: mean reversion complete — exit regardless of PSZ state.
        ceiling = 1.0 - cfg.ceiling_leave_tolerance
        prev_cts = prev_row.get("cts", np.nan)
        if not np.isnan(prev_cts) and prev_cts >= ceiling and cts < ceiling:
            return ExitReason.CEILING_HIT, st.to_int()

        # PSZ glide exit logic: only check if PSZ was above threshold.
        if st.psz_was_above:
            res, _ = self._exit_psz_glide(row, prev_row, trade, st.to_int(), cfg, records, idx)
            if res:
                return res, st.to_int()

        # Safety: CTS drops back to BT (only if BT above floor)
        if not np.isnan(bt) and bt > floor_zone and cts <= bt:
            if bars_held >= cfg.bt_hit_min_bars:
                return ExitReason.BT_HIT, st.to_int()

        return None, st.to_int()

    def _exit_psz_glide(
        self,
        row: dict, prev_row: dict, trade: Trade,
        state: int, cfg: SavgolCTSExitConfig,
        records: list[dict] = None, idx: int = 0,
    ) -> tuple[str | None, int]:
        """PSZ glide exit logic (uses 5-bar mean)."""
        psz_raw = row.get("price_slope_z", np.nan)
        
        if records is not None and idx >= 4:
            # 5-bar mean of PSZ
            lookback_pszs = [records[j].get("price_slope_z", np.nan) for j in range(idx - 4, idx + 1)]
            if not any(np.isnan(lookback_pszs)):
                mean_psz = sum(lookback_pszs) / 5.0
                if mean_psz >= cfg.bt_cross_psz_glide_threshold and psz_raw < cfg.bt_cross_psz_glide_threshold:
                    return ExitReason.PSZ_GLIDE, state
        else:
            # Fallback to 1-bar if records not provided or insufficient history
            prev_psz_raw = prev_row.get("price_slope_z", np.nan)
            if not np.isnan(psz_raw) and not np.isnan(prev_psz_raw):
                if psz_raw < cfg.bt_cross_psz_glide_threshold and prev_psz_raw >= cfg.bt_cross_psz_glide_threshold:
                    return ExitReason.PSZ_GLIDE, state

        return None, state

