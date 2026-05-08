from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import PrtZeroCrossEntryConfig
from src.trading.signals.savgol_cts.scoring import compute_intensity
from src.trading.signals.savgol_cts.ml_guard import MLGuard

def check_prt_zero_cross(
    row: dict,
    prev_row: dict,
    cfg: PrtZeroCrossEntryConfig,
    records: list[dict] | None = None,
    idx: int = 0,
) -> tuple[bool, int, dict]:
    """Check entry condition for PRT Zero Cross path with mandatory ML Guard."""
    if not cfg.enabled:
        return False, 0, {"reason": "Disabled"}

    prt_slope = row.get("prt_slope", np.nan)
    prev_prt_slope = prev_row.get("prt_slope", np.nan)

    if any(np.isnan(x) for x in [prt_slope, prev_prt_slope]):
        return False, 0, {"reason": "Missing data for PRT Zero Cross"}

    # GATE 1: PRT Slope crosses zero from below
    if not (prev_prt_slope <= 0 and prt_slope > 0):
        return False, 0, {"reason": f"PRT Slope no zero cross ({prev_prt_slope:.4f} -> {prt_slope:.4f})"}

    # GATE 2: ML Guard
    ml_guard = MLGuard.get_instance()
    prob = ml_guard.score_setup(row)

    if prob is None:
        return False, 0, {"reason": "ML Guard model not available"}

    prob_pct = prob * 100.0
    if prob_pct < cfg.min_ml_score:
        return False, 0, {"reason": f"ML Guard Failed: Score {prob_pct:.1f}% < {cfg.min_ml_score}%"}

    # All gates passed
    # Override intensity with the ML confidence score (prob_pct) so it maps to the UI correctly
    intensity, meta = compute_intensity(
        row, prev_row, EntryTag.PRT_ZERO_CROSS,
        [f"prt_s={prt_slope:.4f}", f"ml={prob_pct:.1f}%"],
        override_score=prob_pct
    )
    meta["score"] = float(intensity)
    meta["ml_score"] = prob_pct

    return True, intensity, meta
