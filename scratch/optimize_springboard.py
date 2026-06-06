import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend
from scratch.analyze_trends import parse_trends, clean_date_str

def check_springboard_custom(row, prev_row, records, idx, cap_thresh, cap_lookback, rp_max, cwc_slope_min, psz_v_min, spearman_min):
    if idx < cap_lookback:
        return False

    # 1. Range Position Gate
    rp_63 = row.get("range_pos_63", np.nan)
    if np.isnan(rp_63) or rp_63 > rp_max:
        return False

    # 2. Capitulation Check
    capitulated = False
    for k in range(max(0, idx - cap_lookback + 1), idx + 1):
        c = records[k].get("cts", np.nan)
        if not np.isnan(c) and c <= cap_thresh:
            capitulated = True
            break
            
    if not capitulated:
        return False

    # 3. Bullish Flow-Price Divergence
    cts = row.get("cts", np.nan)
    prev_cts = prev_row.get("cts", np.nan)
    if np.isnan(cts) or np.isnan(prev_cts):
        return False
        
    if cts <= prev_cts:
        return False
        
    if cts == -1.0:
        return False

    # 4. Coherence Health Gate
    cwc_slope = row.get("cwc_slope", np.nan)
    if not np.isnan(cwc_slope) and cwc_slope <= cwc_slope_min:
        return False

    # 5. Price Velocity Stabilization
    psz_v = row.get("psz_v", np.nan)
    if not np.isnan(psz_v) and psz_v <= psz_v_min:
        return False

    # 6. Falling Knife Protection
    tps_5 = []
    for k in range(idx - 4, idx + 1):
        r = records[k]
        tp = (r.get("high", 0.0) + r.get("low", 0.0) + r.get("close", 0.0)) / 3.0
        tps_5.append(tp)
        
    if len(tps_5) >= 5:
        spearman_5 = evaluate_spearman_trend(tps_5)
        if spearman_5 <= spearman_min:
            return False
    else:
        return False

    return True

def check_trends_match(df, target_date, cap_thresh, cap_lookback, rp_max, cwc_slope_min, psz_v_min, spearman_min):
    df['date'] = pd.to_datetime(df['date'])
    match_df = df[df['date'] <= target_date]
    if match_df.empty:
        return False
    idx = match_df.index[-1]
    
    start_idx = max(0, idx - 5)
    end_idx = min(len(df) - 1, idx + 20)
    
    records = df.to_dict('records')
    for i in range(start_idx, end_idx + 1):
        row = records[i]
        prev_row = records[i-1] if i > 0 else row
        if check_springboard_custom(
            row, prev_row, records, i,
            cap_thresh, cap_lookback, rp_max, cwc_slope_min, psz_v_min, spearman_min
        ):
            return True
    return False

def main():
    trends = parse_trends()
    
    # Load and cache engines for Nifty 50 stocks
    symbols = list(set([t[0] for t in trends]))
    symbols = [s if s != "ASIANPAINTS" else "ASIANPAINT" for s in symbols]
    
    print("Caching stock data...")
    cache = {}
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            cache[sym] = res.ledger.copy()
        except Exception as e:
            pass
            
    # Candidates to analyze in detail
    candidates = [
        (-0.50, 0.40, -0.95),  # Current
        (-0.60, 0.40, -0.95),
        (-0.70, 0.40, -0.95),
        (-0.75, 0.40, -0.95),
        (-0.80, 0.40, -0.95),
        (-0.90, 0.40, -0.95),
    ]
    
    for cap_thresh, rp_max, spearman_min in candidates:
        cap_lookback = 10
        cwc_slope_min = -0.05
        psz_v_min = -0.16
        
        matched_trends = []
        missed_trends = []
        for symbol, raw_date in trends:
            sym_clean = "ASIANPAINT" if symbol == "ASIANPAINTS" else symbol
            if sym_clean not in cache:
                continue
            target_date = clean_date_str(raw_date)
            df = cache[sym_clean]
            matched = check_trends_match(df, target_date, cap_thresh, cap_lookback, rp_max, cwc_slope_min, psz_v_min, spearman_min)
            if matched:
                matched_trends.append(f"{symbol} ({raw_date})")
            else:
                missed_trends.append(f"{symbol} ({raw_date})")
                
        print(f"\n========================================================")
        print(f"Candidate: Cap Thresh={cap_thresh} | RP Max={rp_max}")
        print(f"========================================================")
        print(f"Match Rate: {len(matched_trends)} / {len(trends)} ({len(matched_trends)/len(trends)*100:.1f}%)")
        print(f"Missed Trends ({len(missed_trends)}):")
        for mt in missed_trends:
            # Let's see if we can find the reason
            print(f"  - {mt}")

if __name__ == "__main__":
    main()
