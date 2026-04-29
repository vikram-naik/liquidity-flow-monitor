import argparse
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from tabulate import tabulate

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine

def main():
    parser = argparse.ArgumentParser(description="Debug a stock by extracting key SavgolCTS indicators.")
    parser.add_argument("--symbol", required=True, help="Stock symbol (e.g., TRENT)")
    parser.add_argument("--start-date", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    sym = args.symbol
    start_date = pd.to_datetime(args.start_date)
    end_date = pd.to_datetime(args.end_date)

    print(f"Loading full history for {sym} to ensure indicator warm-up...")
    # Follow GEMINI.md mandate: DO NOT pass start_date and end_date to DivergenceEngine
    engine = DivergenceEngine(sym)
    df = engine.run().ledger
    
    # Filter the resulting ledger
    df['date_dt'] = pd.to_datetime(df['date'])
    mask = (df['date_dt'] >= start_date) & (df['date_dt'] <= end_date)
    df_filtered = df.loc[mask].copy()

    if df_filtered.empty:
        print(f"No data found for {sym} between {args.start_date} and {args.end_date}.")
        return

    # Extract key features used in savgol_cts package
    features = []
    for _, row in df_filtered.iterrows():
        close = row.get("close", np.nan)
        cwvap = row.get("cwvap", np.nan)
        va_high = row.get("va_high", np.nan)
        cwvap_dist = (close - cwvap) / cwvap * 100.0 if not np.isnan(cwvap) and cwvap > 0 else np.nan
        
        features.append({
            "Date": row.get("date_dt").strftime("%Y-%m-%d"),
            "Close": f"{close:.2f}",
            "Regime": row.get("regime", ""),
            "CTS": f"{row.get('cts', np.nan):.3f}",
            "CTS_BT": f"{row.get('cts_buy_threshold', np.nan):.3f}",
            "CTS_ST": f"{row.get('cts_sell_threshold', np.nan):.3f}",
            "CTS_Slope": f"{row.get('cts_slope', np.nan):.4f}",
            "CTS_Accel": f"{row.get('cts_accel', np.nan):.4f}",
            "Accel_Thr": f"{row.get('cts_accel_threshold', np.nan):.4f}",
            "PSZ": f"{row.get('price_slope_z', np.nan):.3f}",
            "PSZ_V": f"{row.get('psz_v', np.nan):.4f}",
            "FAS": f"{row.get('fas', np.nan):.3f}",
            "PRT_Slope": f"{row.get('prt_slope', np.nan):.3f}",
            "CWVAP_Dist%": f"{cwvap_dist:.2f}%",
            "CWC_Slope": f"{row.get('cwc_slope', np.nan):.4f}",
            "PDD_120": f"{row.get('pdd_120', np.nan):.2f}",
            "RP_63": f"{row.get('range_pos_63', np.nan):.2f}",
            "RP_252": f"{row.get('range_pos_252', np.nan):.2f}",
            "VA_High": f"{va_high:.2f}",
        })

    print(f"\n--- Key SavgolCTS Indicators for {sym} ---")
    print(tabulate(features, headers="keys", tablefmt="grid"))
    
    print("\nLegend:")
    print("CTS/FAS: Core institutional and thrust indicators (Range: -1 to +1)")
    print("PSZ / PSZ_V: Price slope z-score and its velocity (Momentum)")
    print("CTS_BT / CTS_ST: Dynamic Buy and Sell Thresholds for CTS")
    print("CWVAP_Dist%: Distance from Close to CWVAP")
    print("PDD_120: Price Divergence Distance (Institutional Exhaustion)")
    print("RP_n: Range Position over n days (0 = at low, 1 = at high)")
    print("VA_High: Value Area High (Volume profile upper bound)")

if __name__ == "__main__":
    main()
