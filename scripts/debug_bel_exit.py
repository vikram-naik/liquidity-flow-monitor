
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine

sym = "BEL"
engine = DivergenceEngine(sym)
result = engine.run()
df = result.ledger
df['date'] = df['date'].astype(str).str[:10]
mask = (df['date'] >= '2026-01-10') & (df['date'] <= '2026-01-25')
cols = ['date', 'close', 'price_slope_z', 'cwvap']
print(df[mask][cols].to_string(index=False))
