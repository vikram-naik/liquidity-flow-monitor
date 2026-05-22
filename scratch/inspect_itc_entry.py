import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine

def main():
    sym = "ITC"
    engine = DivergenceEngine(sym)
    df = engine.run().ledger
    
    df['date_dt'] = pd.to_datetime(df['date'])
    mask = (df['date_dt'] >= '2025-10-01') & (df['date_dt'] <= '2025-11-25')
    df_filtered = df.loc[mask].copy()
    
    print("Date       | Close  | CWVAP  | Dist%  | CTS    | CTS_Slope | PSZ_V  | FAS    | CWC    | CWC_Slope | Volume")
    print("-" * 115)
    for _, row in df_filtered.iterrows():
        close = row.get("close", np.nan)
        cwvap = row.get("cwvap", np.nan)
        dist = (close - cwvap) / cwvap * 100.0 if cwvap > 0 else np.nan
        print(f"{str(row['date'])[:10]} | {close:6.2f} | {cwvap:6.2f} | {dist:5.2f}% | {row.get('cts', np.nan):6.3f} | {row.get('cts_slope', np.nan):9.6f} | {row.get('psz_v', np.nan):6.3f} | {row.get('fas', np.nan):6.3f} | {row.get('cwc', np.nan):6.3f} | {row.get('cwc_slope', np.nan):9.6f} | {row.get('volume', 0.0):.0f}")

if __name__ == "__main__":
    main()
