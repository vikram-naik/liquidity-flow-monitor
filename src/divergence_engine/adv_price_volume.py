import numpy as np
import pandas as pd

class AdvancedPriceVolume:
    """Computes advanced price-volume indicators for institutional footprinting and efficiency."""
    
    def __init__(self, lookback: int = 20, swing_lookback: int = 60) -> None:
        self.lookback = lookback
        self.swing_lookback = swing_lookback

    def compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        df = self.compute_dv_shock(df)
        df = self.compute_esr(df)
        df = self.compute_swing_anchored_dvwap(df)
        return df

    def compute_dv_shock(self, df: pd.DataFrame) -> pd.DataFrame:
        """Computes 20-bar rolling z-score of delivery_qty to identify institutional liquidity shocks."""
        rolling_mean = df["delivery_qty"].rolling(window=self.lookback, min_periods=5).mean()
        rolling_std = df["delivery_qty"].rolling(window=self.lookback, min_periods=5).std()
        df["dv_shock"] = np.where(rolling_std > 0, (df["delivery_qty"] - rolling_mean) / rolling_std, 0.0)
        df["dv_shock"] = df["dv_shock"].fillna(0.0)
        return df

    def compute_esr(self, df: pd.DataFrame) -> pd.DataFrame:
        """Computes Relative Volume Spread Efficiency (ESR).
        ESR = Log(High/Low) / (Volume / 20-period Moving Average of Volume)
        """
        spread = np.log(df["high"] / df["low"].replace(0, 1e-6))
        vol_mean = df["volume"].rolling(window=self.lookback, min_periods=1).mean()
        relative_volume = np.where(vol_mean > 0, df["volume"] / vol_mean, 1.0)
        df["esr"] = np.where(relative_volume > 0, spread / relative_volume, 0.0)
        df["esr"] = df["esr"].fillna(0.0)
        return df

    def compute_swing_anchored_dvwap(self, df: pd.DataFrame) -> pd.DataFrame:
        """Computes Delivery-Volume Weighted VWAP anchored dynamically to the 60-day price trough."""
        n = len(df)
        sdvwap = np.zeros(n)
        
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        tp_del = tp * df["delivery_qty"]
        
        for i in range(n):
            start_lookback = max(0, i - self.swing_lookback + 1)
            trough_idx = df["low"].iloc[start_lookback : i + 1].idxmin()
            
            del_qty_sum = df["delivery_qty"].iloc[trough_idx : i + 1].sum()
            tp_del_sum = tp_del.iloc[trough_idx : i + 1].sum()
            
            if del_qty_sum > 0:
                sdvwap[i] = tp_del_sum / del_qty_sum
            else:
                sdvwap[i] = tp.iloc[i]
                
        df["sdvwap"] = sdvwap
        return df
