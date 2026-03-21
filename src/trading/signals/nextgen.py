from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from typing import Tuple

from src.trading.signals.base import BaseEntryConfig, BaseExitConfig, SignalInterface, Trade
from src.trading.signals.price_divergence import can_exit, PriceDivergenceExitConfig


@dataclass
class NextGenEntryConfig(BaseEntryConfig):
    """Configuration for NextGen entry signal."""
    bear_accel_discount: float = 0.8   # multiplied against accel threshold in bear regime
    bull_slope_min: float = 0.0001     # stricter cts_slope confirmation in bull regime
    pdd_rel_min: float = -0.40         # Gate 5: min (pdd_120 - threshold) / |threshold|; filters deeply exhausted delivery
    vel_max: float = 0.60              # Gate 5: max velocity_60_norm; blocks overly extended delivery spikes
    cts_abs_floor: float = -0.12       # Gate 6: absolute CTS floor; trades with cts < -0.12 have 67-72% hard-stop rate


@dataclass
class NextGenExitConfig(BaseExitConfig):
    """Configuration for NextGen exit signal (reuses price_divergence exit logic)."""
    stop_loss_pct: float = 2.5
    stop_atr_multiple: float = 2
    noise_threshold: float = 0.0002
    early_stop_atr_mult: float = 1.0   # early stop trigger: exit when pnl < -N×ATR
    early_stop_min_bars: int = 2       # earliest bar to check (T+2, avoids intraday classification)
    early_stop_max_bars: int = 7       # latest bar to check (captures 56.9% of HS trades)


class NextGenSignal(SignalInterface):
    """
    NextGen entry signal grounded in per-stock Spearman analysis.
    4-gate system: accel, CTS, delivery, PDD-120.
    Exit logic is delegated to the existing price_divergence can_exit().
    """

    def check_entry(
        self,
        row: dict,
        prev_row: dict,
        cfg: BaseEntryConfig,
        records: list[dict] | None = None,
        idx: int = 0,
    ) -> tuple[bool, int, dict]:
        if not isinstance(cfg, NextGenEntryConfig):
            raise TypeError("cfg must be NextGenEntryConfig")
        enter, intensity, reason = _can_enter(row, prev_row, cfg)
        return enter, int(intensity), {"reason": reason}

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
        if not isinstance(cfg, NextGenExitConfig):
            raise TypeError("cfg must be NextGenExitConfig")

        # Build a PriceDivergenceExitConfig to delegate to can_exit()
        pd_cfg = PriceDivergenceExitConfig(
            stop_loss_pct=cfg.stop_loss_pct,
            stop_atr_multiple=cfg.stop_atr_multiple,
            noise_threshold=cfg.noise_threshold,
        )

        close = row["close"]
        entry = trade.entry_price
        atr_pct = trade.atr_at_entry / entry if entry > 0 else 0.02

        pnl_pct = (close / entry - 1) * 100

        # 1. Hard stop-loss
        if pnl_pct < -(cfg.stop_atr_multiple * atr_pct * 100):
            return "hard_stop", 0

        # 2. CWVAP-based exits run first — let them catch small early losses cheaply.
        qualifies, intensity, reason = can_exit(trade, row, prev_row, pd_cfg, cwvap_values)
        if qualifies:
            return reason, int(intensity)

        # 3. Early stop: backstop for trades CWVAP missed that are still bleeding.
        #    T+2 minimum hold (avoids intraday tax classification).
        #    Only fires if pnl < -early_stop_atr_mult × ATR within bars 2–7.
        if cfg.early_stop_min_bars <= bars_held <= cfg.early_stop_max_bars:
            if pnl_pct < -(cfg.early_stop_atr_mult * atr_pct * 100):
                return "early_stop", 0

        return None, delivery_bad_count


# ─────────────────────────────────────────────────────────────────────────────


