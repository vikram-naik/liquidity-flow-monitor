import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine

engine = DivergenceEngine("TRENT")
ledger = engine.run().ledger
ledger['date_str'] = ledger['date'].astype(str).str[:10]
idx = ledger[ledger['date_str'] == "2025-01-31"].index
if not idx.empty:
    sig_idx = idx[0] - 1  # signal day is the day before entry day
    print(f"Signal Date: {ledger.iloc[sig_idx]['date_str']}")
    
    print("PSZ V Lookback:")
    for n in range(10, -1, -1):
        row = ledger.iloc[sig_idx - n]
        print(f"  {row['date_str']}: {row['psz_v']:.6f}")
