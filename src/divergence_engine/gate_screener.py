"""
Module 8 — Quantitative Gate Screener.

Performs vectorized delivery outlier truncation, nested rolling Z-score
calculations, delivery volume velocity estimation, and calculates the 
multi-factor S_total composite score. Categorizes candidate equities 
into the four approved setups: SIAB, CDMA, CLFR, and ISP.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class GateScreener:
    """Vectorized EOD Quantitative Gate Screener."""

    def __init__(self, lookback_windows: tuple[int, ...] = (5, 20, 60, 252)) -> None:
        self.windows = lookback_windows

    def compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute all quantitative gate indicators, scores, and setups."""
        if df.empty or len(df) < 5:
            return df

        df = self.compute_outlier_truncation(df)
        df = self.compute_nested_zscores(df)
        df = self.compute_delivery_velocity(df)
        df = self.compute_composite_scores(df)
        df = self.classify_gate_setups(df)
        return df

    def compute_outlier_truncation(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply statistical winsorization (Block Deal Filter) on delivery quantity.
        
        Truncates delivery_qty to 5.0x rolling 20-period median to prevent
        discrete pre-negotiated crossings from polluting the historical baseline.
        """
        rolling_median = df["delivery_qty"].rolling(window=20, min_periods=1).median()
        df["clean_delivery"] = np.minimum(df["delivery_qty"], 5.0 * rolling_median)
        return df

    def compute_nested_zscores(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute rolling Z-scores of the cleaned delivery series across nested horizons."""
        for n in self.windows:
            mean = df["clean_delivery"].rolling(window=n, min_periods=min(n, 5)).mean()
            std = df["clean_delivery"].rolling(window=n, min_periods=min(n, 5)).std()
            # Avoid division by zero
            df[f"z_{n}"] = np.where(std > 0, (df["clean_delivery"] - mean) / std, 0.0)
            df[f"z_{n}"] = df[f"z_{n}"].fillna(0.0)
        return df

    def compute_delivery_velocity(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute delivery volume velocity (rate of change of institutional flow EMA)."""
        for n in self.windows:
            std = df["clean_delivery"].rolling(window=n, min_periods=min(n, 5)).std()
            ema = df["clean_delivery"].ewm(span=n, adjust=False).mean()
            ema_diff = ema - ema.shift(5)
            df[f"vv_{n}"] = np.where(std > 0, ema_diff / std, 0.0)
            df[f"vv_{n}"] = df[f"vv_{n}"].fillna(0.0)
        return df

    def compute_composite_scores(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate multi-factor probabilistic scoring matrix (S_total)."""
        # 1. Volume Score (S_V)
        def phi(z_series: pd.Series) -> pd.Series:
            return np.clip((z_series / 3.0) * 100, 0, 100)

        df["s_volume"] = (
            0.15 * phi(df["z_5"]) +
            0.35 * phi(df["z_20"]) +
            0.30 * phi(df["z_60"]) +
            0.20 * phi(df["z_252"])
        )

        # 2. Price Score (S_P)
        mfm_score = (df["mfm"] + 1.0) / 2.0 * 100
        
        # Symmetrical range score (max 100 at center 0.5)
        rp_score = 100.0 * (1.0 - (df["range_pos_252"] - 0.5).abs() * 2.0)
        
        esr_rolling_median = df["esr"].rolling(window=20, min_periods=1).median()
        esr_score = np.where(
            esr_rolling_median > 0, 
            np.clip(df["esr"] / esr_rolling_median * 50.0, 0, 100), 
            50.0
        )
        
        df["s_price"] = 0.40 * mfm_score + 0.30 * rp_score + 0.30 * esr_score

        # 3. Trend Score (S_T)
        cwc_score = np.clip(df["cwc"] * 100.0, 0, 100)
        psz_score = np.clip((df["psz_v"] + 1.0) * 50.0, 0, 100)
        
        df["s_trend"] = 0.50 * cwc_score + 0.50 * psz_score

        # 4. Total Composite Score (S_total)
        df["s_total"] = 0.40 * df["s_volume"] + 0.35 * df["s_price"] + 0.25 * df["s_trend"]
        return df

    def classify_gate_setups(self, df: pd.DataFrame) -> pd.DataFrame:
        """Vectorized classification of candidates into SIAB, CDMA, CLFR, and ISP setups."""
        n_rows = len(df)
        setup_arr = np.full(n_rows, "None", dtype=object)
        
        # Calculate Relative Total Volume: total volume relative to its 20-day mean
        vol_mean = df["volume"].rolling(window=20, min_periods=1).mean()
        rdv_vol = np.where(vol_mean > 0, df["volume"] / vol_mean, 1.0)
        
        # Extract series
        rp = df["range_pos_252"]
        tightness = df.get("base_tightness", pd.Series(np.ones(n_rows)))
        z_252 = df["z_252"]
        z_60 = df["z_60"]
        z_20 = df["z_20"]
        z_5 = df["z_5"]
        dp = df["delivery_pct"] # stored as percentage 0 to 100
        close = df["close"]
        cwvap = df["cwvap"]
        sdvwap = df.get("sdvwap", df["close"])
        cwc = df["cwc"]
        cts = df["cts"]
        regime = df["regime"]
        mfm = df["mfm"]
        esr = df["esr"]
        esr_med = df["esr"].rolling(window=20, min_periods=1).median()
        cwc_slope = df.get("cwc_slope", pd.Series(np.zeros(n_rows)))

        for i in range(n_rows):
            # Setup 1: SIAB
            is_siab = (
                (rp.iloc[i] < 0.35) and
                (tightness.iloc[i] < 0.25) and
                (z_252.iloc[i] >= 1.5) and
                (z_60.iloc[i] >= 2.0) and
                (dp.iloc[i] >= 45.0) and
                (1.0 <= rdv_vol[i] <= 2.0) and
                (z_5.iloc[i] >= 1.5) and
                (close.iloc[i] >= cwvap.iloc[i]) and
                (cwc.iloc[i] > 0) and
                (cts.iloc[i] >= -0.2)
            )
            
            # Setup 2: CDMA
            is_cdma = (
                (rp.iloc[i] >= 0.80) and
                (rdv_vol[i] >= 2.5) and
                (dp.iloc[i] >= 35.0) and
                (z_5.iloc[i] >= 1.5) and
                (z_20.iloc[i] >= 2.0) and
                (mfm.iloc[i] >= 0.60) and
                (esr_med.iloc[i] > 0 and esr.iloc[i] >= 1.5 * esr_med.iloc[i]) and
                (close.iloc[i] > cwvap.iloc[i]) and
                (cts.iloc[i] >= 0.5) and
                (cwc.iloc[i] >= 0.60) and
                (cwc_slope.iloc[i] > 0)
            )

            # Setup 3: CLFR
            is_clfr = (
                (rp.iloc[i] <= 0.15) and
                (close.iloc[i] <= sdvwap.iloc[i]) and
                (rdv_vol[i] >= 3.5) and
                (dp.iloc[i] >= 40.0) and
                (z_5.iloc[i] >= 1.7) and
                (esr_med.iloc[i] > 0 and esr.iloc[i] < 0.5 * esr_med.iloc[i]) and
                (mfm.iloc[i] >= 0.20)
            )

            # Setup 4: ISP
            # Support entry bar conditions
            is_isp_entry = (
                (rp.iloc[i] >= 0.60) and
                (regime.iloc[i] in ("strongup", "weakup")) and
                (rdv_vol[i] >= 1.5) and
                (dp.iloc[i] >= 40.0) and
                (abs(close.iloc[i] - cwvap.iloc[i]) / cwvap.iloc[i] <= 0.015) and
                (z_5.iloc[i] >= 1.2) and
                (mfm.iloc[i] >= 0.0)
            )
            # Pullback volume check (preceding bar volume was dry)
            has_dry_pullback = False
            if i > 0:
                has_dry_pullback = (rdv_vol[i - 1] < 1.0)
            
            is_isp = is_isp_entry and has_dry_pullback

            if is_siab:
                setup_arr[i] = "SIAB"
            elif is_cdma:
                setup_arr[i] = "CDMA"
            elif is_clfr:
                setup_arr[i] = "CLFR"
            elif is_isp:
                setup_arr[i] = "ISP"

        df["gate_setup"] = setup_arr
        df["gate_signal"] = np.where((df["s_total"] >= 80.0) & (df["gate_setup"] != "None"), 1, 0)
        return df
