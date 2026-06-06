import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine

from scratch.analyze_trends import parse_trends, clean_date_str

def main():
    trends = parse_trends()
    # Let's inspect a few representatives:
    # 1. ASIANPAINTS (2-Mar-2026)
    # 2. BAJAJ-AUTO (10-Dec-2025 or 13-Mar-2026)
    # 3. BEL (16-Dec-2025)
    # 4. COALINDIA (25-Nov-2025)
    # 5. ETERNAL (12-Mar-2026)
    # 6. TRENT (19-Mar-2026)
    
    inspect_symbols = ["ASIANPAINTS", "BAJAJ-AUTO", "BEL", "COALINDIA", "ETERNAL", "TRENT"]
    
    for symbol, raw_date in trends:
        if symbol not in inspect_symbols:
            continue
            
        target_date = clean_date_str(raw_date)
        print(f"\n======================================================================")
        print(f"INSPECTING: {symbol} around {target_date.strftime('%Y-%m-%d')}")
        print(f"======================================================================")
        
        try:
            engine = DivergenceEngine(symbol)
            res = engine.run()
            df = res.ledger
            df['date'] = pd.to_datetime(df['date'])
        except Exception as e:
            print(f"Failed to load engine for {symbol}: {e}")
            continue
            
        match_df = df[df['date'] <= target_date]
        if match_df.empty:
            continue
            
        idx = match_df.index[-1]
        
        # We want to print a window from idx-5 to idx+15
        start_idx = max(0, idx - 5)
        end_idx = min(len(df) - 1, idx + 15)
        
        rows = []
        for i in range(start_idx, end_idx + 1):
            r = df.loc[i]
            # check if a signal would fire on this bar
            close = r.get("close", np.nan)
            cwvap = r.get("cwvap", np.nan)
            cwvap_dist = (close - cwvap) / cwvap * 100.0 if not np.isnan(cwvap) and cwvap > 0 else np.nan
            
            rows.append({
                "Index": i,
                "Date": r['date'].strftime('%Y-%m-%d'),
                "Close": f"{close:.2f}",
                "CTS": f"{r.get('cts', np.nan):.3f}",
                "CTS_Slope": f"{r.get('cts_slope', np.nan):.4f}",
                "CTS_Accel": f"{r.get('cts_accel', np.nan):.4f}",
                "CWC": f"{r.get('cwc', np.nan):.3f}",
                "CWC_Slope": f"{r.get('cwc_slope', np.nan):.4f}",
                "FAS": f"{r.get('fas', np.nan):.3f}",
                "PSZ_V": f"{r.get('psz_v', np.nan):.4f}",
                "PSZ": f"{r.get('price_slope_z', np.nan):.3f}",
                "PDD_120": f"{r.get('pdd_120', np.nan):.2f}",
                "CWV_Dist%": f"{cwvap_dist:.1f}%",
                "RP_63": f"{r.get('range_pos_63', np.nan):.2f}",
                "Regime": r.get("regime", "")
            })
            
        print(tabulate(rows, headers="keys", tablefmt="simple"))

if __name__ == "__main__":
    main()
