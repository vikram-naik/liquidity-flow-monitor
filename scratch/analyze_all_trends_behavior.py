import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine
from scratch.analyze_trends import parse_trends, clean_date_str

def main():
    trends = parse_trends()
    results = []
    
    for symbol, raw_date in trends:
        if symbol == "ASIANPAINTS":
            symbol = "ASIANPAINT"
        target_date = clean_date_str(raw_date)
        
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
        
        # Look at window of [idx - 10, idx + 10]
        start_idx = max(0, idx - 10)
        end_idx = min(len(df) - 1, idx + 10)
        
        window_df = df.iloc[start_idx:end_idx+1].copy()
        
        print(f"\n========================================================")
        print(f"SYMBOL: {symbol} | Target Date: {target_date.strftime('%Y-%m-%d')} (index {idx})")
        print(f"========================================================")
        
        # Let's inspect behavior of indicators in this window
        for i in range(start_idx, end_idx + 1):
            r = df.iloc[i]
            date_str = r['date'].strftime('%Y-%m-%d')
            close = r.get('close', np.nan)
            cts = r.get('cts', np.nan)
            cts_slope = r.get('cts_slope', np.nan)
            cts_accel = r.get('cts_accel', np.nan)
            cwc = r.get('cwc', np.nan)
            cwc_slope = r.get('cwc_slope', np.nan)
            fas = r.get('fas', np.nan)
            psz = r.get('price_slope_z', np.nan)
            psz_v = r.get('psz_v', np.nan)
            pdd_120 = r.get('pdd_120', np.nan)
            rp_10 = r.get('range_pos_10', np.nan)
            rp_63 = r.get('range_pos_63', np.nan)
            base_t = r.get('base_tightness', np.nan)
            regime = r.get('regime', '')
            
            prefix = "--> " if i == idx else "    "
            print(f"{prefix}{date_str} (idx {i:4d}): Close: {close:8.2f} | CTS: {cts:6.3f} | CTS_Sl: {cts_slope:7.4f} | CTS_Ac: {cts_accel:7.4f} | CWC: {cwc:5.3f} | FAS: {fas:6.3f} | PSZ_V: {psz_v:7.4f} | PSZ: {psz:6.3f} | PDD_120: {pdd_120:6.2f} | RP_63: {rp_63:5.2f} | BaseT: {base_t:5.3f} | {regime}")

if __name__ == "__main__":
    main()
