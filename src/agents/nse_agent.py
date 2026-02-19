
"""
NSE Agent - Fetches Delivery & Volume Data from NSE Archives (Bhavcopy)
"""
import requests
import pandas as pd
import io
import zipfile
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


def fetch_legacy_data(date_obj):
    """
    Fetches and merges Price (CSV) and Delivery (DAT) files for pre-2020 dates.
    """
    date_str = date_obj.strftime('%d%m%Y')
    
    # Format components for URLs
    yyyy = date_obj.strftime("%Y")
    mmm = date_obj.strftime("%b").upper()
    dd = date_obj.strftime("%d")
    mm = date_obj.strftime("%m")
    
    # 1. Price Data URL (CSV inside ZIP)
    price_url = f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{yyyy}/{mmm}/cm{dd}{mmm}{yyyy}bhav.csv.zip"
    
    # 2. Delivery Data URL (DAT file, effectively CSV)
    delivery_url = f"https://nsearchives.nseindia.com/archives/equities/mto/MTO_{dd}{mm}{yyyy}.DAT"
    
    print(f"[NSE] Fetching Legacy Data for {date_str}...")
    
    try:
        s = requests.Session()
        s.headers.update(HEADERS)
        s.get("https://www.nseindia.com", timeout=10) # Cookie
        
        # --- Fetch Price ---
        r_price = s.get(price_url, timeout=15)
        if r_price.status_code == 404:
            print(f"[NSE] Price data not found for {date_str} (Likely Holiday/Weekend)")
            return None
        elif r_price.status_code != 200:
            print(f"[NSE] Price fetch failed: {r_price.status_code}")
            return None
            
        df_price = None
        with zipfile.ZipFile(io.BytesIO(r_price.content)) as z:
            csv_name = z.namelist()[0]
            with z.open(csv_name) as f:
                df_price = pd.read_csv(f)
                
        # --- Fetch Delivery ---
        r_deliv = s.get(delivery_url, timeout=15)
        df_deliv = None
        if r_deliv.status_code == 200:
            # Skip first 4 lines (header junk), use line 5 as header?
            # Actually, standard MTO file:
            # Line 1: Header info
            # Line 2: Header info
            # Line 3: Trade Date info
            # Line 4: Column Headers -> Record Type,Sr No,Name of Security,Quantity Traded,Deliverable Quantity...
            # We can skip rows usually. Let's try reading with 'header=3' (0-indexed -> row 4)
            # OR better: read strictly based on known columns to be safe.
            
            content = r_deliv.content.decode('utf-8')
            # Filter for lines starting with '20,' (Equities)
            data_lines = [line for line in content.splitlines() if line.startswith('20,')]
            
            if data_lines:
                # Manual CSV parsing for robustness
                # Format: Record Type, Sr No, Name of Security, Series, Quantity Traded, Deliverable Quantity, % Deliverable
                inv_data = [line.split(',') for line in data_lines]
                df_deliv = pd.DataFrame(inv_data, columns=[
                    'RecordType', 'SrNo', 'NameOfSecurity', 'SERIES', 'Volume', 'DeliveryQty', 'DeliveryPct'
                ])
            else:
                print(f"[NSE] No Equity delivery data found in MTO file.")
        
        if df_price is None:
            return None

        # --- Merge ---
        # Cleanup column names
        df_price.columns = [c.strip().upper() for c in df_price.columns]
        
        # Standardize Price DF
        # Rename to match standard format expected by process_and_store_data
        # Standard: SYMBOL, SERIES, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, PREV_CLOSE, TTL_TRD_QNTY, DELIV_QTY, DELIV_PER
        
        # Legacy Price Cols: SYMBOL, SERIES, OPEN, HIGH, LOW, CLOSE, LAST, PREVCLOSE, TOTTRDQTY, TOTTRDVAL, TIMESTAMP...
        df_price.rename(columns={
            'OPEN': 'OPEN_PRICE',
            'HIGH': 'HIGH_PRICE',
            'LOW': 'LOW_PRICE',
            'CLOSE': 'CLOSE_PRICE',
            'PREVCLOSE': 'PREV_CLOSE',
            'TOTTRDQTY': 'TTL_TRD_QNTY'
        }, inplace=True)
        
        # If we have delivery data, merge it. If not, fill with 0.
        if df_deliv is not None:
            df_deliv['NameOfSecurity'] = df_deliv['NameOfSecurity'].str.strip()
            df_deliv['SERIES'] = df_deliv['SERIES'].str.strip()
            
            # Merge on Symbol AND Series
            merged = pd.merge(
                df_price, 
                df_deliv[['NameOfSecurity', 'SERIES', 'DeliveryQty', 'DeliveryPct']], 
                left_on=['SYMBOL', 'SERIES'], 
                right_on=['NameOfSecurity', 'SERIES'], 
                how='left'
            )
            
            merged.rename(columns={
                'DeliveryQty': 'DELIV_QTY', 
                'DeliveryPct': 'DELIV_PER'
            }, inplace=True)
            
            # Fill NaNs (for series not in MTO, e.g. indices or weird stuff)
            merged['DELIV_QTY'] = merged['DELIV_QTY'].fillna(0)
            merged['DELIV_PER'] = merged['DELIV_PER'].fillna(0)
            
            return merged
        else:
             # No delivery data available
             df_price['DELIV_QTY'] = 0
             df_price['DELIV_PER'] = 0
             return df_price

    except Exception as e:
        print(f"[NSE] Error fetching legacy {date_str}: {e}")
        return None