def _can_enter(row: dict, prev_row: dict, cfg: NextGenEntryConfig) -> Tuple[bool, float, str]:
    """
    6-gate NextGen entry logic.

    Gate 1: cts_accel > cts_accel_threshold (discounted by bear_accel_discount in bear regime)
    Gate 2: cts >= cts_buy_threshold  AND  cts >= cts_abs_floor
    Gate 3: vel_dp5 >= 4 (velocity delta positive 4/5 bars) AND velocity_60_norm > -0.10
    Gate 4: pdd_120 < pdd_120_threshold
    Gate 5: pdd_rel >= pdd_rel_min  AND  velocity_60_norm <= vel_max
              pdd_rel = (pdd_120 - pdd_120_threshold) / |pdd_120_threshold|
              filters stocks where delivery is deeply exhausted relative to their own
              rolling baseline, and entries chasing an already-extended velocity spike.
    Gate 6: cts >= cts_abs_floor (absolute CTS floor)
              entries with cts < -0.12 have 67-72% hard-stop rate empirically.
    Bull regime extra: cts_slope >= bull_slope_min

    Returns (should_enter, intensity, reason).
    """
    cts = row.get("cts", np.nan)
    cts_slope = row.get("cts_slope", np.nan)
    cts_accel = row.get("cts_accel", np.nan)
    cts_accel_threshold = row.get("cts_accel_threshold", np.nan)
    cts_buy_threshold = row.get("cts_buy_threshold", np.nan)
    velocity_60_norm = row.get("velocity_60_norm", np.nan)
    pdd_120 = row.get("pdd_120", np.nan)
    pdd_120_threshold = row.get("pdd_120_threshold", np.nan)
    psz_v = row.get("psz_v", np.nan)
    psz_v_extreme_threshold = row.get("psz_v_extreme_threshold", np.nan)
    regime = row.get("regime", "")

    # Determine if we are in a bear regime
    is_bear = isinstance(regime, str) and "bear" in regime.lower()
    is_bull = isinstance(regime, str) and "bull" in regime.lower()

    # Gate 1: CTS acceleration above rolling threshold
    if np.isnan(cts_accel) or np.isnan(cts_accel_threshold):
        return False, 0.0, "CTS accel data missing"

    effective_threshold = cts_accel_threshold * cfg.bear_accel_discount if is_bear else cts_accel_threshold
    if cts_accel <= effective_threshold:
        return False, 0.0, "CTS accel below threshold"

    # Gate 2: CTS above buy threshold
    if np.isnan(cts) or np.isnan(cts_buy_threshold):
        return False, 0.0, "CTS data missing"
    if cts < cts_buy_threshold:
        return False, 0.0, "CTS below buy threshold"

    # Gate 3: Delivery momentum — velocity trending toward zero (vel_dp4) AND above floor
    vel_dp5 = row.get("vel_dp5", np.nan)
    vel_approaching = (not np.isnan(velocity_60_norm)) and velocity_60_norm > -0.10
    vel_trending = (not np.isnan(vel_dp5)) and vel_dp5 >= 4
    if not (vel_trending and vel_approaching):
        return False, 0.0, "No delivery momentum (vel not trending toward zero)"

    # Gate 4: PDD-120 below its rolling threshold (stock not yet in exhaustion)
    if np.isnan(pdd_120) or np.isnan(pdd_120_threshold):
        return False, 0.0, "PDD-120 data missing"
    if pdd_120 >= pdd_120_threshold:
        return False, 0.0, "PDD-120 at or above threshold (exhaustion)"

    # Gate 6: Absolute CTS floor — blocks entries where CTS is deeply negative.
    #         Empirically, cts < -0.12 leads to 67-72% hard-stop rate in 30-bar holds.
    if not np.isnan(cts) and cts < cfg.cts_abs_floor:
        return False, 0.0, f"Gate 6: CTS too low (cts={cts:.3f} < {cfg.cts_abs_floor})"

    # Gate 5: Delivery freshness — pdd_rel within acceptable distance from threshold,
    #         and velocity not already overly extended.
    #         pdd_rel = (pdd_120 - pdd_120_threshold) / |pdd_120_threshold|
    #         A value of -0.4 means pdd_120 is 40% below its threshold — empirically,
    #         deeper values (< -0.4) yield negative expected PnL across NIFTY 500.
    if pdd_120_threshold != 0:
        pdd_rel = (pdd_120 - pdd_120_threshold) / abs(pdd_120_threshold)
        if pdd_rel < cfg.pdd_rel_min:
            return False, 0.0, f"Gate 5: delivery too exhausted (pdd_rel={pdd_rel:.2f})"
    if not np.isnan(velocity_60_norm) and velocity_60_norm > cfg.vel_max:
        return False, 0.0, f"Gate 5: velocity too extended (vel={velocity_60_norm:.2f})"

    # Bull regime extra gate: cts_slope must confirm upward momentum
    if is_bull:
        if np.isnan(cts_slope) or cts_slope < cfg.bull_slope_min:
            return False, 0.0, "Bull regime: CTS slope too weak"

    # All gates passed — compute intensity
    accel_ratio = cts_accel / effective_threshold if effective_threshold != 0 else 1.0
    intensity = 40.0 + min(30.0, (accel_ratio - 1.0) * 30.0)

    # Bonus: strong momentum (velocity already approaching zero)
    vel_strong = (not np.isnan(velocity_60_norm)) and velocity_60_norm >= 0
    if vel_strong:
        intensity += 10.0

    psz_v_extreme = (
        not np.isnan(psz_v)
        and not np.isnan(psz_v_extreme_threshold)
        and abs(psz_v) >= psz_v_extreme_threshold
    )
    if psz_v_extreme:
        intensity += 10.0

    intensity = min(100.0, max(0.0, float(np.nan_to_num(intensity))))

    if psz_v_extreme and vel_strong:
        reason = "NextGen: All gates passed [HIGH MOMENTUM]"
    else:
        reason = "NextGen: All gates passed"

    return True, intensity, reason
