
"""
FRED Flow Agent - Fetches yield and FX data from Federal Reserve Economic Data
Fetches incrementally from last download date (base: 1-Nov-2025)
"""
import sqlite3
from datetime import datetime, date, timedelta
from fredapi import Fred
import yfinance as yf
import sys
import os
import requests
from bs4 import BeautifulSoup

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from src.utils.data_sync import get_last_yield_date, BASE_DATE

DB_PATH = "liquidity_monitor.db"
FRED_API_KEY = os.getenv("FRED_API_KEY", "5bbee1aad376b64693645ea3a2c8becd")

# Series to fetch
FRED_SERIES = {
    'DGS10': ('USD', '10Y'),           # 10-Year Treasury Yield
    'DEXJPUS': ('JPY', 'Spot'),        # USD/JPY Exchange Rate
    'IRLTLT01JPM156N': ('JPY', '10Y'), # Japan 10-Year Bond Yield
    'DTWEXBGS': ('USD', 'DXY'),        # Nominal Broad US Dollar Index
    'BAMLH0A0HYM2': ('USD', 'HY_SPREAD'), # ICE BofA US High Yield Spread
    'RRPONTSYD': ('USD', 'RRP'),       # Overnight Reverse Repo (Billions)
    'VIXCLS': ('USD', 'VIX')           # CBOE Volatility Index (Fear Gauge)
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
                rate_val = float(value)
                if rate_val <= 0 and currency != 'JPY': # JPY rates can be 0 or negative sometimes, but USD yields shouldn't be 0
                    print(f"    ⚠️ Skipping {dt.date()}: Rate is {rate_val}")
                    continue

                # FRED data is daily, we align to UTC EOD
                cursor.execute("""
                    INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
                    VALUES (?, ?, ?, ?)
                """, (dt.strftime("%Y-%m-%d 23:59:59"), currency, tenor, rate_val))
                count += 1
                
            print(f"  ✓ {count} observations ({data.index[0].date()} to {data.index[-1].date()})")
            total_records += count
                
        except Exception as e:
            print(f"  ⚠️ Error: {e}")
            
    conn.commit()
    conn.close()
    
    print(f"\n✓ Synced {total_records} total records")
    
    # Fallback: Fetch USD/JPY from Yahoo Finance if FRED has gaps
    fetch_usdjpy_yfinance_fallback(start_date)


def fetch_usdjpy_yfinance_fallback(start_date: date):
    """
    Fallback to fetch USD/JPY from Yahoo Finance when FRED has data lag.
    Only fills in dates that are missing in the database.
    """
    print("\n--- USD/JPY YFinance Fallback ---")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get existing USD/JPY dates
    existing = cursor.execute("""
        SELECT DATE(timestamp) FROM yield_logs 
        WHERE currency='JPY' AND tenor='Spot' AND timestamp >= ?
    """, (start_date.strftime('%Y-%m-%d'),)).fetchall()
    existing_dates = set(row[0] for row in existing)
    
    today = date.today()
    
    try:
        # Fetch from Yahoo Finance (JPY=X is USD/JPY)
        df = yf.download('JPY=X', start=start_date.strftime('%Y-%m-%d'), 
                         end=(today + timedelta(days=1)).strftime('%Y-%m-%d'), 
                         progress=False)
        
        if df.empty:
            print("  ⚠️ No data from YFinance")
            conn.close()
            return
            
        count = 0
        for idx, row in df.iterrows():
            dt = idx.date()
            dt_str = dt.strftime('%Y-%m-%d')
            
            # Skip if already have data for this date
            if dt_str in existing_dates:
                continue
                
            rate_val = float(row['Close'].iloc[0]) if hasattr(row['Close'], 'iloc') else float(row['Close'])
            if rate_val <= 0:
                continue
                
            cursor.execute("""
                INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
                VALUES (?, ?, ?, ?)
            """, (f"{dt_str} 23:59:59", 'JPY', 'Spot', rate_val))
            count += 1
            
        conn.commit()
        
        if count > 0:
            print(f"  ✓ Filled {count} missing USD/JPY dates from YFinance")
        else:
            print("  ✓ No missing dates to fill")
            
    except Exception as e:
        print(f"  ⚠️ YFinance error: {e}")
    finally:
        conn.close()


def fetch_vix_yfinance_fallback(start_date: date):
    """
    Fallback to fetch VIX from Yahoo Finance when FRED has data lag.
    Only fills in dates that are missing in the database.
    """
    print("\n--- VIX YFinance Fallback ---")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get existing VIX dates
    existing = cursor.execute("""
        SELECT DATE(timestamp) FROM yield_logs 
        WHERE tenor='VIX' AND timestamp >= ?
    """, (start_date.strftime('%Y-%m-%d'),)).fetchall()
    existing_dates = set(row[0] for row in existing)
    
    today = date.today()
    
    try:
        df = yf.download('^VIX', start=start_date.strftime('%Y-%m-%d'), 
                         end=(today + timedelta(days=1)).strftime('%Y-%m-%d'), 
                         progress=False)
        
        if df.empty:
            print("  ⚠️ No data from YFinance")
            conn.close()
            return
            
        count = 0
        for idx, row in df.iterrows():
            dt = idx.date()
            dt_str = dt.strftime('%Y-%m-%d')
            
            if dt_str in existing_dates:
                continue
                
            rate_val = float(row['Close'].iloc[0]) if hasattr(row['Close'], 'iloc') else float(row['Close'])
            if rate_val <= 0:
                continue
                
            cursor.execute("""
                INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
                VALUES (?, ?, ?, ?)
            """, (f"{dt_str} 23:59:59", 'USD', 'VIX', rate_val))
            count += 1
            
        conn.commit()
        
        if count > 0:
            print(f"  ✓ Filled {count} missing VIX dates from YFinance")
        else:
            print("  ✓ No missing dates to fill")
            
    except Exception as e:
        print(f"  ⚠️ YFinance error: {e}")
    finally:
        conn.close()


def fetch_us10y_yfinance_fallback(start_date: date):
    """
    Fallback to fetch US 10Y yield from Yahoo Finance when FRED has data lag.
    ^TNX returns direct percentage value.
    """
    print("\n--- US 10Y YFinance Fallback ---")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get existing US 10Y dates
    existing = cursor.execute("""
        SELECT DATE(timestamp) FROM yield_logs 
        WHERE currency='USD' AND tenor='10Y' AND timestamp >= ?
    """, (start_date.strftime('%Y-%m-%d'),)).fetchall()
    existing_dates = set(row[0] for row in existing)
    
    today = date.today()
    
    try:
        df = yf.download('^TNX', start=start_date.strftime('%Y-%m-%d'), 
                         end=(today + timedelta(days=1)).strftime('%Y-%m-%d'), 
                         progress=False)
        
        if df.empty:
            print("  ⚠️ No data from YFinance")
            conn.close()
            return
            
        count = 0
        for idx, row in df.iterrows():
            dt = idx.date()
            dt_str = dt.strftime('%Y-%m-%d')
            
            if dt_str in existing_dates:
                continue
                
            rate_val = float(row['Close'].iloc[0]) if hasattr(row['Close'], 'iloc') else float(row['Close'])
            if rate_val <= 0:
                continue
                
            cursor.execute("""
                INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
                VALUES (?, ?, ?, ?)
            """, (f"{dt_str} 23:59:59", 'USD', '10Y', rate_val))
            count += 1
            
        conn.commit()
        
        if count > 0:
            print(f"  ✓ Filled {count} missing US 10Y dates from YFinance")
        else:
            print("  ✓ No missing dates to fill")
            
    except Exception as e:
        print(f"  ⚠️ YFinance error: {e}")
    finally:
        conn.close()


def fetch_rrp_nyfed_fallback(start_date: date):
    """
    Fallback to fetch RRP (Reverse Repo) from NY Fed Markets API.
    API: https://markets.newyorkfed.org/api/rp/reverserepo/propositions/search.json
    """
    import urllib.request
    import json
    
    print("\n--- RRP NY Fed API Fallback ---")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get existing RRP dates
    existing = cursor.execute("""
        SELECT DATE(timestamp) FROM yield_logs 
        WHERE tenor='RRP' AND timestamp >= ?
    """, (start_date.strftime('%Y-%m-%d'),)).fetchall()
    existing_dates = set(row[0] for row in existing)
    
    today = date.today()
    
    try:
        url = f"https://markets.newyorkfed.org/api/rp/reverserepo/propositions/search.json?startDate={start_date.strftime('%Y-%m-%d')}&endDate={today.strftime('%Y-%m-%d')}"
        
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read().decode())
        
        if 'repo' not in data or 'operations' not in data['repo']:
            print("  ⚠️ No data from NY Fed API")
            conn.close()
            return
            
        count = 0
        for op in data['repo']['operations']:
            dt_str = op['operationDate']
            
            if dt_str in existing_dates:
                continue
                
            # totalAmtAccepted is in dollars, convert to billions
            rate_val = float(op['totalAmtAccepted']) / 1e9
            
            cursor.execute("""
                INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
                VALUES (?, ?, ?, ?)
            """, (f"{dt_str} 00:00:00", 'USD', 'RRP', rate_val))
            count += 1
            
        conn.commit()
        
        if count > 0:
            print(f"  ✓ Filled {count} missing RRP dates from NY Fed API")
        else:
            print("  ✓ No missing dates to fill")
            
    except Exception as e:
        print(f"  ⚠️ NY Fed API error: {e}")
    finally:
        conn.close()


