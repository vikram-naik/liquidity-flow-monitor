
import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine

sym = "FEDERALBNK"
df = DivergenceEngine(sym).run().ledger
df['date'] = df['date'].astype(str).str[:10]
mask = (df['date'] >= '2025-10-01') & (df['date'] <= '2025-10-15')

for i, row in df[mask].iterrows():
    prev = df.loc[i-1] if i>0 else row
    cts_acc = row.get("cts_accel", 0)
    psz_v = row.get("psz_v", 0)
    rp252 = row.get("range_pos_252", 0)
    
    is_building = cts_acc > 0.015
    is_explosive = cts_acc > 0.04 or psz_v > 0.08
    is_not_peak = rp252 < 0.85
    
    print(f"{row['date']} | acc:{cts_acc:.3f} | psz_v:{psz_v:.3f} | rp:{rp252:.2f} | Bld:{is_building} Exp:{is_explosive} Pk:{is_not_peak}")
