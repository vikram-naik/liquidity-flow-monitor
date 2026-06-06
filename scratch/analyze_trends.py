import re
import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig

TRENDS_FILE = Path(__file__).resolve().parent.parent / "output" / "trends.txt"

def parse_trends():
    trends = []
    # Regular expression to match symbol and date
    # Format: 1: ASIANPAINTS dt: 2-Mar-2026 onwards.
    # or 2: AXISBANK, dt: 19-Mar-2026 onwards
    pattern = re.compile(r'\d+:\s+([A-Z0-9\-]+)[,\s]*dt[:\s]+([\d]+-[A-Za-z]+-\d{4})')
    with open(TRENDS_FILE, 'r') as f:
        for line in f:
            match = pattern.search(line)
            if match:
                symbol = match.group(1).strip()
                date_str = match.group(2).strip()
                trends.append((symbol, date_str))
            else:
                # Try a looser pattern
                # e.g., 22: POWERGRID, dt 16-Jan-2026 onwards
                match_loose = re.search(r'([A-Z0-9\-]+)[,\s]+dt\s+([\d]+-[A-Za-z]+-\d{4})', line)
                if match_loose:
                    symbol = match_loose.group(1).strip()
                    date_str = match_loose.group(2).strip()
                    trends.append((symbol, date_str))
                else:
                    # Let's try matching Sepc or other misspellings
                    match_typo = re.search(r'([A-Z0-9\-]+)[,\s]+dt[:\s]+([\d]+-[A-Za-z]+-\d{4})', line)
                    if match_typo:
                        symbol = match_typo.group(1).strip()
                        date_str = match_typo.group(2).strip()
                        trends.append((symbol, date_str))
    return trends

def clean_date_str(date_str):
    # Fix typos like 'Sepc' -> 'Sep'
    date_str = date_str.replace("Sepc", "Sep")
    try:
        return pd.to_datetime(date_str, format="%d-%b-%Y")
    except Exception as e:
        print(f"Error parsing date string '{date_str}': {e}")
        return pd.to_datetime(date_str)

def main():
    trends = parse_trends()
    print(f"Parsed {len(trends)} trends from output/trends.txt:")
    for sym, dt in trends:
        print(f"  {sym} on {dt}")
        
    signal = SignalFactory.get_signal("savgol_cts")
    entry_cfg = SavgolCTSEntryConfig()
    
    results = []
    
    for symbol, raw_date in trends:
        # Map ASIANPAINTS to ASIANPAINT
        if symbol == "ASIANPAINTS":
            symbol = "ASIANPAINT"
        # Check if the symbol is in the database
        target_date = clean_date_str(raw_date)
        print(f"\nProcessing {symbol} for target date {target_date.strftime('%Y-%m-%d')}...")
        
        try:
            engine = DivergenceEngine(symbol)
            res = engine.run()
            df = res.ledger
            df['date'] = pd.to_datetime(df['date'])
        except Exception as e:
            print(f"Failed to load engine for {symbol}: {e}")
            continue
            
        # Find index of the target date or the closest date before/on it
        match_df = df[df['date'] <= target_date]
        if match_df.empty:
            print(f"No dates found on or before {target_date} for {symbol}")
            continue
        
        idx = match_df.index[-1]
        actual_date = df.loc[idx, 'date']
        
        # Let's inspect a window of 5 bars before the target date to 5 bars after
        start_idx = max(0, idx - 5)
        end_idx = min(len(df) - 1, idx + 5)
        
        # Check if any entry signal fired in this window
        fired_in_window = []
        for i in range(start_idx, end_idx + 1):
            row = df.loc[i].to_dict()
            prev_row = df.loc[i-1].to_dict() if i > 0 else row
            passed, intensity, meta = signal.check_entry(row, prev_row, entry_cfg, df.to_dict('records'), i)
            if passed:
                fired_in_window.append({
                    "idx": i,
                    "date": row['date'].strftime('%Y-%m-%d'),
                    "tag": meta.get("entry_tag"),
                    "reason": meta.get("reason"),
                    "intensity": intensity
                })
        
        # Also inspect the exact target bar
        target_row = df.loc[idx].to_dict()
        target_prev = df.loc[idx-1].to_dict() if idx > 0 else target_row
        
        results.append({
            "symbol": symbol,
            "target_date": target_date.strftime('%Y-%m-%d'),
            "actual_date": actual_date.strftime('%Y-%m-%d'),
            "fired": fired_in_window,
            "target_indicators": {
                "cts": target_row.get("cts", np.nan),
                "cts_slope": target_row.get("cts_slope", np.nan),
                "cts_accel": target_row.get("cts_accel", np.nan),
                "cwc": target_row.get("cwc", np.nan),
                "cwc_slope": target_row.get("cwc_slope", np.nan),
                "fas": target_row.get("fas", np.nan),
                "psz_v": target_row.get("psz_v", np.nan),
                "price_slope_z": target_row.get("price_slope_z", np.nan),
                "base_tightness": target_row.get("base_tightness", np.nan),
                "pdd_120": target_row.get("pdd_120", np.nan),
                "range_pos_63": target_row.get("range_pos_63", np.nan),
                "range_pos_252": target_row.get("range_pos_252", np.nan),
                "close": target_row.get("close", np.nan),
            }
        })
        
    # Print summary of matches
    print("\n" + "="*80)
    print("MATCH SUMMARY")
    print("="*80)
    matched_count = 0
    for res in results:
        sym = res["symbol"]
        tgt = res["target_date"]
        fired = res["fired"]
        if fired:
            matched_count += 1
            print(f"\n{sym} on {tgt}: SUCCESS. Signals fired in window:")
            for f in fired:
                print(f"  - {f['date']}: {f['tag']} (intensity: {f['intensity']}) | Reason: {f['reason']}")
        else:
            print(f"\n{sym} on {tgt}: FAILED. No signal in window +/- 5 bars.")
            # Print indicators at target date
            ti = res["target_indicators"]
            print("  Indicators at target date:")
            print(f"    Close: {ti['close']:.2f} | Regime: {df.loc[df['date'] <= pd.to_datetime(tgt)].iloc[-1].get('regime', 'unknown')}")
            print(f"    CTS: {ti['cts']:.4f} | CTS_Slope: {ti['cts_slope']:.4f} | CTS_Accel: {ti['cts_accel']:.4f}")
            print(f"    CWC: {ti['cwc']:.4f} | CWC_Slope: {ti['cwc_slope']:.4f}")
            print(f"    FAS: {ti['fas']:.4f} | PSZ_V: {ti['psz_v']:.4f} | Price_Slope_Z: {ti['price_slope_z']:.4f}")
            print(f"    BaseTight: {ti['base_tightness']:.4f} | PDD_120: {ti['pdd_120']:.4f}")
            print(f"    RP_63: {ti['range_pos_63']:.4f} | RP_252: {ti['range_pos_252']:.4f}")
            
    print("\n" + "="*80)
    print(f"Total matched: {matched_count} / {len(results)}")
    print("="*80)

if __name__ == "__main__":
    main()
