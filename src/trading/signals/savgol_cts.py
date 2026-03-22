from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.trading.signals.base import BaseEntryConfig, BaseExitConfig, SignalInterface, Trade


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
    psz_bend_enabled: bool = True
    psz_bend_lookback: int = 8
    psz_bend_quiescence_threshold: float = 0.05
    psz_bend_quiescence_min_bars: int = 5
    psz_bend_v_floor: float = -0.05
    psz_bend_v_min_at_signal: float = 0.02
    # Mean psz_v gate: if the average psz_v over the lookback is above this,
    # PSZ was already drifting upward (not a fresh base) — empirically (NIFTY 50
    # 2024+) mean_v >= 0.010 drops WR below 50% and avg PnL near zero.
    psz_bend_mean_v_max: float = 0.010
    # BT crossover complement: CTS crosses BT from below in oversold zone.
    # Catches V-bottoms where CTS hits floor but BT hasn't caught up.
    bt_cross_enabled: bool = True
    bt_cross_oversold_threshold: float = -0.50
    # BT depth gate: reject BT-cross when BT is too shallow (not deeply oversold).
    # Empirically (NIFTY 500): trades with bt > -0.71 have 51% WR and near-zero
    # median PnL — gating at -0.71 drops 25% of BT-cross volume but lifts WR +2pp.
    bt_cross_bt_max: float = -0.71


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
    # PSZ stall early exit: at bar N after entry, if psz_v is non-positive
    # the momentum has not materialised — exit.
    psz_stall_enabled: bool = False
    psz_stall_check_bar: int = 2
    # BT-cross PSZ glide exit: hold until PSZ drops below this after having been above it.
    # Only applies to BT-cross entries. Safety nets: hit_bt / hit_floor still active.
    bt_cross_psz_glide_threshold: float = 0.30


