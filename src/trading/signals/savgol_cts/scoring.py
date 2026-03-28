"""Shared intensity scoring for all SavgolCTS entry paths.

Intensity is a 0–100 score combining coherence, PDD, and regime bonuses.
The score is used by the simulation loop to rank signal quality.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.enums import EntryTag


def compute_intensity(
    cts: float,
    coh: float,
    pdd: float,
    regime: str,
    tag: EntryTag,
    extra_parts: list[str] | None = None,
) -> tuple[int, dict]:
    """Score entry quality and build a human-readable reason string.

    Returns:
        (intensity_int, meta_dict) where meta_dict contains ``reason``
        and ``entry_tag`` keys.
    """
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
