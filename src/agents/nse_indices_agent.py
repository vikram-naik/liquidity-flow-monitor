"""
NSE Indices Agent - Fetches OHLC and Fundamentals Data from NSE Archives
"""
import requests
import pandas as pd
import io
import os
import sys
from datetime import datetime, timedelta
import time
import random
import numpy as np

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from src.database import get_db_connection
from src.cache import get_cache
from src.agents.nse_agent import is_trading_holiday

cache = get_cache()

# Archives URL structure for indices
BASE_URL = "https://archives.nseindia.com/content/indices/ind_close_all_{date_str}.csv"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Referer': 'https://www.nseindia.com/'
}

def fetch_indices_data(date_obj):
    """
    Fetches the indices data for a specific date.
    Returns DataFrame or None if not found/holiday.
    """
    date_str = date_obj.strftime('%d%m%Y')
    url = BASE_URL.format(date_str=date_str)
    
    print(f"[NSE Indices] Fetching {url}...")
    
    try:
        s = requests.Session()
        s.headers.update(HEADERS)
        s.get("https://www.nseindia.com", timeout=10) # Get cookies first
        response = s.get(url, timeout=15)
        
        if response.status_code == 200:
            csv_content = response.content.decode('utf-8')
            df = pd.read_csv(io.StringIO(csv_content))
            df.columns = [c.strip() for c in df.columns]
            return df
        elif response.status_code == 404:
            print(f"[NSE Indices] No data for {date_str} (Likely Holiday/Weekend)")
            return None
        else:
            print(f"[NSE Indices] Failed: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"[NSE Indices] Error fetching {date_str}: {e}")
        return None

def process_and_store_data(df, record_date):
    """
    Processes the raw indices dataframe and stores it in the DB.
    """
    if df is None or df.empty:
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Aggressively strip whitespace from all string columns
    df.columns = [c.strip() for c in df.columns]
    for col in df.select_dtypes(include=['object', 'string']).columns:
        df[col] = df[col].str.strip()
    
    records = []
    
    for _, row in df.iterrows():
        # Upper case indexing symbol format - "NIFTY 50"
        symbol = str(row['Index Name']).upper()
        
        # Convert any bad string formats like '-' to NaN then to 0.0
        row_dict = {}
        for col in row.index:
            val = row[col]
            if isinstance(val, str) and val.strip() == '-':
                row_dict[col] = np.nan
            else:
                row_dict[col] = val
                
        open_price = float(row_dict.get('Open Index Value', 0.0)) if pd.notna(row_dict.get('Open Index Value')) else 0.0
        high_price = float(row_dict.get('High Index Value', 0.0)) if pd.notna(row_dict.get('High Index Value')) else 0.0
        low_price = float(row_dict.get('Low Index Value', 0.0)) if pd.notna(row_dict.get('Low Index Value')) else 0.0
        close_price = float(row_dict.get('Closing Index Value', 0.0)) if pd.notna(row_dict.get('Closing Index Value')) else 0.0
        volume = int(float(row_dict.get('Volume', 0))) if pd.notna(row_dict.get('Volume')) else 0
        
        # For indices, delivery is 100% of volume
        deliv_qty = volume
        deliv_pct = 100.0 if volume > 0 else 0.0
        
        # Price change % directly from data
        price_chg = float(row_dict.get('Change(%)', 0.0)) if pd.notna(row_dict.get('Change(%)')) else 0.0
        
        # Fundamental fields
        pe_ratio = float(row_dict.get('P/E', 0.0)) if pd.notna(row_dict.get('P/E')) else None
        pb_ratio = float(row_dict.get('P/B', 0.0)) if pd.notna(row_dict.get('P/B')) else None
        div_yield = float(row_dict.get('Div Yield', 0.0)) if pd.notna(row_dict.get('Div Yield')) else None
        turnover = float(row_dict.get('Turnover (Rs. Cr.)', 0.0)) if pd.notna(row_dict.get('Turnover (Rs. Cr.)')) else None

        records.append((
            record_date.strftime('%Y-%m-%d'),
            symbol,
            close_price,
            open_price,
            high_price,
            low_price,
            volume,
            deliv_qty,
            deliv_pct,
            price_chg,
            0.0, # vol_change (updated in enrichment pass)
            0.0,  # deliv_change (updated in enrichment pass)
            pe_ratio,
            pb_ratio,
            div_yield,
            turnover,
            'INDEX',
        ))
        
    print(f"[NSE Indices] Inserting {len(records)} records for {record_date}...")
    
    cursor.executemany("""
        INSERT OR REPLACE INTO nse_delivery_log
        (record_date, symbol, price_close, price_open, price_high, price_low, volume_total, delivery_qty, delivery_pct,
         price_change_pct, volume_change_pct, delivery_change_pct, pe_ratio, pb_ratio, dividend_yield, turnover_crs,
         instrument_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    
    conn.commit()
    
    # Invalidate cache for processed symbols
    for s in df['Index Name'].unique():
        cache_key = f"lfm:raw_data:{s.upper().strip()}"
        cache.delete(cache_key)
        cache_key = f"de:raw_data:{s.upper().strip()}"
        cache.delete(cache_key)
    
    print(f"[NSE Indices] Invalidated cache for {len(df['Index Name'].unique())} symbols.")
    conn.close()

def update_changes_for_date(current_date):
    """
    Calculates and updates volume_change_pct and delivery_change_pct for a specific date
    by comparing with the previous available date.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    current_date_str = current_date.strftime('%Y-%m-%d')
    
    res = cursor.execute("""
        SELECT MAX(record_date) FROM nse_delivery_log WHERE record_date < ? AND symbol LIKE 'NIFTY%'
    """, (current_date_str,)).fetchone()
    
    prev_date_str = res[0]
    
    if not prev_date_str:
        print(f"[NSE Indices] No previous data found before {current_date_str}. Skipping change calc.")
        conn.close()
        return

    print(f"[NSE Indices] Calculating changes for {current_date_str} vs {prev_date_str}...")
    
    try:
        cursor.execute(f"""
        INSERT OR REPLACE INTO nse_delivery_log (
            record_date, symbol, price_close, price_open, price_high, price_low,
            volume_total, delivery_qty, delivery_pct,
            price_change_pct, volume_change_pct, delivery_change_pct,
            pe_ratio, pb_ratio, dividend_yield, turnover_crs,
            instrument_type
        )
        SELECT
            curr.record_date, curr.symbol, curr.price_close, curr.price_open, curr.price_high, curr.price_low,
            curr.volume_total, curr.delivery_qty, curr.delivery_pct, curr.price_change_pct,
            ((curr.volume_total - prev.volume_total) * 100.0 / prev.volume_total),
            (curr.delivery_pct - prev.delivery_pct),
            curr.pe_ratio, curr.pb_ratio, curr.dividend_yield, curr.turnover_crs,
            'INDEX'
        FROM nse_delivery_log AS curr
        JOIN nse_delivery_log AS prev ON curr.symbol = prev.symbol
        WHERE curr.record_date = '{current_date_str}' AND prev.record_date = '{prev_date_str}'
        AND prev.volume_total > 0
        AND curr.instrument_type = 'INDEX';
        """)
        conn.commit()
        print("[NSE Indices] Metrics updated successfully.")
    except Exception as e:
        print(f"[NSE Indices] Error updating metrics: {e}")
        
    conn.close()

def backfill_data(days=0, start_date=None, force=False):
    """
    Backfills data for the last N days or from a specific start date.
    If force is False, skips dates already present in the DB.
    """
    today = datetime.now().date()
    
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
            if start_dt > today:
                print(f"[NSE Indices] Start date {start_date} is in the future. Skipping.")
                return
            days = (today - start_dt).days
        except ValueError:
            print(f"[NSE Indices] Invalid date format for --start-date: {start_date}. Expected YYYY-MM-DD.")
            return

    if days < 0:
        return

    # Check for existing dates if not forcing (checking a known index)
    existing_dates = set()
    if not force:
        conn = get_db_connection()
        res = conn.execute("SELECT DISTINCT record_date FROM nse_delivery_log WHERE symbol = 'NIFTY 50'").fetchall()
        existing_dates = {row[0] for row in res}
        conn.close()
        print(f"[NSE Indices] Found {len(existing_dates)} existing dates in DB.")

    for i in range(days, -1, -1):
        d = today - timedelta(days=i)
        d_str = d.strftime('%Y-%m-%d')
        
        if d.weekday() >= 5:
            continue
            
        is_holiday, h_desc = is_trading_holiday(d)
        if is_holiday:
            print(f"[NSE Indices] Skipping {d_str} (Trading Holiday: {h_desc})")
            continue
            
        if not force and d_str in existing_dates:
            print(f"[NSE Indices] Skipping {d_str} (already in DB). Use --force to overwrite.")
            continue

        print(f"\n[NSE Indices] Processing {d}...")
        df = fetch_indices_data(d)
        if df is not None:
            process_and_store_data(df, d)
            update_changes_for_date(d)
        else:
            print(f"[NSE Indices] No data found for {d}")
            
        time.sleep(random.uniform(1, 4))

def smart_sync():
    """
    Smart sync: finds the last date in DB and fetches from the next day to today.
    If no data exists, defaults to 7-day backfill.
    Used by cron/daily sync to avoid re-downloading existing data.
    """
    conn = get_db_connection()
    res = conn.execute("SELECT MAX(record_date) FROM nse_delivery_log WHERE symbol = 'NIFTY 50'").fetchone()
    conn.close()
    
    today = datetime.now().date()
    
    if res and res[0]:
        last_date = datetime.strptime(res[0], '%Y-%m-%d').date()
        days_gap = (today - last_date).days
        
        if days_gap <= 0:
            print(f"[NSE Indices] Already up to date (last: {last_date}). Nothing to sync.")
            return
        
        print(f"[NSE Indices] Last data: {last_date}. Syncing {days_gap} day(s) to {today}...")
        
        for i in range(days_gap, -1, -1):
            d = today - timedelta(days=i)
            if d <= last_date:
                continue
            if d.weekday() >= 5:
                continue
                
            is_holiday, h_desc = is_trading_holiday(d)
            if is_holiday:
                print(f"[NSE Indices] Skipping {d} (Trading Holiday: {h_desc})")
                continue
            
            print(f"\n[NSE Indices] Processing {d}...")
            df = fetch_indices_data(d)
            if df is not None:
                process_and_store_data(df, d)
                update_changes_for_date(d)
            
            time.sleep(random.uniform(1, 3))
    else:
        print("[NSE Indices] No existing data. Running 7-day initial backfill...")
        backfill_data(7)
    
    print("[NSE Indices] Smart sync complete.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", type=int, help="Days to backfill", default=0)
    parser.add_argument("--start-date", type=str, help="Start date to backfill from (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="Force refill even if data exists")
    parser.add_argument("--sync", action="store_true", help="Smart sync: fetch from last available date to today")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only log errors")
    args = parser.parse_args()

    if args.quiet:
        import sys, os
        sys.stdout = open(os.devnull, 'w')
    
    if args.backfill > 0 or args.start_date:
        backfill_data(days=args.backfill, start_date=args.start_date, force=args.force)
    elif args.sync:
        smart_sync()
    else:
        # Default: smart sync (used by cron)
        smart_sync()
