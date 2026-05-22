import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine

def main():
    engine = DivergenceEngine("TECHM")
    res = engine.run()
    ledger = res.ledger
    
    if ledger is None or ledger.empty:
        print("Empty ledger for TECHM")
        return
        
    ledger['date_str'] = ledger['date'].astype(str).str[:10]
    
    # Filter for the relevant window: March 1, 2026 to May 20, 2026
    df = ledger[(ledger['date_str'] >= '2026-03-01') & (ledger['date_str'] <= '2026-05-20')]
    
    print("Columns in ledger:", [c for c in df.columns if 'cts' in c or 'st' in c or 'close' in c or 'date' in c])
    
    print("\nTECHM Ledger around 17-Apr-2026:")
    print(f"{'Date':<10} | {'Close':>8} | {'CTS':>8} | {'CTS_ST':>8} | {'PRT':>8} | {'PRT_BT':>8} | {'PRT_ST':>8} | {'Regime'}")
    print("-" * 90)
    for _, row in df.iterrows():
        date = row['date_str']
        close = row.get('close', np.nan)
        cts = row.get('cts', np.nan)
        cts_st = row.get('cts_sell_threshold', np.nan)
        prt = row.get('prt', np.nan)
        prt_bt = row.get('prt_buy_threshold', np.nan)
        prt_st = row.get('prt_sell_threshold', np.nan)
        regime = row.get('regime', 'N/A')
        
        star = " *** " if date == "2026-04-17" else ""
        print(f"{date:<10} | {close:>8.2f} | {cts:>8.4f} | {cts_st:>8.4f} | {prt:>8.4f} | {prt_bt:>8.4f} | {prt_st:>8.4f} | {regime}{star}")

if __name__ == "__main__":
    main()
