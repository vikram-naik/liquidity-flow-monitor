import sys
import os
import pandas as pd
import numpy as np

# Add project root to sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.divergence_engine.engine import DivergenceEngine
from src.divergence_engine.cts.causal_savgol import CausalSavgolStrategy

def test_reliance_windows():
    print("--- Testing RELIANCE with different window lengths ---")
    engine = DivergenceEngine(ticker="RELIANCE")
    result = engine.run()
    df_raw = result.ledger[["date", "cwvap", "atr_20"]]
    
    # Causal SF=5.0, W=15
    strat_15 = CausalSavgolStrategy(window_length=15)
    df_15 = strat_15.compute(df_raw.copy())
    
    # Causal SF=5.0, W=11
    strat_11 = CausalSavgolStrategy(window_length=11)
    df_11 = strat_11.compute(df_raw.copy())
    
    # Centered SF=5.0, W=11
    from src.divergence_engine.cts.savgol import SavgolStrategy
    strat_cent_11 = SavgolStrategy(window_length=11)
    df_cent_11 = strat_cent_11.compute(df_raw.copy())
    
    print(f"CTS Stats for RELIANCE (SF=5.0):")
    print(f"Causal (W=15): Min={df_15['cts'].min():.4f}, Max={df_15['cts'].max():.4f}")
    print(f"Causal (W=11): Min={df_11['cts'].min():.4f}, Max={df_11['cts'].max():.4f}")
    print(f"Centered (W=11): Min={df_cent_11['cts'].min():.4f}, Max={df_cent_11['cts'].max():.4f}")
    
    # Check clipping count
    print(f"Clipping Counts (|cts| >= 0.999):")
    print(f"Causal (W=15): {len(df_15[df_15['cts'].abs() >= 0.999])}")
    print(f"Causal (W=11): {len(df_11[df_11['cts'].abs() >= 0.999])}")
    print(f"Centered (W=11): {len(df_cent_11[df_cent_11['cts'].abs() >= 0.999])}")

if __name__ == "__main__":
    test_reliance_windows()