class SavgolCTSSignal(SignalInterface):
    """CTS -1/+1 mean-reversion signal with three entry paths (priority order).

    Path 1 (CTS-floor-leave): CTS rises above -1.0 after being pinned there.
        prev_cts <= -0.98, cts > -0.98. No BT constraint. Fires on lift bar,
        not touch bar — confirms floor held. Ceiling exits dominate.
    Path 2 (PSZ bend):        PSZ velocity quiescent base + upward turn.
        Fires independent of CTS level.
    Path 3 (BT-cross):        CTS crosses BT from below in oversold zone.
        BT depth gate (bt_max=-0.71) filters shallow entries.

    Exit:  CTS drops to buy_threshold OR -1.0 (after having risen above -1).
           Floor-leave trades skip sell threshold — ceiling and BT exits only.

    The ``delivery_bad_count`` parameter from the base interface is repurposed
    as a bitfield tracking per-trade state (see SIGNAL_FLOW.md).
    """

    def _compute_intensity(
        self, cts: float, coh: float, pdd: float, regime: str, tag: str,
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
            reason = f"SavgolCTS {tag}: STRONG [{', '.join(parts)}]"
        elif intensity >= 65:
            reason = f"SavgolCTS {tag}: good [{', '.join(parts)}]"
        else:
            reason = f"SavgolCTS {tag}: [{', '.join(parts)}]"

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
            return True

        lookback = cfg.psz_bend_lookback
        start = max(0, idx - lookback)
        if start >= idx:
            return True  # not enough history — don't block

        # Condition 3: upward turn at signal bar while PSZ is still negative
        # (if PSZ is already positive we're entering late — the move has run)
        psz_raw_now = records[idx].get("price_slope_z", np.nan)
        psz_v_now = records[idx].get("psz_v", np.nan)
        psz_v_prev = records[idx - 1].get("psz_v", np.nan) if idx > 0 else np.nan
        if np.isnan(psz_v_now) or np.isnan(psz_v_prev) or np.isnan(psz_raw_now):
            return False
        if psz_raw_now >= 0:
            return False
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
            return True, f"PSZ stall (v={psz_v:+.4f} at bar {bars_held})"
        return False, ""

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

        floor = cfg.cts_floor + cfg.floor_touch_tolerance
        if not (prev_cts <= floor and cts > floor):
            return False, 0, {"reason": "CTS not leaving floor"}

        coh    = row.get("coherence", np.nan)
        pdd    = row.get("pdd_120", np.nan)
        regime = row.get("regime", "")
        intensity_int, meta = self._compute_intensity(
            cts, coh, pdd, regime, "CTS-floor-leave",
            [f"prev_cts={prev_cts:.3f}"],
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
        psz_raw = row.get("price_slope_z", np.nan)
        psz_prev = prev_row.get("price_slope_z", np.nan)
        if np.isnan(psz_raw) or np.isnan(psz_prev):
            return False, 0, {"reason": "Missing PSZ data"}
        if not self._is_psz_bending(records, idx, cfg):
            return False, 0, {"reason": "PSZ not bending (psz_v not quiescent)"}

        cts = row.get("cts", np.nan)
        coh = row.get("coherence", np.nan)
        pdd = row.get("pdd_120", np.nan)
        regime = row.get("regime", "")
        intensity_int, meta = self._compute_intensity(
            cts, coh, pdd, regime, "PSZ",
            [f"psz={psz_raw:.3f}", f"prev_psz={psz_prev:.3f}"],
        )
        return True, intensity_int, meta

    def _check_bt_crossover(
        self, row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
    ) -> tuple[bool, int, dict]:
        """Path 2: CTS crosses BT from below while in oversold territory."""
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
        if not np.isnan(psz_raw) and psz_raw >= 0:
            return False, 0, {"reason": f"PSZ already positive ({psz_raw:.3f}), late entry"}

        # BT depth gate: BT must be sufficiently oversold
        if not np.isnan(bt) and bt > cfg.bt_cross_bt_max:
            return False, 0, {"reason": f"BT {bt:.3f} not deep enough (max {cfg.bt_cross_bt_max})"}

        coh = row.get("coherence", np.nan)
        pdd = row.get("pdd_120", np.nan)
        regime = row.get("regime", "")
        intensity_int, meta = self._compute_intensity(
            cts, coh, pdd, regime, "BT-cross",
            [f"bt={bt:.3f}", f"prev_cts={prev_cts:.3f}"],
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

        cts = row.get("cts", np.nan)
        if np.isnan(cts):
            return False, 0, {"reason": "Missing CTS data"}

        # Path 1: CTS-floor-leave — CTS rising above floor after being pinned
        passed, intensity, meta = self._check_floor_leave(row, prev_row, cfg)
        if passed:
            return True, intensity, meta

        # Path 2: PSZ bend — quiescent base + upward PSZ turn
        passed, intensity, meta = self._check_psz_bend(row, prev_row, cfg, records, idx)
        if passed:
            return True, intensity, meta

        # Path 3: BT-cross — CTS crosses BT from below in oversold zone
        passed, intensity, meta = self._check_bt_crossover(row, prev_row, cfg)
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
        """Exit logic, branched by entry path.

        ``delivery_bad_count`` is repurposed as a bitfield:
          bit 0 (& 1): cts_rose — CTS has risen above -1.0
          bit 1 (& 2): psz_was_above — PSZ crossed above glide threshold
                        (BT-cross trades only)
        """
        if not isinstance(cfg, SavgolCTSExitConfig):
            cfg = SavgolCTSExitConfig()

        tag = trade.entry_tag if trade is not None else ""

        if tag in ("CTS-floor-leave", "CTS-BT-floor"):
            return self._exit_floor(
                row, prev_row, trade, peak_close, bars_held,
                delivery_bad_count, cfg, records, idx,
            )
        if tag == "BT-cross":
            return self._exit_bt_cross(
                row, prev_row, trade, peak_close, bars_held,
                delivery_bad_count, cfg, records, idx,
            )
        return self._exit_psz(
            row, prev_row, trade, peak_close, bars_held,
            delivery_bad_count, cfg, records, idx,
        )

    def _exit_floor(
        self,
        row: dict, prev_row: dict, trade: Trade,
        peak_close: float, bars_held: int, state: int,
        cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
    ) -> tuple[str | None, int]:
        """Exit logic for CTS-floor-leave entries (also handles legacy CTS-BT-floor tag).

        Sell threshold suppressed — floor entries have the full CTS range to travel.
        Empirically (NIFTY 500): 63.9% WR, +2.52% avg, 73.1% ceiling exits.
        Exits: ceiling hit, CTS hit BT, CTS hit -1.0.
        """
        cts      = row.get("cts", np.nan)
        bt       = row.get("cts_buy_threshold", np.nan)
        psz_raw  = row.get("price_slope_z", np.nan)
        cts_rose     = state & 1
        cts_above_bt = (state >> 2) & 1

        if np.isnan(cts):
            return None, state

        if cts > -1.0:
            cts_rose = 1
        if not np.isnan(bt) and cts > bt:
            cts_above_bt = 1
        state = (cts_above_bt << 2) | (state & 2) | cts_rose

        stalled, stall_reason = self._is_psz_stalled(records, trade, idx, bars_held, cfg)
        if stalled:
            return stall_reason, state

        if not cts_rose:
            return None, state

        floor_zone = -1.0 + cfg.floor_tolerance
        if cts <= floor_zone and not np.isnan(bt) and bt <= floor_zone:
            return None, state

        if not np.isnan(psz_raw) and psz_raw < -0.26:
            return None, state

        # Ceiling-leave exit: CTS drops below ceiling after having been there
        ceiling = 1.0 - cfg.ceiling_leave_tolerance
        prev_cts = prev_row.get("cts", np.nan)
        if not np.isnan(prev_cts) and prev_cts >= ceiling and cts < ceiling:
            return "CTS ceiling hit", state

        # Sell threshold intentionally omitted for floor entries.

        # Hit BT
        if not np.isnan(bt) and bt > floor_zone and cts <= bt and cts_above_bt:
            return f"CTS hit BT ({bt:.3f})", state

        # Hit floor
        if cts <= -1.0:
            return "CTS hit -1.0", state

        return None, state

    def _exit_psz(
        self,
        row: dict, prev_row: dict, trade: Trade,
        peak_close: float, bars_held: int, state: int,
        cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
    ) -> tuple[str | None, int]:
        """Original exit logic for PSZ crossover entries."""
        cts = row.get("cts", np.nan)
        prev_cts = prev_row.get("cts", np.nan)
        bt = row.get("cts_buy_threshold", np.nan)
        cts_rose = state & 1
        cts_above_bt = (state >> 2) & 1  # bit 2: CTS has been above BT

        if np.isnan(cts):
            return None, state

        if cts > -1.0:
            cts_rose = 1
        if not np.isnan(bt) and cts > bt:
            cts_above_bt = 1
        state = (cts_above_bt << 2) | (state & 2) | cts_rose

        # PSZ stall early exit
        psz_raw = row.get("price_slope_z", np.nan)
        stalled, stall_reason = self._is_psz_stalled(records, trade, idx, bars_held, cfg)
        if stalled:
            return stall_reason, state

        if not cts_rose:
            return None, state

        # Suppress exit: floor zone — both CTS and BT deeply oversold,
        # stock has room to revive
        floor_zone = -1.0 + cfg.floor_tolerance
        if cts <= floor_zone and not np.isnan(bt) and bt <= floor_zone:
            return None, state

        # Suppress exit: deeply negative PSZ
        if not np.isnan(psz_raw) and psz_raw < -0.26:
            return None, state

        # Ceiling-leave exit: CTS drops below ceiling after having been there
        ceiling = 1.0 - cfg.ceiling_leave_tolerance
        if not np.isnan(prev_cts) and prev_cts >= ceiling and cts < ceiling:
            return "CTS ceiling hit", state

        # Sell threshold exit
        st = row.get("cts_sell_threshold", np.nan)
        if not np.isnan(st) and prev_cts > (st - cfg.st_crossover_tolerance) and cts < 1.0 and cts < prev_cts and cts < st:
            return "CTS sell threshold hit", state

        # Floor exit: hit BT (only if BT above floor zone AND CTS was above BT
        # at some point — prevents premature exit when PSZ bend enters with
        # CTS already below BT)
        if not np.isnan(bt) and bt > floor_zone and cts <= bt and cts_above_bt:
            return f"CTS hit BT ({bt:.3f})", state

        # Floor exit: hit -1.0
        if cts <= -1.0:
            return "CTS hit -1.0", state

        return None, state

    def _exit_bt_cross(
        self,
        row: dict, prev_row: dict, trade: Trade,
        peak_close: float, bars_held: int, state: int,
        cfg: SavgolCTSExitConfig, records: list[dict] | None, idx: int,
    ) -> tuple[str | None, int]:
        """PSZ glide exit for BT-cross entries.

        Hold until PSZ raw drops below glide threshold after having been above it.
        Safety nets: hit_bt / hit_floor still active.
        """
        cts = row.get("cts", np.nan)
        bt = row.get("cts_buy_threshold", np.nan)
        psz_raw = row.get("price_slope_z", np.nan)
        prev_psz_raw = prev_row.get("price_slope_z", np.nan)

        cts_rose = state & 1
        psz_was_above = (state >> 1) & 1

        if np.isnan(cts):
            return None, state

        if cts > -1.0:
            cts_rose = 1

        # Track if PSZ raw ever rose above glide threshold.
        if not np.isnan(psz_raw) and psz_raw >= cfg.bt_cross_psz_glide_threshold:
            psz_was_above = 1

        state = (psz_was_above << 1) | cts_rose

        # PSZ stall early exit
        stalled, stall_reason = self._is_psz_stalled(records, trade, idx, bars_held, cfg)
        if stalled:
            return stall_reason, state

        if not cts_rose:
            return None, state

        # Suppress exit: floor zone — both CTS and BT deeply oversold,
        # stock has room to revive
        floor_zone = -1.0 + cfg.floor_tolerance
        if cts <= floor_zone and not np.isnan(bt) and bt <= floor_zone:
            return None, state

        # Ceiling-leave exit: mean reversion complete — exit regardless of PSZ state.
        # PSZ glide is the primary exit but may not fire if PSZ never crossed
        # the glide threshold; ceiling is the definitive safety net.
        ceiling = 1.0 - cfg.ceiling_leave_tolerance
        prev_cts = prev_row.get("cts", np.nan)
        if not np.isnan(prev_cts) and prev_cts >= ceiling and cts < ceiling:
            return "CTS ceiling hit", state

        # PSZ glide exit: once PSZ raw was above threshold, exit when it drops below.
        if psz_was_above and not np.isnan(psz_raw) and not np.isnan(prev_psz_raw):
            if psz_raw < cfg.bt_cross_psz_glide_threshold and prev_psz_raw >= cfg.bt_cross_psz_glide_threshold:
                return f"PSZ glide exit (<{cfg.bt_cross_psz_glide_threshold})", state

        # Safety: CTS drops back to BT (only if BT above floor — at floor
        # the stock is deeply oversold and has room to revive)
        if not np.isnan(bt) and bt > floor_zone and cts <= bt:
            return f"CTS hit BT ({bt:.3f})", state

        # Safety: CTS drops to floor
        if cts <= -1.0:
            return "CTS hit -1.0", state

        return None, state

