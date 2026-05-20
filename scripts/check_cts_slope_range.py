
import pandas as pd
import numpy as np
from src.divergence_engine.engine import DivergenceEngine

def check_cts_slope_range(symbol):
    engine = DivergenceEngine(ticker=symbol)
    result = engine.run()
    df = result.ledger
    if "cts_slope" in df.columns:
        valid_slopes = df["cts_slope"].dropna()
        print(f"Symbol: {symbol}")
        print(f"  Min CTS Slope: {valid_slopes.min():.4f}")
        print(f"  Max CTS Slope: {valid_slopes.max():.4f}")
        print(f"  Mean CTS Slope: {valid_slopes.mean():.4f}")
        print(f"  Std CTS Slope: {valid_slopes.std():.4f}")
        print(f"  Count below -0.187: {(valid_slopes < -0.187).sum()} / {len(valid_slopes)}")
    else:
        print(f"Symbol: {symbol} - No cts_slope found")

if __name__ == "__main__":
    symbols = ["RELIANCE", "TCS", "HDFCBANK", "INFY"]
    for sym in symbols:
        try:
            check_cts_slope_range(sym)
        except Exception as e:
            print(f"Error checking {sym}: {e}")
