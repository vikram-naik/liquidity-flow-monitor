import os
import sys
sys.path.insert(0, os.path.abspath('.'))

from src.divergence_engine.engine import DivergenceEngine
import pandas as pd

engine = DivergenceEngine("TATASTEEL")
df = engine.run().ledger
try:
    idx = df.index[df['date'] == pd.to_datetime("2024-07-05")].tolist()[0]
    for i in range(idx-10, idx+1):
        row = df.iloc[i]
        print(f"Date: {row['date'].strftime('%Y-%m-%d')}, FAS: {row['fas']:.4f}, CTS: {row['cts']:.4f}, PRT: {row.get('prt_slope', 0):.4f}, ACCEL: {row.get('cts_accel', 0):.4f}")
except Exception as e:
    print(e)
