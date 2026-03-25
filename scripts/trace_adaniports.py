"""
Trace ADANIPORTS trade around 2026-01-21
"""
import sys
from pathlib import Path
import numpy as np
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine

def trace():
    ticker = "ADANIPORTS"
    engine = DivergenceEngine(ticker, start_date="2025-10-01", end_date="2026-03-15")
    result = engine.run()
    df = result.ledger
    
    df = df[df["date"] >= "2026-01-15"].copy()
    
    rows = []
    for _, r in df.iterrows():
        close = r.get("close", np.nan)
        cts = r.get("cts", np.nan)
        bt = r.get("cts_buy_threshold", np.nan)
        psz = r.get("price_slope_z", np.nan)
        psz_v = r.get("psz_v", np.nan)
        cwvap = r.get("cwvap", np.nan)
        cwvap_dist = (close/cwvap - 1)*100 if not np.isnan(cwvap) and cwvap > 0 else np.nan
        
        rows.append({
            "Date": str(r["date"])[:10],
            "Close": round(close, 1),
            "CWD%": round(cwvap_dist, 1),
            "CTS": round(cts, 3),
            "BT": round(bt, 3),
            "CTS>BT": "Y" if cts > bt else "",
            "PSZ": round(psz, 3),
            "PSZv": round(psz_v, 4) if not np.isnan(psz_v) else "",
        })
    
    print(f"\n=== {ticker} Trade Trace ===\n")
    print(tabulate(rows, headers="keys", tablefmt="github"))

if __name__ == "__main__":
    trace()
