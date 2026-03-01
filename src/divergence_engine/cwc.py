"""
Module 4 — Cross-Window Coherence (CWC).

Measures how consistently all anchor-window delivery cohorts move together.

Key outputs:
- **CWC**: Mean pairwise Pearson correlation of day-over-day DVL_rate
  changes across rolling 10-bar windows.
- **C_10_30, C_30_60, C_60_120**: Pairwise coherence for early-warning
  and regime-change detection.
- **CWC_delta**: 5-bar change in CWC (trend alignment acceleration).

Interpretation:
- High CWC (>0.70)  → all cohorts aligned → trend is real
- Low CWC (<0.30)   → diverging cohorts → transition or noise
- Negative CWC      → opposing cohorts → accumulation/distribution
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd

from src.divergence_engine.utils import WINDOWS


class CrossWindowCoherence:
    """Compute CWC and pairwise coherence metrics.

    Prerequisite columns: ``dvl_rate_10, dvl_rate_30, dvl_rate_60,
    dvl_rate_120`` (from :class:`DVLLedger`).
    """

    def __init__(
        self,
        windows: list[int] | None = None,
        corr_window: int = 10,
    ) -> None:
        self.windows = windows or WINDOWS
        self.corr_window = corr_window

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add CWC, pairwise coherence, and CWC_delta columns."""
        # Day-over-day changes in DVL_rate for each window
        diff_cols: list[str] = []
        for n in self.windows:
            col = f"d_dvl_rate_{n}"
            df[col] = df[f"dvl_rate_{n}"].diff()
            diff_cols.append(col)

        # 4.1 CWC — mean of all pairwise rolling Pearson correlations
        pairs = list(combinations(diff_cols, 2))
        pair_corrs: list[pd.Series] = []
        for col_a, col_b in pairs:
            corr = df[col_a].rolling(window=self.corr_window, min_periods=3).corr(
                df[col_b]
            )
            pair_corrs.append(corr)

        # Stack and compute mean
        corr_matrix = pd.concat(pair_corrs, axis=1)
        df["cwc"] = corr_matrix.mean(axis=1)

        # 4.2 Pairwise Coherence — specific pairs for interpretation
        df["c_10_30"] = df[diff_cols[0]].rolling(self.corr_window, min_periods=3).corr(
            df[diff_cols[1]]
        )
        df["c_30_60"] = df[diff_cols[1]].rolling(self.corr_window, min_periods=3).corr(
            df[diff_cols[2]]
        )
        df["c_60_120"] = df[diff_cols[2]].rolling(self.corr_window, min_periods=3).corr(
            df[diff_cols[3]]
        )

        # 4.3 CWC Trend
        df["cwc_delta"] = df["cwc"] - df["cwc"].shift(5)

        # 4.4 CWC Slope (v2.0 requirement)
        cwc_vals = df["cwc"].values.astype(float)
        slopes = np.full(len(cwc_vals), np.nan)
        x = np.arange(10, dtype=float)
        for i in range(9, len(cwc_vals)):
            y = cwc_vals[i - 9 : i + 1]
            if not np.any(np.isnan(y)):
                coeffs = np.polyfit(x, y, 1)
                slopes[i] = coeffs[0]
        df["cwc_slope"] = slopes

        # Clean up intermediate diff columns
        df.drop(columns=diff_cols, inplace=True)

        return df
