
"""
NSE Agent - Fetches Delivery & Volume Data from NSE Archives (Bhavcopy)
"""
import requests
import pandas as pd
import io
import sqlite3
import os
import sys
from datetime import datetime, timedelta
import time
import random

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from src.database import get_db_connection

# Archives URL structure
BASE_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{date_str}.csv"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Referer': 'https://www.nseindia.com/'
}

def fetch_bhavcopy(date_obj):
    """
    Fetches the full bhavcopy for a specific date.
    Returns DataFrame or None if not found/holiday.
    """
    date_str = date_obj.strftime('%d%m%Y')
    url = BASE_URL.format(date_str=date_str)
    
    print(f"[NSE] Fetching {url}...")
    
    try:
        s = requests.Session()
        s.headers.update(HEADERS)
        s.get("https://www.nseindia.com", timeout=10)
        response = s.get(url, timeout=15)
        
        if response.status_code == 200:
            csv_content = response.content.decode('utf-8')
            df = pd.read_csv(io.StringIO(csv_content))
            df.columns = [c.strip() for c in df.columns]
            return df
        elif response.status_code == 404:
            print(f"[NSE] No data for {date_str} (Likely Holiday/Weekend)")
            return None
        else:
            print(f"[NSE] Failed: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"[NSE] Error fetching {date_str}: {e}")
        return None

def process_and_store_data(df, record_date):
    """
    Processes the raw bhavcopy dataframe and stores it in the DB.
    Now includes price_change_pct computed from PREV_CLOSE.
    """
    if df is None or df.empty:
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Filter for Series = 'EQ' (Equity) only
    df['SERIES'] = df['SERIES'].str.strip()
    df = df[df['SERIES'] == 'EQ'].copy()
    
    records = []
    for _, row in df.iterrows():
        symbol = row['SYMBOL']
        close = float(row['CLOSE_PRICE'])
        prev_close = float(row['PREV_CLOSE']) if pd.notna(row.get('PREV_CLOSE')) else close
        volume = int(row['TTL_TRD_QNTY'])
        deliv_qty = int(row['DELIV_QTY']) if pd.notna(row['DELIV_QTY']) else 0
        deliv_pct = float(row['DELIV_PER']) if pd.notna(row['DELIV_PER']) else 0.0
        
        # Price change % from prev close (available directly in bhavcopy)
        price_chg = ((close - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
        
        records.append((
            record_date.strftime('%Y-%m-%d'),
            symbol,
            close,
            volume,
            deliv_qty,
            deliv_pct,
            price_chg,
            0.0, # vol_change (updated in enrichment pass)
            0.0  # deliv_change (updated in enrichment pass)
        ))
        
    print(f"[NSE] Inserting {len(records)} records for {record_date}...")
    
    cursor.executemany("""
        INSERT OR REPLACE INTO nse_delivery_log 
        (record_date, symbol, price_close, volume_total, delivery_qty, delivery_pct, 
         price_change_pct, volume_change_pct, delivery_change_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    
    conn.commit()
    conn.close()

def update_changes_for_date(current_date):
    """
    Calculates and updates volume_change_pct and delivery_change_pct for a specific date
    by comparing with the previous available date.
    delivery_change_pct = absolute change in delivery % (e.g., 40% -> 50% = +10)
    volume_change_pct = relative change in volume (e.g. 1M -> 1.5M = +50%)
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    current_date_str = current_date.strftime('%Y-%m-%d')
    
    res = cursor.execute("""
        SELECT MAX(record_date) FROM nse_delivery_log WHERE record_date < ?
    """, (current_date_str,)).fetchone()
    
    prev_date_str = res[0]
    
    if not prev_date_str:
        print(f"[NSE] No previous data found before {current_date_str}. Skipping change calc.")
        conn.close()
        return

    print(f"[NSE] Calculating changes for {current_date_str} vs {prev_date_str}...")
    
    try:
        cursor.execute(f"""
        INSERT OR REPLACE INTO nse_delivery_log (
            record_date, symbol, price_close, volume_total, delivery_qty, delivery_pct, 
            price_change_pct, volume_change_pct, delivery_change_pct
        )
        SELECT 
            curr.record_date, curr.symbol, curr.price_close, curr.volume_total, 
            curr.delivery_qty, curr.delivery_pct, curr.price_change_pct,
            ((curr.volume_total - prev.volume_total) * 100.0 / prev.volume_total),
            (curr.delivery_pct - prev.delivery_pct)
        FROM nse_delivery_log AS curr
        JOIN nse_delivery_log AS prev ON curr.symbol = prev.symbol
        WHERE curr.record_date = '{current_date_str}' AND prev.record_date = '{prev_date_str}'
        AND prev.volume_total > 0;
        """)
        conn.commit()
        print("[NSE] Metrics updated successfully.")
    except Exception as e:
        print(f"[NSE] Error updating metrics: {e}")
        
    conn.close()

def backfill_data(days=30):
    """
    Backfills data for the last N days.
    """
    today = datetime.now().date()
    for i in range(days, -1, -1):
        d = today - timedelta(days=i)
        
        if d.weekday() >= 5:
            continue
            
        print(f"\n[NSE] Processing {d}...")
        df = fetch_bhavcopy(d)
        if df is not None:
            process_and_store_data(df, d)
            update_changes_for_date(d)
            
        time.sleep(random.uniform(1, 3))

def smart_sync():
    """
    Smart sync: finds the last date in DB and fetches from the next day to today.
    If no data exists, defaults to 7-day backfill.
    Used by cron/daily sync to avoid re-downloading existing data.
    """
    conn = get_db_connection()
    res = conn.execute("SELECT MAX(record_date) FROM nse_delivery_log").fetchone()
    conn.close()
    
    today = datetime.now().date()
    
    if res and res[0]:
        last_date = datetime.strptime(res[0], '%Y-%m-%d').date()
        days_gap = (today - last_date).days
        
        if days_gap <= 0:
            print(f"[NSE] Already up to date (last: {last_date}). Nothing to sync.")
            return
        
        print(f"[NSE] Last data: {last_date}. Syncing {days_gap} day(s) to {today}...")
        
        for i in range(days_gap, -1, -1):
            d = today - timedelta(days=i)
            if d <= last_date:
                continue
            if d.weekday() >= 5:
                continue
            
            print(f"\n[NSE] Processing {d}...")
            df = fetch_bhavcopy(d)
            if df is not None:
                process_and_store_data(df, d)
                update_changes_for_date(d)
            
            time.sleep(random.uniform(1, 3))
    else:
        print("[NSE] No existing data. Running 7-day initial backfill...")
        backfill_data(7)
    
    print("[NSE] Smart sync complete.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", type=int, help="Days to backfill", default=0)
    parser.add_argument("--sync", action="store_true", help="Smart sync: fetch from last available date to today")
    args = parser.parse_args()
    
    if args.backfill > 0:
        backfill_data(args.backfill)
    elif args.sync:
        smart_sync()
    else:
        # Default: smart sync (used by cron)
        smart_sync()

