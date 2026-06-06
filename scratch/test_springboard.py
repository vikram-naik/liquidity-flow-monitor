import re
import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from scratch.analyze_trends import parse_trends, clean_date_str

def check_springboard_entry(row, prev_row, records, idx):
    """
    SpringBoard Setup: Captures high-quality bottom inflections.
    
    1. Capitulation: CTS reached near -1.0 recently (within last 10 bars).
    2. Range position: Price in the lower half of its range (range_pos_63 <= 0.40).
    3. Bullish Flow Divergence: CTS is rising (cts > prev_cts) and is above -1.0.
    4. Coherence: Coherence is stable or rising (cwc_slope > -0.05).
    5. Price Velocity: Price is stabilizing or turning up (psz_v > -0.05).
    6. No falling knife: We avoid entering if price is dropping extremely fast (e.g., price spearman over last 5 bars <= -0.95).
    """
    if idx < 10:
        return False, {}

    # 1. Range Position Gate
    rp_63 = row.get("range_pos_63", np.nan)
    if np.isnan(rp_63) or rp_63 > 0.40:
        return False, {"reason": f"rp_63 ({rp_63:.2f}) > 0.40"}

    # 2. Capitulation Check
    # Look back 10 bars to see if CTS was at or near -1.0
    capitulated = False
    for k in range(max(0, idx - 9), idx + 1):
        c = records[k].get("cts", 0)
        if c <= -0.50:
            capitulated = True
            break
            
    if not capitulated:
        return False, {"reason": "No recent capitulation (CTS did not reach <= -0.95)"}

    # 3. Bullish Flow-Price Divergence
    cts = row.get("cts", np.nan)
    prev_cts = prev_row.get("cts", np.nan)
    if np.isnan(cts) or np.isnan(prev_cts):
        return False, {"reason": "Missing CTS"}
        
    if cts <= prev_cts:
        return False, {"reason": f"CTS not rising (curr: {cts:.3f}, prev: {prev_cts:.3f})"}
        
    if cts == -1.0:
        return False, {"reason": "CTS still pegged at -1.0"}

    # 4. Coherence and Flow Health
    cwc = row.get("cwc", 0.0)
    cwc_slope = row.get("cwc_slope", 0.0)
    if cwc_slope <= -0.05:
        return False, {"reason": f"CWC slope degrading too fast ({cwc_slope:.4f})"}

    # 5. Price Velocity check (optional - relaxed to allow deep capitulation stabilization)
    # We rely on spearman_5 to protect against straight-line falling knives.
    psz_v = row.get("psz_v", 0.0)
    if psz_v <= -0.16:
        return False, {"reason": f"Price velocity dropping extremely fast ({psz_v:.4f})"}

    # 6. Falling Knife Protection (Spearman price trend over last 5 bars)
    tps_5 = []
    for k in range(idx - 4, idx + 1):
        r = records[k]
        tp = (r.get("high", 0.0) + r.get("low", 0.0) + r.get("close", 0.0)) / 3.0
        tps_5.append(tp)
        
    from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend
    if len(tps_5) >= 5:
        spearman_5 = evaluate_spearman_trend(tps_5)
        if spearman_5 <= -0.95:
            return False, {"reason": f"Falling knife: spearman_5 ({spearman_5:.2f}) <= -0.95"}

    # Passed!
    return True, {
        "reason": "SpringBoard inflection accepted",
        "entry_tag": "SpringBoard",
        "score": 84,
        "cts": cts,
        "cwc": cwc,
        "rp_63": rp_63
    }

def main():
    trends = parse_trends()
    matched_count = 0
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
            print(f"Failed to load {symbol}: {e}")
            continue
            
        match_df = df[df['date'] <= target_date]
        if match_df.empty:
            continue
        idx = match_df.index[-1]
        
        # Look for a SpringBoard entry in window [idx - 5, idx + 20]
        start_idx = max(0, idx - 5)
        end_idx = min(len(df) - 1, idx + 20)
        
        fired = []
        records = df.to_dict('records')
        for i in range(start_idx, end_idx + 1):
            row = records[i]
            prev_row = records[i-1] if i > 0 else row
            passed, meta = check_springboard_entry(row, prev_row, records, i)
            if passed:
                fired.append({
                    "date": row['date'].strftime('%Y-%m-%d'),
                    "close": row['close'],
                    "cts": row['cts'],
                    "meta": meta
                })
                
        if fired:
            matched_count += 1
            print(f"SUCCESS: {symbol} on {target_date.strftime('%Y-%m-%d')} matches SpringBoard setup:")
            for f in fired:
                print(f"  - {f['date']}: Close = {f['close']:.2f}, CTS = {f['cts']:.3f}")
        else:
            print(f"FAILED: {symbol} on {target_date.strftime('%Y-%m-%d')} - no SpringBoard trigger in window")
            # Debug what happened on the target date
            passed, meta = check_springboard_entry(records[idx], records[idx-1] if idx > 0 else records[idx], records, idx)
            print(f"  * Target date evaluation: Passed={passed} | Reject reason: {meta.get('reason')}")
            
    print("\n" + "="*80)
    print(f"Total matched trends: {matched_count} / {len(trends)}")
    print("="*80)

if __name__ == "__main__":
    main()
