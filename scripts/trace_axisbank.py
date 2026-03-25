"""
Trace AXISBANK trade around 2025-11-12 to 2026-12-08
"""
import sys
from pathlib import Path
import numpy as np
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine

def trace():
    ticker = "AXISBANK"
    engine = DivergenceEngine(ticker, start_date="2025-11-01", end_date="2027-01-01")
    result = engine.run()
    df = result.ledger
    
    # Filter for interesting range
    df = df[(df["date"] >= "2026-03-01") & (df["date"] <= "2026-12-31")].copy()
    
    rows = []
    for _, r in df.iterrows():
        close = r.get("close", np.nan)
        cts = r.get("cts", np.nan)
        bt = r.get("cts_buy_threshold", np.nan)
        st = r.get("cts_sell_threshold", np.nan)
        psz = r.get("price_slope_z", np.nan)
        cwvap = r.get("cwvap", np.nan)
        
        # Check ceiling
        ceiling = 0.98
        is_ceiling = cts >= ceiling
        
        rows.append({
            "Date": str(r["date"])[:10],
            "Close": round(close, 1),
            "CWVAP": round(cwvap, 1) if not np.isnan(cwvap) else "",
            "Above": "Y" if close > cwvap else "",
            "CTS": round(cts, 3),
            "Ceil": "HIT" if is_ceiling else "",
            "PSZ": round(psz, 3),
            "ST": round(st, 3) if not np.isnan(st) else "",
        })
    
    # Print only every 5th or 10th row if too many, or just interesting ones
    # For now, print all to see where it hits
    print(f"\n=== {ticker} Trace ===\n")
    print(tabulate(rows, headers="keys", tablefmt="github"))

if __name__ == "__main__":
    trace()
