#!/usr/bin/env python3
"""
Yield Data Backfill Script
Backfills yield data from various sources: FRED, YFinance, NY Fed API, MOF Japan

Usage:
    python3 scripts/backfill_yields.py --source=fred --days=30
    python3 scripts/backfill_yields.py --source=yfinance --series=VIX,US10Y --days=7
    python3 scripts/backfill_yields.py --source=nyfed --series=RRP --days=7
    python3 scripts/backfill_yields.py --source=mof --series=JPY10Y
"""

import argparse
import sqlite3
import sys
import os
from datetime import date, timedelta, datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

DB_PATH = "liquidity_monitor.db"
FRED_API_KEY = os.getenv("FRED_API_KEY", "5bbee1aad376b64693645ea3a2c8becd")


def backfill_fred(days: int):
    """Backfill from FRED API"""
    from fredapi import Fred
    
    print(f"=== FRED Backfill (last {days} days) ===")
    
    fred = Fred(api_key=FRED_API_KEY)
    start_date = date.today() - timedelta(days=days)
    
    FRED_SERIES = {
        'DGS10': ('USD', '10Y'),
        'DEXJPUS': ('JPY', 'Spot'),
        'IRLTLT01JPM156N': ('JPY', '10Y'),
        'DTWEXBGS': ('USD', 'DXY'),
        'BAMLH0A0HYM2': ('USD', 'HY_SPREAD'),
        'RRPONTSYD': ('USD', 'RRP'),
        'VIXCLS': ('USD', 'VIX')
    }
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    total = 0
    
    for series_id, (currency, tenor) in FRED_SERIES.items():
        print(f"Fetching {series_id} ({tenor})...")
        try:
            data = fred.get_series(series_id, start_date.strftime('%Y-%m-%d'))
            data = data.dropna()
            
            if data.empty:
                print(f"  ⚠️ No data")
                continue
                
            count = 0
            for dt, value in data.items():
                rate_val = float(value)
                cursor.execute("""
                    INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
                    VALUES (?, ?, ?, ?)
                """, (dt.strftime("%Y-%m-%d 23:59:59"), currency, tenor, rate_val))
                count += 1
                
            print(f"  ✓ {count} records")
            total += count
                
        except Exception as e:
            print(f"  ⚠️ Error: {e}")
            
    conn.commit()
    conn.close()
    print(f"\n✓ Total: {total} records from FRED")


def backfill_yfinance(series: list, days: int):
    """Backfill from YFinance"""
    import yfinance as yf
    
    print(f"=== YFinance Backfill ({series}, last {days} days) ===")
    
    start_date = date.today() - timedelta(days=days)
    end_date = date.today() + timedelta(days=1)
    
    YFINANCE_MAP = {
        'VIX': ('^VIX', 'USD', 'VIX'),
        'US10Y': ('^TNX', 'USD', '10Y'),
    }
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    total = 0
    
    for s in series:
        if s not in YFINANCE_MAP:
            print(f"⚠️ Unknown series: {s}")
            continue
            
        symbol, currency, tenor = YFINANCE_MAP[s]
        print(f"Fetching {s} ({symbol})...")
        
        try:
            df = yf.download(symbol, start=start_date.strftime('%Y-%m-%d'), 
                           end=end_date.strftime('%Y-%m-%d'), progress=False)
            
            if df.empty:
                print(f"  ⚠️ No data")
                continue
                
            count = 0
            for idx, row in df.iterrows():
                dt = idx.date()
                rate_val = float(row['Close'].iloc[0]) if hasattr(row['Close'], 'iloc') else float(row['Close'])
                
                cursor.execute("""
                    INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
                    VALUES (?, ?, ?, ?)
                """, (f"{dt.strftime('%Y-%m-%d')} 23:59:59", currency, tenor, rate_val))
                count += 1
                
            print(f"  ✓ {count} records")
            total += count
                
        except Exception as e:
            print(f"  ⚠️ Error: {e}")
            
    conn.commit()
    conn.close()
    print(f"\n✓ Total: {total} records from YFinance")


