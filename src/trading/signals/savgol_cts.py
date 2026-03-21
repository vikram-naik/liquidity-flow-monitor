from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.trading.signals.base import BaseEntryConfig, BaseExitConfig, SignalInterface, Trade


@dataclass
class SavgolCTSEntryConfig(BaseEntryConfig):
    """Configuration for CTS -1/+1 mean-reversion entry signal.

    Two complementary entry paths:
    1. PSZ crossover: CTS and BT both at floor (-1.0), PSZ raw crosses
       above psz_cross_threshold (sustained oversold + structural turn).
    2. BT crossover: CTS crosses above BT from below while still in
       oversold territory (V-bottom recovery before BT reaches -1.0).
    """
    cts_floor: float = -1.0
    # PSZ raw crossover: signal fires when psz_raw crosses above this level
    # while CTS and BT are pinned at floor. Replaces psz_v/bend gates.
    psz_cross_threshold: float = -0.25
    # BT crossover complement: CTS crosses BT from below in oversold zone.
    # Catches V-bottoms where CTS hits floor but BT hasn't caught up.
    bt_cross_enabled: bool = True
    bt_cross_oversold_threshold: float = -0.50


@dataclass
class SavgolCTSExitConfig(BaseExitConfig):
    """Configuration for CTS -1/+1 mean-reversion exit signal.

    Exit triggers when:
    - prev CTS == 1.0 and current CTS < 1.0 (ceiling-reversal exit), OR
    - CTS drops back to buy_threshold or -1.0 (floor exit).
    """
    st_crossover_tolerance: float = 0.03
    floor_tolerance: float = 0.10
    # PSZ stall early exit: if PSZ raw hasn't risen by this much from entry by T+N, exit
    psz_stall_min_delta: float = 0.005
    psz_stall_check_bar: int = 3
    # BT-cross PSZ glide exit: hold until PSZ drops below this after having been above it.
    # Only applies to BT-cross entries. Safety nets: hit_bt / hit_floor still active.
    bt_cross_psz_glide_threshold: float = 0.30


