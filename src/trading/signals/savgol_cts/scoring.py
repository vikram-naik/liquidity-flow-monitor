"""Shared intensity scoring for all SavgolCTS entry paths.

Intensity is a 0–100 score combining structural features like CWVAP distance,
PDD, regime bonuses, and path-specific momentum thrusts.
The score is used by the simulation loop to rank signal quality.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag


def _interp(val: float, min_val: float, max_val: float, min_pts: float, max_pts: float) -> float:
    """Linearly interpolate val between min_val and max_val to a point scale."""
    if np.isnan(val):
        return 0.0
    if min_val == max_val:
        return max_pts if val >= max_val else min_pts
        
    ratio = (val - min_val) / (max_val - min_val)
    ratio = max(0.0, min(1.0, ratio)) # clamp
    return min_pts + ratio * (max_pts - min_pts)


def compute_intensity(
    row: dict,
    prev_row: dict,
    tag: EntryTag,
    extra_parts: list[str] | None = None,
    override_score: float | None = None,
) -> tuple[int, dict]:
    """Score entry quality and build a human-readable reason string.

    Returns:
        (intensity_int, meta_dict) where meta_dict contains ``reason``
        and ``entry_tag`` keys.
    """
    if override_score is not None:
        intensity = override_score
    else:
        intensity = 20.0  # Base score
    
    regime = row.get("regime", "")
    pdd = row.get("pdd_120", np.nan)
    cts = row.get("cts", np.nan)
    close = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)
    
    cwvap_dist = np.nan
    if not np.isnan(close) and not np.isnan(cwvap) and cwvap > 0:
        cwvap_dist = (close - cwvap) / cwvap * 100.0

    # Regime bonus: downtrend/notrend preferred for mean-reversion (0–10)
    if override_score is None:
        if regime == "downtrend":
            intensity += 10.0
        elif regime == "notrend":
            intensity += 5.0

    # Path-Specific Scoring (Max 70 points)
    path_score = 0.0
    
    if tag == EntryTag.SLOPE_BOTTOM:
        # For Slope Bottom, PDD is positively correlated (+0.09)
        # Closer to 0.0 gets more points
        if not np.isnan(pdd):
            path_score += _interp(pdd, -10.0, 0.0, 0.0, 10.0)
            
        # 1. Depth of Exhaustion (CTS): -0.85 -> 0 pts, -1.0 -> 20 pts
        path_score += _interp(cts, -0.85, -1.0, 0.0, 20.0)
        
        # 2. Structural Location (CWVAP Dist): Deeper is better
        if not np.isnan(cwvap_dist):
            path_score += _interp(cwvap_dist, -0.5, -4.0, 0.0, 20.0)
            
        # 3. Inflection Sharpness (Slope Delta)
        cs = row.get("cts_slope", np.nan)
        pcs = prev_row.get("cts_slope", np.nan)
        if not np.isnan(cs) and not np.isnan(pcs):
            slope_delta = cs - pcs
            path_score += _interp(slope_delta, 0.002, 0.02, 0.0, 20.0)
            
    elif tag == EntryTag.INSTITUTIONAL_FLOOR:
        # 1. Depth of Price Exhaustion (PSZ): -0.30 -> 0 pts, -0.60 -> 25 pts
        psz = row.get("price_slope_z", np.nan)
        if not np.isnan(psz):
            path_score += _interp(psz, -0.30, -0.60, 0.0, 25.0)

        # 2. Institutional Alignment (CWC Slope): 0.0 -> 0 pts, 0.05 -> 25 pts
        cwc_s = row.get("cwc_slope", np.nan)
        if not np.isnan(cwc_s):
            path_score += _interp(cwc_s, 0.0, 0.05, 0.0, 25.0)

        # 3. Structural Location (CWVAP Dist): Deeper is better
        if not np.isnan(cwvap_dist):
            path_score += _interp(cwvap_dist, 0.0, -5.0, 0.0, 20.0)

    elif tag == EntryTag.ACCEL:
        # Score is passed via override_score from the study-logic module
        pass

    elif tag == EntryTag.ACCEL_ZERO_CROSS:
        # Score based on how deep cts and cts_slope are.
        if not np.isnan(cts):
            # CTS: -0.85 -> 0 pts, -1.0 -> 25 pts
            path_score += _interp(cts, -0.85, -1.0, 0.0, 25.0)
        
        cs = row.get("cts_slope", np.nan)
        if not np.isnan(cs):
            # CTS Slope: -0.1 -> 0 pts, -0.2 -> 25 pts
            path_score += _interp(cs, -0.1, -0.2, 0.0, 25.0)

    elif tag == EntryTag.FAS_FLOOR_REVERSION:
        # FAS Floor Reversion is an extreme bottom signal
        # 1. Depth of FAS: -1.10 -> 0 pts, -1.5 -> 25 pts
        fas = row.get("fas", np.nan)
        if not np.isnan(fas):
            path_score += _interp(fas, -1.10, -1.50, 0.0, 25.0)
        
        # 2. PRT Slope inflection: 0.0 -> 0 pts, 0.05 -> 25 pts
        prt_s = row.get("prt_slope", np.nan)
        if not np.isnan(prt_s):
            path_score += _interp(prt_s, 0.0, 0.05, 0.0, 25.0)
            
        # 3. Base intensity: start at 40 (instead of 20) for this high-conviction setup
        intensity = 40.0

    if override_score is None:
        intensity += path_score
    
    intensity = min(100.0, max(0.0, float(np.nan_to_num(intensity))))
    intensity_int = int(round(intensity))

    # Build reason string
    parts = [f"CTS={cts:.2f}"]
    if not np.isnan(pdd):
        parts.append(f"pdd={pdd:.1f}")
    parts.append(regime)
    if extra_parts:
        parts.extend(extra_parts)

    if intensity_int >= 80:
        reason = f"SavgolCTS {tag.value}: STRONG [{', '.join(parts)}]"
    elif intensity_int >= 65:
        reason = f"SavgolCTS {tag.value}: good [{', '.join(parts)}]"
    else:
        reason = f"SavgolCTS {tag.value}: [{', '.join(parts)}]"

    return intensity_int, {"reason": reason, "entry_tag": tag}
