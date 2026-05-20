import os
import sys
import pandas as pd
import numpy as np
from scipy.stats import spearmanr

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend

# List of stocks and dates to check
stocks = [
    ("SHRIRAMFIN", "2025-01-01"),
    ("POWERGRID", "2025-08-08"),
    ("TRENT", "2025-11-20"),
    ("KOTAKBANK", "2026-02-06"),
    ("TATASTEEL", "2024-11-05"),
    ("NTPC", "2024-12-03"),
    ("ADANIENT", "2024-12-02"),
    ("ITC", "2025-06-09"),
]

def calculate_spearman(x):
    if len(x) < 5:
        return 0.0
    # Handle perfectly flat data to avoid ConstantInputWarning
    # x is a pandas Series from rolling().apply()
    vals = x.values
    if np.all(vals == vals[0]):
        return 0.0
    # spearmanr returns (correlation, p-value)
    return spearmanr(vals, np.arange(len(vals)))[0]

def main():
    results = []

    for symbol, sig_date in stocks:
        print(f"Analyzing {symbol} for {sig_date}...")
        try:
            # Load full history via DivergenceEngine to ensure indicators are warmed up
            engine = DivergenceEngine(ticker=symbol)
            res = engine.run()
            df = res.ledger
            
            # Ensure date is datetime
            df['date'] = pd.to_datetime(df['date'])
            target_dt = pd.to_datetime(sig_date)
            
            # 1. Calculate Typical Price Spearman (5-day)
            # Typical price is (H+L+C)/3. BaseCalculator already computes 'tp' but engine drops it.
            # We'll recompute it for this script.
            df["tp"] = (df["high"] + df["low"] + df["close"]) / 3.0
            df["tp_spearman_5"] = df["tp"].rolling(window=5).apply(calculate_spearman)

            # Calculate CTS Slope Spearman (5-day)
            df["cts_slope_spearman_5"] = df["cts_slope"].rolling(window=5).apply(lambda x: evaluate_spearman_trend(x.tolist()))
            
            # 2. Identify target indices for T and T-1
            # Find index of the bar on or before the sig_date
            target_mask = df['date'] <= target_dt
            if not target_mask.any():
                print(f"  [!] No data found for {symbol} on or before {sig_date}")
                continue
                
            sig_idx = df[target_mask].index[-1]
            
            # Gap Down Check (Last 10 bars including T)
            # Gap down: prev_low > current_high
            # Size: (prev_low - current_high) > 0.3 * ATR
            lookback = 10
            start_idx = max(1, sig_idx - lookback + 1)
            gap_found = False
            for i in range(start_idx, sig_idx + 1):
                prev_low = df.iloc[i-1]['low']
                curr_high = df.iloc[i]['high']
                atr = df.iloc[i]['atr_20']
                if prev_low > curr_high:
                    gap_size = prev_low - curr_high
                    if gap_size > (0.3 * atr):
                        gap_found = True
                        break
            gap_str = "YES" if gap_found else "NO"

            # Base indices
            t_idx = sig_idx
            t1_idx = sig_idx - 1

            if t1_idx < 0:
                print(f"  [!] Not enough history for T-1 check on {symbol}")
                continue
            for label, idx in [("T", t_idx), ("T-1", t1_idx)]:
                row = df.iloc[idx]
                actual_date = row['date'].strftime('%Y-%m-%d')

                # 3. Extract Basing Metrics
                bt = row.get('base_tightness', np.nan)
                rw10 = row.get('range_width_10', np.nan)
                atr = row.get('atr_20', np.nan)

                rw10_abs = (rw10 * row['close']) / 100.0
                rw10_atrs = rw10_abs / atr if atr > 0 else np.nan
                tp_spearman = row['tp_spearman_5']

                # 4. Slope Flatness Check (Spearman)
                # If |Spearman| < 0.6, the slope lacks a strong consistent trend (is flattish)
                slope_spearman = row['cts_slope_spearman_5']
                slope_flat = "YES" if abs(slope_spearman) < 0.6 else "NO"

                if symbol == "POWERGRID" and label == "T-1":
                    print(f"\n[DEBUG POWERGRID T-1 SLOPE SPEARMAN]")
                    print(f"  Values: {df.iloc[idx-4:idx+1]['cts_slope'].values.tolist()}")
                    print(f"  Spearman: {slope_spearman:.4f}")

                # 5. Evaluation
                is_tight = bt < 0.35
                is_narrow = rw10_atrs < 1.5
                is_flat = abs(tp_spearman) < 0.6

                basing_score = int(is_tight) + int(is_narrow) + int(is_flat)
                status = "YES" if basing_score >= 2 else "NO"

                results.append({
                    "Symbol": symbol,
                    "Shift": label,
                    "Date": actual_date,
                    "BT": f"{bt:.3f}",
                    "GAP10": gap_str,
                    "SlopeFlat": slope_flat,
                    "RW10_ATRs": f"{rw10_atrs:.2f}",
                    "TP_Spearman": f"{tp_spearman:.3f}",
                    "Basing?": status,
                    "Qualifies": "YES" if (gap_str == "YES" or slope_flat == "YES" or status == "YES") else "NO"
                })

            
        except Exception as e:
            print(f"  [!] Error: {e}")

    # Display results
    df_res = pd.DataFrame(results)
    print("\n" + "="*115)
    print(f"{'SYMBOL':<12} | {'SHIFT':<5} | {'DATE':<12} | {'BT':<6} | {'GAP10':<5} | {'SLOPE_F':<7} | {'BASING?':<8} | {'QUALIFIES'}")
    print("-" * 115)
    for _, r in df_res.iterrows():
        print(f"{r['Symbol']:<12} | {r['Shift']:<5} | {r['Date']:<12} | {r['BT']:<6} | {r['GAP10']:<5} | {r['SlopeFlat']:<7} | {r['Basing?']:<8} | {r['Qualifies']}")
    print("="*115)
    print("\nMetrics Guide:")
    print(" - BT (Base Tightness): rw10 / rw63. Lower is tighter. < 0.35 is good.")
    print(" - RW10_ATR: 10-day range in multiples of ATR. < 1.5 is narrow.")
    print(" - SPEARMAN: 5-day typical price trend. Near 0 is flat/basing.")

if __name__ == "__main__":
    main()
