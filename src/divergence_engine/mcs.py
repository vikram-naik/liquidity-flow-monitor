"""
Module 5 — Money Composite Score (MCS).

Quantifies the alignment between price action and delivery participation
using rolling Pearson correlations.

Key outputs:
- **MCS**: 30-day rolling Pearson(TP, RDV) — price vs delivery direction.
- **MCS_MFM**: 30-day rolling Pearson(MFM_TP, RDV) — intraday pressure
  vs delivery commitment.
- **MCS_delta**: change in MCS over multiple windows (e.g. mcs_delta_5d).
- **MCS_composite**: 0.6 × MCS + 0.4 × MCS_MFM — primary money-flow
  health score (range: −1 to +1).
"""

from __future__ import annotations

import pandas as pd


class MoneyCompositeScore:
    """Compute MCS, MCS_MFM, and the composite signal.

    Prerequisite columns: ``tp, rdv, mfm_tp``
    (from :class:`BaseCalculator`).
    """

    def __init__(self, window: int = 30, delta_windows: list[int] | None = None) -> None:
        self.window = window
        self.delta_windows = delta_windows or [5]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add MCS, MCS_MFM, MCS_delta, and MCS_composite columns."""
        w = self.window

        # 5.1 MCS — rolling Pearson(TP, RDV)
        df["mcs"] = df["tp"].rolling(window=w, min_periods=10).corr(df["rdv"])

        # 5.2 MCS_MFM — rolling Pearson(MFM_TP, RDV)
        df["mcs_mfm"] = df["mfm_tp"].rolling(window=w, min_periods=10).corr(df["rdv"])

        # 5.3 MCS_deltas — configurable rates of change
        for w_delta in self.delta_windows:
            df[f"mcs_delta_{w_delta}d"] = df["mcs"] - df["mcs"].shift(w_delta)

        # 5.4 MCS_composite — blended signal
        df["mcs_composite"] = 0.6 * df["mcs"].fillna(0) + 0.4 * df["mcs_mfm"].fillna(0)

        # 5.5 MCS_composite Slope (v2.0 requirement)
        import numpy as np
        mcs_vals = df["mcs_composite"].values.astype(float)
        slopes = np.full(len(mcs_vals), np.nan)
        x = np.arange(10, dtype=float)
        for i in range(9, len(mcs_vals)):
            y = mcs_vals[i-9:i+1]
            if not np.any(np.isnan(y)):
                coeffs = np.polyfit(x, y, 1)
                slopes[i] = coeffs[0]
        df["mcs_composite_slope"] = slopes

        return df