def fetch_jpy10y_mof_fallback():
    """
    Fetch Japan 10Y yield from Ministry of Finance Japan CSV.
    This provides DAILY data vs FRED's monthly data.
    URL: https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv
    """
    import urllib.request
    import csv
    import io
    
    print("\n--- JPY 10Y MOF Japan Fallback ---")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get existing JPY 10Y dates
    existing = cursor.execute("""
        SELECT DATE(timestamp) FROM yield_logs 
        WHERE currency='JPY' AND tenor='10Y'
    """).fetchall()
    existing_dates = set(row[0] for row in existing)
    
    try:
        url = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
        
        with urllib.request.urlopen(url, timeout=30) as response:
            raw_content = response.read()
        
        # Try UTF-8 first, then shift_jis (common for Japanese sites)
        content = None
        for encoding in ['utf-8', 'shift_jis', 'cp932', 'latin-1']:
            try:
                content = raw_content.decode(encoding)
                break
            except:
                continue
        
        if content is None:
            print("  ⚠️ Could not decode CSV content")
            conn.close()
            return
        
        lines = content.strip().split('\n')
        
        # Find header row (contains "10Y")
        header_idx = None
        for i, line in enumerate(lines):
            if '10Y' in line:
                header_idx = i
                break
        
        if header_idx is None:
            print("  ⚠️ Could not find header row")
            conn.close()
            return
            
        headers = [h.strip() for h in lines[header_idx].split(',')]
        col_10y = headers.index('10Y') if '10Y' in headers else None
        
        if col_10y is None:
            print("  ⚠️ Could not find 10Y column")
            conn.close()
            return
            
        count = 0
        for line in lines[header_idx + 1:]:
            if not line.strip():
                continue
                
            parts = line.split(',')
            if len(parts) <= col_10y:
                continue
                
            # Date format: YYYY/M/D
            dt_raw = parts[0].strip()
            try:
                dt = datetime.strptime(dt_raw, '%Y/%m/%d').date()
            except:
                continue
                
            dt_str = dt.strftime('%Y-%m-%d')
            
            if dt_str in existing_dates:
                continue
                
            try:
                rate_val = float(parts[col_10y].strip())
            except:
                continue
                
            cursor.execute("""
                INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
                VALUES (?, ?, ?, ?)
            """, (f"{dt_str} 23:59:59", 'JPY', '10Y', rate_val))
            count += 1
            
        conn.commit()
        
        if count > 0:
            print(f"  ✓ Filled {count} JPY 10Y dates from MOF Japan")
        else:
            print("  ✓ No new dates to fill")
            
    except Exception as e:
        print(f"  ⚠️ MOF Japan error: {e}")
    finally:
        conn.close()

