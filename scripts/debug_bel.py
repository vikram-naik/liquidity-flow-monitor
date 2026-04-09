
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
mask = (df['date'] >= '2025-12-10') & (df['date'] <= '2025-12-25')
cols = ['date', 'close', 'rdv_slope_z', 'price_slope_z', 'cwvap', 'cts', 'cts_slope', 'cts_accel', 'psz_v', 'range_pos_252', 'range_pos_63', 'range_pos_10']
print(df[mask][cols].to_string(index=False))