class SavgolCTSSignal(SignalInterface):
    """CTS -1/+1 mean-reversion signal with two complementary entry paths.

    Path 1 (PSZ crossover): CTS <= -1.0 AND BT <= -1.0 AND PSZ raw crosses
        above -0.25.  Catches sustained oversold setups.
    Path 2 (BT crossover):  prev_CTS <= prev_BT AND CTS > BT AND CTS <=
        oversold threshold.  Catches V-bottom recoveries.

    Exit:  CTS drops to buy_threshold OR -1.0 (after having risen above -1)

    The ``delivery_bad_count`` parameter from the base interface is repurposed
    as a boolean flag (0/1) to track whether CTS has risen above -1.0 during
    the trade (``cts_rose``).
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

    def _check_psz_crossover(
        self, row: dict, prev_row: dict, cfg: SavgolCTSEntryConfig,
    ) -> tuple[bool, int, dict]:
        """Path 1: CTS and BT at floor, PSZ raw crosses above threshold."""
        cts = row.get("cts", np.nan)
        bt = row.get("cts_buy_threshold", np.nan)

        if cts > cfg.cts_floor:
            return False, 0, {"reason": "Neutral/No Entry"}
        if np.isnan(bt) or bt > cfg.cts_floor:
            return False, 0, {"reason": f"BT not exhausted ({bt:.3f})" if not np.isnan(bt) else "Missing BT"}

        psz_raw = row.get("price_slope_z", np.nan)
        psz_prev = prev_row.get("price_slope_z", np.nan)
        if np.isnan(psz_raw) or np.isnan(psz_prev):
            return False, 0, {"reason": "Missing PSZ data"}
        if not (psz_raw > cfg.psz_cross_threshold and psz_prev <= cfg.psz_cross_threshold):
            return False, 0, {"reason": f"PSZ not crossing {cfg.psz_cross_threshold} ({psz_prev:.3f} -> {psz_raw:.3f})"}

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

        # Must still be in oversold territory
        if cts > cfg.bt_cross_oversold_threshold:
            return False, 0, {"reason": f"CTS {cts:.3f} above oversold threshold {cfg.bt_cross_oversold_threshold}"}

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

        # Path 1: PSZ crossover (CTS and BT at floor)
        passed, intensity, meta = self._check_psz_crossover(row, prev_row, cfg)
        if passed:
            return True, intensity, meta

        # Path 2: BT crossover complement (V-bottom recovery)
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

        is_bt_cross = trade is not None and trade.entry_tag == "BT-cross"

        if is_bt_cross:
            return self._exit_bt_cross(
                row, prev_row, trade, peak_close, bars_held,
                delivery_bad_count, cfg, records, idx,
            )
        return self._exit_psz(
            row, prev_row, trade, peak_close, bars_held,
            delivery_bad_count, cfg, records, idx,
        )

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

        if np.isnan(cts):
            return None, state

        if cts > -1.0:
            cts_rose = 1
        state = (state & ~1) | cts_rose

        # PSZ stall early exit
        psz_raw = row.get("price_slope_z", np.nan)
        if (
            records is not None
            and trade is not None
            and bars_held == cfg.psz_stall_check_bar
            and cfg.psz_stall_min_delta > 0
        ):
            entry_psz = records[trade.entry_idx].get("price_slope_z", np.nan)
            if not np.isnan(psz_raw) and not np.isnan(entry_psz):
                psz_delta = psz_raw - entry_psz
                if psz_delta < cfg.psz_stall_min_delta:
                    return f"PSZ stall (d={psz_delta:+.4f})", state

        if not cts_rose:
            return None, state

        # Suppress exit: floor zone
        if cts <= (-1.0 + cfg.floor_tolerance) and not np.isnan(bt) and bt <= (-1.0 + cfg.floor_tolerance):
            return None, state

        # Suppress exit: deeply negative PSZ
        if not np.isnan(psz_raw) and psz_raw < -0.26:
            return None, state

        # Ceiling exit
        if cts >= 1.0:
            return "CTS ceiling hit", state

        # Sell threshold exit
        st = row.get("cts_sell_threshold", np.nan)
        if not np.isnan(st) and prev_cts > (st - cfg.st_crossover_tolerance) and cts < 1.0 and cts < prev_cts and cts < st:
            return "CTS sell threshold hit", state

        # Floor exit: hit BT
        if not np.isnan(bt) and cts <= bt:
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

        Hold until PSZ drops below glide threshold after having been above it.
        Safety nets: hit_bt / hit_floor still active.
        """
        cts = row.get("cts", np.nan)
        bt = row.get("cts_buy_threshold", np.nan)
        psz_raw = row.get("price_slope_z", np.nan)
        prev_psz = prev_row.get("price_slope_z", np.nan)

        cts_rose = state & 1
        psz_was_above = (state >> 1) & 1

        if np.isnan(cts):
            return None, state

        if cts > -1.0:
            cts_rose = 1

        # Track if PSZ ever rose above glide threshold
        if not np.isnan(psz_raw) and psz_raw >= cfg.bt_cross_psz_glide_threshold:
            psz_was_above = 1

        state = (psz_was_above << 1) | cts_rose

        # PSZ stall early exit (same as PSZ path — catches dead trades)
        if (
            records is not None
            and trade is not None
            and bars_held == cfg.psz_stall_check_bar
            and cfg.psz_stall_min_delta > 0
        ):
            entry_psz = records[trade.entry_idx].get("price_slope_z", np.nan)
            if not np.isnan(psz_raw) and not np.isnan(entry_psz):
                psz_delta = psz_raw - entry_psz
                if psz_delta < cfg.psz_stall_min_delta:
                    return f"PSZ stall (d={psz_delta:+.4f})", state

        if not cts_rose:
            return None, state

        # PSZ glide exit: once PSZ was above threshold, exit when it drops below
        if psz_was_above and not np.isnan(psz_raw) and not np.isnan(prev_psz):
            if psz_raw < cfg.bt_cross_psz_glide_threshold and prev_psz >= cfg.bt_cross_psz_glide_threshold:
                return f"PSZ glide exit (<{cfg.bt_cross_psz_glide_threshold})", state

        # Safety: CTS drops back to BT (round-trip)
        if not np.isnan(bt) and cts <= bt:
            return f"CTS hit BT ({bt:.3f})", state

        # Safety: CTS drops to floor
        if cts <= -1.0:
            return "CTS hit -1.0", state

        return None, state
