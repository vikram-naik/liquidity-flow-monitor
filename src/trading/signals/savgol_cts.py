from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.trading.signals.base import BaseEntryConfig, BaseExitConfig, SignalInterface, Trade


@dataclass
class SavgolCTSEntryConfig(BaseEntryConfig):
    """Configuration for CTS -1/+1 mean-reversion entry signal."""
    cts_floor: float = -1.0
    coherence_max: float = 0.3
    pdd_floor: float = -10.0
    exclude_uptrend: bool = True


@dataclass
class SavgolCTSExitConfig(BaseExitConfig):
    """Configuration for CTS -1/+1 mean-reversion exit signal.

    Exit triggers when CTS drops back to buy_threshold or -1.0 after
    having risen above -1.0 at least once (confirming the trade moved).
    """
    pass


class SavgolCTSSignal(SignalInterface):
    """CTS -1/+1 mean-reversion signal.

    Entry: CTS <= -1.0 AND coherence <= 0.3 AND pdd_120 > -10.0
           AND regime != 'uptrend'
    Exit:  CTS drops to buy_threshold OR -1.0 (after having risen above -1)

    The ``delivery_bad_count`` parameter from the base interface is repurposed
    as a boolean flag (0/1) to track whether CTS has risen above -1.0 during
    the trade (``cts_rose``).
    """

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
        coh = row.get("coherence", np.nan)
        pdd = row.get("pdd_120", np.nan)
        regime = row.get("regime", "")

        if np.isnan(cts) or np.isnan(coh):
            return False, 0, {"reason": "Missing CTS or coherence data"}

        # Gate: regime != uptrend (checked on signal bar)
        if cfg.exclude_uptrend and regime == "uptrend":
            return False, 0, {"reason": "Uptrend regime excluded"}

        # Gate: pdd_120 > floor (not deeply delivery-exhausted)
        if not np.isnan(pdd) and pdd <= cfg.pdd_floor:
            return False, 0, {"reason": f"PDD exhausted ({pdd:.1f})"}

        # Gate: coherence <= max (divergence present)
        if coh > cfg.coherence_max:
            return False, 0, {"reason": f"Coherence too high ({coh:.2f})"}

        # Gate: CTS at floor (maximum oversold)
        if cts > cfg.cts_floor:
            return False, 0, {"reason": "Neutral/No Entry"}

        # All gates passed — compute intensity (0–100)
        # Base 50 for hitting all gates
        intensity = 50.0

        # Coherence bonus: lower = more divergence = better setup (0–25)
        # coh range [0, 0.3] → bonus [25, 0]
        coh_bonus = max(0.0, (cfg.coherence_max - coh) / cfg.coherence_max) * 25.0
        intensity += coh_bonus

        # PDD bonus: closer to zero = less exhaustion = better quality (0–15)
        if not np.isnan(pdd):
            pdd_range = abs(cfg.pdd_floor)  # 10.0
            pdd_bonus = max(0.0, (pdd - cfg.pdd_floor) / pdd_range) * 15.0
            intensity += pdd_bonus

        # Regime bonus: downtrend/notrend preferred for mean-reversion (0–10)
        if regime == "downtrend":
            intensity += 10.0
        elif regime == "notrend":
            intensity += 5.0

        intensity = min(100.0, max(0.0, float(np.nan_to_num(intensity))))
        intensity_int = int(round(intensity))

        # Build reason string
        parts = [f"CTS={cts:.2f}", f"coh={coh:.2f}"]
        if not np.isnan(pdd):
            parts.append(f"pdd={pdd:.1f}")
        parts.append(regime)

        if intensity >= 80:
            reason = f"SavgolCTS: STRONG [{', '.join(parts)}]"
        elif intensity >= 65:
            reason = f"SavgolCTS: good [{', '.join(parts)}]"
        else:
            reason = f"SavgolCTS: [{', '.join(parts)}]"

        return True, intensity_int, {"reason": reason}

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
    ) -> tuple[str | None, int]:
        """Exit when CTS drops to buy_threshold or -1.0 after having risen above -1.

        ``delivery_bad_count`` is repurposed as ``cts_rose`` flag (0 or 1).
        """
        cts = row.get("cts", np.nan)
        bt = row.get("cts_buy_threshold", np.nan)
        cts_rose = delivery_bad_count  # 0 = not yet risen, 1 = risen

        if np.isnan(cts):
            return None, cts_rose

        # Track that CTS has risen above -1 (trade is moving)
        if cts > -1.0:
            cts_rose = 1

        # Don't exit until CTS has risen above -1 at least once
        if not cts_rose:
            return None, cts_rose

        # Exit: CTS drops to buy_threshold (dynamic per-stock level)
        if not np.isnan(bt) and cts <= bt:
            return f"CTS hit BT ({bt:.3f})", cts_rose

        # Exit: CTS drops back to -1.0 (full round-trip)
        if cts <= -1.0:
            return "CTS hit -1.0", cts_rose

        return None, cts_rose