def fetch_jp10y_cnbc_realtime():
    """
    Fetch real-time JP10Y yield from CNBC.
    This acts as an intraday update source before the official MOF close data is available.
    """
    print("\\n--- JP10Y Real-time (CNBC) ---")
    
    url = "https://www.cnbc.com/quotes/JP10Y"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"  ⚠️ CNBC fetch failed: {response.status_code}")
            return

        soup = BeautifulSoup(response.content, 'html.parser')
        
        # CNBC class for last price
        el = soup.select_one(".QuoteStrip-lastPrice")
        if not el:
            print("  ⚠️ Could not find price element on CNBC")
            return

        rate_str = el.text.strip().replace('%', '')
        try:
            rate_val = float(rate_str)
        except ValueError:
             print(f"  ⚠️ Could not parse rate: {rate_str}")
             return
        
        if rate_val <= 0:
            print(f"  ⚠️ Invalid rate: {rate_val}")
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        today = date.today()
        today_str = today.strftime('%Y-%m-%d')
        
        # Check if we already have a value for today (from MOF)
        # MOF data is usually stored with 23:59:59 timestamp
        existing = cursor.execute("""
            SELECT rate, timestamp FROM yield_logs 
            WHERE currency='JPY' AND tenor='10Y' AND DATE(timestamp) = ?
        """, (today_str,)).fetchone()
        
        if existing:
             print(f"  ℹ️  Updating today's value with real-time: {rate_val}% (Old: {existing[0]}%)")
        else:
            print(f"  ✓ Fetched real-time JP10Y: {rate_val}%")
            
        # Insert/Update for today
        # We use today's date with 23:59:59 to align with daily schema, 
        # so that when MOF runs, it overwrites THIS record.
        cursor.execute("""
            INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
            VALUES (?, ?, ?, ?)
        """, (f"{today_str} 23:59:59", 'JPY', '10Y', rate_val))
        
        conn.commit()
        conn.close()

    except Exception as e:
        print(f"  ⚠️ CNBC Error: {e}")


