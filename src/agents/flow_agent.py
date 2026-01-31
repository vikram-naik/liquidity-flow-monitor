
"""
FRED Flow Agent - Fetches yield and FX data from Federal Reserve Economic Data
Fetches incrementally from last download date (base: 1-Nov-2025)
"""
import sqlite3
from datetime import datetime, date, timedelta
from fredapi import Fred
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from src.utils.data_sync import get_last_yield_date, BASE_DATE

DB_PATH = "liquidity_monitor.db"
FRED_API_KEY = "5bbee1aad376b64693645ea3a2c8becd"

# Series to fetch
FRED_SERIES = {
    'DGS10': ('USD', '10Y'),      # 10-Year Treasury Yield
    'DEXJPUS': ('JPY', 'Spot'),   # USD/JPY Exchange Rate
    'IRLTLT01JPM156N': ('JPY', '10Y') # Japan 10-Year Bond Yield
}

def fetch_fred_data(start_date: date = None):
    """
    Fetch yield and FX data from FRED API.
    If start_date is None, uses last download date + 1 day.
    """
    fred = Fred(api_key=FRED_API_KEY)
    
    if start_date is None:
        last_date = get_last_yield_date()
        start_date = last_date + timedelta(days=1)
        
    today = date.today()
    
    if start_date > today:
        print("✓ Already up to date!")
        return
        
    print(f"=== FRED Flow Agent ===")
    print(f"Base date: {BASE_DATE}")
    print(f"Last download: {get_last_yield_date()}")
    print(f"Fetching: {start_date} to {today}")
    print()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    total_records = 0
    
    for series_id, (currency, tenor) in FRED_SERIES.items():
        print(f"Fetching {series_id}...")
        try:
            data = fred.get_series(series_id, start_date.strftime('%Y-%m-%d'))
            data = data.dropna()
            
            if data.empty:
                print(f"  ⚠️ No new data available")
                continue
                
            count = 0
            for dt, value in data.items():
                cursor.execute("""
                    INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
                    VALUES (?, ?, ?, ?)
                """, (dt.strftime("%Y-%m-%d %H:%M:%S"), currency, tenor, float(value)))
                count += 1
                
            print(f"  ✓ {count} observations ({data.index[0].date()} to {data.index[-1].date()})")
            total_records += count
                
        except Exception as e:
            print(f"  ⚠️ Error: {e}")
            
    conn.commit()
    conn.close()
    
    print(f"\n✓ Synced {total_records} total records")

def run_flow_sync():
    """Main sync function"""
    fetch_fred_data()

if __name__ == "__main__":
    run_flow_sync()