def backfill_nyfed(days: int):
    """Backfill RRP from NY Fed Markets API"""
    import urllib.request
    import json
    
    print(f"=== NY Fed API Backfill (RRP, last {days} days) ===")
    
    start_date = date.today() - timedelta(days=days)
    end_date = date.today()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        url = f"https://markets.newyorkfed.org/api/rp/reverserepo/propositions/search.json?startDate={start_date.strftime('%Y-%m-%d')}&endDate={end_date.strftime('%Y-%m-%d')}"
        
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read().decode())
        
        if 'repo' not in data or 'operations' not in data['repo']:
            print("⚠️ No data from NY Fed API")
            conn.close()
            return
            
        count = 0
        for op in data['repo']['operations']:
            dt_str = op['operationDate']
            rate_val = float(op['totalAmtAccepted']) / 1e9
            
            cursor.execute("""
                INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
                VALUES (?, ?, ?, ?)
            """, (f"{dt_str} 00:00:00", 'USD', 'RRP', rate_val))
            count += 1
            
        conn.commit()
        print(f"✓ {count} records from NY Fed API")
            
    except Exception as e:
        print(f"⚠️ NY Fed API error: {e}")
    finally:
        conn.close()


def backfill_mof():
    """Backfill JPY 10Y from MOF Japan"""
    import urllib.request
    
    print("=== MOF Japan Backfill (JPY 10Y) ===")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Use historical data for full backfill
        url = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv"
        
        print("Fetching historical data (this may take a moment)...")
        with urllib.request.urlopen(url, timeout=60) as response:
            raw_content = response.read()
        
        # Try multiple encodings
        content = None
        for encoding in ['utf-8', 'shift_jis', 'cp932', 'latin-1']:
            try:
                content = raw_content.decode(encoding)
                break
            except:
                continue
        
        if content is None:
            print("⚠️ Could not decode CSV content")
            conn.close()
            return
        
        lines = content.strip().split('\n')
        
        # Find header row
        header_idx = None
        for i, line in enumerate(lines):
            if '10Y' in line and 'Date' in line:
                header_idx = i
                break
        
        if header_idx is None:
            print("⚠️ Could not find header row")
            conn.close()
            return
            
        headers = [h.strip() for h in lines[header_idx].split(',')]
        col_10y = headers.index('10Y') if '10Y' in headers else None
        
        if col_10y is None:
            print("⚠️ Could not find 10Y column")
            conn.close()
            return
            
        count = 0
        for line in lines[header_idx + 1:]:
            if not line.strip():
                continue
                
            parts = line.split(',')
            if len(parts) <= col_10y:
                continue
                
            dt_raw = parts[0].strip()
            try:
                dt = datetime.strptime(dt_raw, '%Y/%m/%d').date()
            except:
                continue
                
            try:
                rate_val = float(parts[col_10y].strip())
            except:
                continue
                
            cursor.execute("""
                INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
                VALUES (?, ?, ?, ?)
            """, (f"{dt.strftime('%Y-%m-%d')} 23:59:59", 'JPY', '10Y', rate_val))
            count += 1
            
        conn.commit()
        print(f"✓ {count} records from MOF Japan")
            
    except Exception as e:
        print(f"⚠️ MOF Japan error: {e}")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='Yield Data Backfill Script')
    parser.add_argument('--source', required=True, choices=['fred', 'yfinance', 'nyfed', 'mof'],
                       help='Data source to backfill from')
    parser.add_argument('--series', default='',
                       help='Comma-separated series (for yfinance: VIX,US10Y)')
    parser.add_argument('--days', type=int, default=30,
                       help='Number of days to backfill (default: 30)')
    
    args = parser.parse_args()
    
    if args.source == 'fred':
        backfill_fred(args.days)
    elif args.source == 'yfinance':
        series = [s.strip() for s in args.series.split(',') if s.strip()]
        if not series:
            series = ['VIX', 'US10Y']
        backfill_yfinance(series, args.days)
    elif args.source == 'nyfed':
        backfill_nyfed(args.days)
    elif args.source == 'mof':
        backfill_mof()


if __name__ == "__main__":
    main()