def fetch_bhavcopy(date_obj):
    """
    Fetches the full bhavcopy for a specific date.
    Returns DataFrame or None if not found/holiday.
    """
    # CUTOFF DATE for New Format: Jan 1st 2020
    # Normalize to date object if it's a datetime
    check_date = date_obj.date() if isinstance(date_obj, datetime) else date_obj
    if check_date < datetime(2020, 1, 1).date():
        return fetch_legacy_data(date_obj)

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
    
    # Aggressively strip whitespace from all string columns
    df.columns = [c.strip() for c in df.columns]
    for col in df.select_dtypes(include=['object']).columns:
        df[col] = df[col].str.strip()
    
    # Filter for Series = 'EQ' (Equity) or 'BE' (Book Entry / Trade-to-Trade)
    if 'SERIES' in df.columns:
        df = df[df['SERIES'].isin(['EQ', 'BE'])].copy()
    else:
        # Fallback if SERIES col is missing or named differently
        print("[NSE] Warning: 'SERIES' column missing. Processing all rows.")
    
    records = []
    # DEBUG: Track if we see WOCKPHARMA
    found_wock = False
    
    for _, row in df.iterrows():
        symbol = row['SYMBOL']
        if "WOCKPHARMA" in symbol:
            print(f"[DEBUG] Found WOCKPHARMA row: Series={row.get('SERIES')}, Close={row['CLOSE_PRICE']}")
            found_wock = True

        close = float(row['CLOSE_PRICE'])
        prev_close = float(row['PREV_CLOSE']) if pd.notna(row.get('PREV_CLOSE')) else close
        volume = int(row['TTL_TRD_QNTY'])
        
        # Handle '-' in delivery columns (common in BE series)
        # If BE series has '-', it implies 100% delivery (Trade-to-Trade)
        
        d_qty_str = str(row['DELIV_QTY']).strip()
        if d_qty_str in ['-', '', 'nan', 'None']:
            if row['SERIES'] == 'BE':
                deliv_qty = volume
            else:
                deliv_qty = 0
        else:
            try:
                deliv_qty = int(float(d_qty_str))
            except:
                deliv_qty = 0

        d_pct_str = str(row['DELIV_PER']).strip()
        if d_pct_str in ['-', '', 'nan', 'None']:
            if row['SERIES'] == 'BE':
                deliv_pct = 100.0
            else:
                deliv_pct = 0.0
        else:
            try:
                deliv_pct = float(d_pct_str)
            except:
                deliv_pct = 0.0
        
        # Price change % from prev close (available directly in bhavcopy)
        price_chg = ((close - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
        
        # New OHLC fields
        open_price = float(row['OPEN_PRICE']) if pd.notna(row.get('OPEN_PRICE')) else 0.0
        high_price = float(row['HIGH_PRICE']) if pd.notna(row.get('HIGH_PRICE')) else 0.0
        low_price = float(row['LOW_PRICE']) if pd.notna(row.get('LOW_PRICE')) else 0.0

        records.append((
            record_date.strftime('%Y-%m-%d'),
            symbol,
            close,
            open_price,
            high_price,
            low_price,
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
        (record_date, symbol, price_close, price_open, price_high, price_low, volume_total, delivery_qty, delivery_pct, 
         price_change_pct, volume_change_pct, delivery_change_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            record_date, symbol, price_close, price_open, price_high, price_low, 
            volume_total, delivery_qty, delivery_pct, 
            price_change_pct, volume_change_pct, delivery_change_pct
        )
        SELECT 
            curr.record_date, curr.symbol, curr.price_close, curr.price_open, curr.price_high, curr.price_low,
            curr.volume_total, curr.delivery_qty, curr.delivery_pct, curr.price_change_pct,
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
                print(f"[NSE] Start date {start_date} is in the future. Skipping.")
                return
            days = (today - start_dt).days
        except ValueError:
            print(f"[NSE] Invalid date format for --start-date: {start_date}. Expected YYYY-MM-DD.")
            return

    if days < 0:
        return

    # Check for existing dates if not forcing
    existing_dates = set()
    if not force:
        conn = get_db_connection()
        res = conn.execute("SELECT DISTINCT record_date FROM nse_delivery_log").fetchall()
        existing_dates = {row[0] for row in res}
        conn.close()
        print(f"[NSE] Found {len(existing_dates)} existing dates in DB.")

    for i in range(days, -1, -1):
        d = today - timedelta(days=i)
        d_str = d.strftime('%Y-%m-%d')
        
        if d.weekday() >= 5:
            continue
            
        if not force and d_str in existing_dates:
            print(f"[NSE] Skipping {d_str} (already in DB). Use --force to overwrite.")
            continue

        print(f"\n[NSE] Processing {d}...")
        df = fetch_bhavcopy(d)
        if df is not None:
            process_and_store_data(df, d)
            update_changes_for_date(d)
        else:
            print(f"[NSE] No data found for {d}")
            
        time.sleep(random.uniform(1, 4))

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

def show_db_status():
    """
    Displays the current status of the NSE delivery database.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    res = cursor.execute("""
        SELECT MIN(record_date), MAX(record_date), COUNT(DISTINCT record_date), COUNT(*) 
        FROM nse_delivery_log
    """).fetchone()
    
    conn.close()
    
    if res and res[0]:
        min_date, max_date, distinct_dates, total_records = res
        print("\n=== NSE Delivery Database Status ===")
        print(f"Date Range     : {min_date} to {max_date}")
        print(f"Trading Days   : {distinct_dates}")
        print(f"Total Records  : {total_records:,}")
        print("===================================\n")
    else:
        print("[NSE] Database is empty.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", type=int, help="Days to backfill", default=0)
    parser.add_argument("--start-date", type=str, help="Start date to backfill from (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="Force refill even if data exists")
    parser.add_argument("--sync", action="store_true", help="Smart sync: fetch from last available date to today")
    parser.add_argument("--info", action="store_true", help="Show database status (date range, records)")
    args = parser.parse_args()
    
    if args.info:
        show_db_status()
    elif args.backfill > 0 or args.start_date:
        backfill_data(days=args.backfill, start_date=args.start_date, force=args.force)
    elif args.sync:
        smart_sync()
    else:
        # Default: smart sync (used by cron)
        smart_sync()

