
"""
Backfill historical data for the Liquidity Monitor
Uses REAL FRED data - no simulation/mock
"""
import sqlite3
from datetime import datetime
from fredapi import Fred

DB_PATH = "liquidity_monitor.db"
FRED_API_KEY = "5bbee1aad376b64693645ea3a2c8becd"

def backfill_fred_data(start_date="2025-11-01"):
    """Fetch historical yield data from FRED (REAL DATA ONLY)"""
    fred = Fred(api_key=FRED_API_KEY)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    series = {
        'DGS10': ('USD', '10Y'),           # 10-Year Treasury Yield
        'DEXJPUS': ('JPY', 'Spot'),        # USD/JPY Exchange Rate
        'IRLTLT01JPM156N': ('JPY', '10Y'), # Japan 10-Year Bond Yield
        'DTWEXBGS': ('USD', 'DXY'),        # Nominal Broad US Dollar Index
        'BAMLH0A0HYM2': ('USD', 'HY_SPREAD'), # ICE BofA US High Yield Spread
        'RRPONTSYD': ('USD', 'RRP'),       # Overnight Reverse Repo (Billions)
        'VIXCLS': ('USD', 'VIX')           # CBOE Volatility Index
    }
    
    print(f"Fetching FRED data from {start_date}...")
    
    for series_id, (currency, tenor) in series.items():
        try:
            data = fred.get_series(series_id, start_date)
            data = data.dropna()
            
            if data.empty:
                print(f"  ⚠️ WARNING: No data for {series_id} from {start_date}")
                continue
                
            print(f"  > {series_id}: {len(data)} observations")
            
            for date, value in data.items():
                cursor.execute("""
                    INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
                    VALUES (?, ?, ?, ?)
                """, (date.strftime("%Y-%m-%d %H:%M:%S"), currency, tenor, float(value)))
                
        except Exception as e:
            print(f"  ⚠️ WARNING: Failed to fetch {series_id}: {e}")
            
    conn.commit()
    conn.close()
    print("FRED backfill complete.")

if __name__ == "__main__":
    print("=== FRED Historical Backfill ===")
    backfill_fred_data()