def fetch_us10y_cnbc_realtime():
    """
    Fetch real-time US10Y yield from CNBC.
    This acts as an intraday update source before the official FRED close data is available.
    """
    print("\\n--- US10Y Real-time (CNBC) ---")
    
    url = "https://www.cnbc.com/quotes/US10Y"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"  ⚠️ CNBC fetch failed: {response.status_code}")
            return

        soup = BeautifulSoup(response.content, 'html.parser')
        
        # CNBC class for last price
        el = soup.select_one(".QuoteStrip-lastPrice")
        if not el:
            print("  ⚠️ Could not find price element on CNBC")
            return

        rate_str = el.text.strip().replace('%', '')
        try:
            rate_val = float(rate_str)
        except ValueError:
             print(f"  ⚠️ Could not parse rate: {rate_str}")
             return
        
        if rate_val <= 0:
            print(f"  ⚠️ Invalid rate: {rate_val}")
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        today = date.today()
        today_str = today.strftime('%Y-%m-%d')
        
        # Check if we already have a value for today (from FRED)
        # FRED data is usually stored with 23:59:59 timestamp
        existing = cursor.execute("""
            SELECT rate, timestamp FROM yield_logs 
            WHERE currency='USD' AND tenor='10Y' AND DATE(timestamp) = ?
        """, (today_str,)).fetchone()
        
        if existing:
             print(f"  ℹ️  Updating today's value with real-time: {rate_val}% (Old: {existing[0]}%)")
        else:
            print(f"  ✓ Fetched real-time US10Y: {rate_val}%")
            
        # Insert/Update for today
        cursor.execute("""
            INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
            VALUES (?, ?, ?, ?)
        """, (f"{today_str} 23:59:59", 'USD', '10Y', rate_val))
        
        conn.commit()
        conn.close()

    except Exception as e:
        print(f"  ⚠️ CNBC Error: {e}")

def fetch_ice_dxy_yfinance_fallback(start_date: date):
    """
    Fetch ICE DXY (DX-Y.NYB) from Yahoo Finance.
    This provides a 'Live' view compared to FRED's lagging Broad Dollar Index.
    """
    print("\n--- ICE DXY (Live) YFinance Fallback ---")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get existing DXY_ICE dates
    existing = cursor.execute("""
        SELECT DATE(timestamp) FROM yield_logs 
        WHERE tenor='DXY_ICE' AND timestamp >= ?
    """, (start_date.strftime('%Y-%m-%d'),)).fetchall()
    existing_dates = set(row[0] for row in existing)
    
    today = date.today()
    
    try:
        # DX-Y.NYB is the ticker for ICE US Dollar Index
        df = yf.download('DX-Y.NYB', start=start_date.strftime('%Y-%m-%d'), 
                         end=(today + timedelta(days=1)).strftime('%Y-%m-%d'), 
                         progress=False)
        
        if df.empty:
            print("  ⚠️ No data from YFinance for DX-Y.NYB")
            conn.close()
            return
            
        count = 0
        for idx, row in df.iterrows():
            dt = idx.date()
            dt_str = dt.strftime('%Y-%m-%d')
            
            if dt_str in existing_dates:
                continue
                
            rate_val = float(row['Close'].iloc[0]) if hasattr(row['Close'], 'iloc') else float(row['Close'])
            if rate_val <= 0:
                continue
                
            cursor.execute("""
                INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
                VALUES (?, ?, ?, ?)
            """, (f"{dt_str} 23:59:59", 'USD', 'DXY_ICE', rate_val))
            count += 1
            
        conn.commit()
        
        if count > 0:
            print(f"  ✓ Filled {count} missing ICE DXY dates from YFinance")
        else:
            print("  ✓ No missing dates to fill")
            
    except Exception as e:
        print(f"  ⚠️ YFinance error: {e}")
    finally:
        conn.close()


def run_flow_sync():
    """Main sync function - fetches from FRED then fills gaps from fallbacks"""
    fetch_fred_data()
    
    # Get start date for fallbacks
    last_date = get_last_yield_date()
    start_date = last_date - timedelta(days=7)  # Look back 7 days to fill gaps
    
    # Run all fallbacks
    fetch_vix_yfinance_fallback(start_date)
    fetch_us10y_yfinance_fallback(start_date)
    fetch_rrp_nyfed_fallback(start_date)
    fetch_jpy10y_mof_fallback()
    
    # Intraday update for JPY 10Y (CNBC)
    fetch_jp10y_cnbc_realtime()

    # Intraday update for US 10Y (CNBC)
    fetch_us10y_cnbc_realtime()

    fetch_ice_dxy_yfinance_fallback(start_date)

if __name__ == "__main__":
    run_flow_sync()

