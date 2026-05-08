"""
Module 7 — Oracle Labeling.

Identifies historical peaks and troughs using look-ahead (non-causal) 
Savitzky-Golay filtering for ground-truth discovery.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter, argrelextrema

class OracleLabeler:
    """
    Identifies major structural turning points using look-ahead smoothing.
    """

    def __init__(self, window_length: int = 31, polyorder: int = 2, min_swing_pct: float = 3.0):
        """
        :param window_length: Smoothing window (must be odd). Larger = more structural.
        :param polyorder: Polynomial order for fitting.
        :param min_swing_pct: Minimum price move between extrema to qualify as a label.
        """
        if window_length % 2 == 0:
            window_length += 1
        self.window_length = window_length
        self.polyorder = polyorder
        self.min_swing_pct = min_swing_pct

    def compute_labels(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Adds 'oracle_trough' and 'oracle_peak' columns to the DataFrame.
        - oracle_trough: 1 at local minima
        - oracle_peak: 1 at local maxima
        """
        if len(df) < self.window_length:
            df["oracle_trough"] = np.nan
            df["oracle_peak"] = np.nan
            return df

        # 1. Smooth the price series using non-causal Savgol
        # We use 'Typical Price' (tp) for ground truth labeling if available, else 'close'.
        price_col = "tp" if "tp" in df.columns else "close"
        prices = df[price_col].values
        smoothed = savgol_filter(prices, self.window_length, self.polyorder, deriv=0)
        df["oracle_smooth"] = smoothed

        # 2. Find local extrema on the smoothed line
        # Use argrelextrema for efficiency
        peak_idx = argrelextrema(smoothed, np.greater)[0]
        trough_idx = argrelextrema(smoothed, np.less)[0]

        # 3. Combine and sort extrema by index to enforce alternating peak/trough
        extrema = []
        for idx in peak_idx:
            extrema.append({"idx": idx, "type": "peak", "val": smoothed[idx]})
        for idx in trough_idx:
            extrema.append({"idx": idx, "type": "trough", "val": smoothed[idx]})
        
        extrema.sort(key=lambda x: x["idx"])

        # 4. Filter for significance (alternating and min_swing)
        # We want to ensure we don't have wiggles that are too small.
        refined = []
        if extrema:
            last = extrema[0]
            refined.append(last)
            
            for i in range(1, len(extrema)):
                curr = extrema[i]
                
                # If same type, keep the one that is more extreme
                if curr["type"] == last["type"]:
                    if (curr["type"] == "peak" and curr["val"] > last["val"]) or \
                       (curr["type"] == "trough" and curr["val"] < last["val"]):
                        refined[-1] = curr
                        last = curr
                    continue
                
                # Check if the move is large enough
                price_move = abs(curr["val"] / last["val"] - 1.0) * 100.0
                if price_move >= self.min_swing_pct:
                    refined.append(curr)
                    last = curr

        # 5. Populate output columns
        df["oracle_trough"] = np.nan
        df["oracle_peak"] = np.nan
        
        for e in refined:
            if e["type"] == "trough":
                df.at[df.index[e["idx"]], "oracle_trough"] = 1.0
            else:
                df.at[df.index[e["idx"]], "oracle_peak"] = 1.0

        return df
