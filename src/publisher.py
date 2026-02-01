import sqlite3
import requests
import json
import argparse
import sys
from datetime import datetime, date

def get_data_from_db(db_path, table, limit=None):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    limit_str = f"LIMIT {limit}" if limit else ""
    
    if table == "margin_logs":
        query = f"""
            SELECT l.*, i.symbol, e.name as exchange 
            FROM margin_logs l
            JOIN instruments i ON l.instrument_id = i.id
            JOIN exchanges e ON i.exchange_id = e.id
            ORDER BY l.timestamp DESC {limit_str}
        """
    elif table == "trading_holidays":
        query = f"""
            SELECT h.*, e.name as exchange 
            FROM trading_holidays h
            JOIN exchanges e ON h.exchange_id = e.id
            {limit_str}
        """
    else:
        query = f"SELECT * FROM {table} ORDER BY timestamp DESC {limit_str}"
        
    cursor.execute(query)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def push_to_api(url, data, user, password, chunk_size=100):
    total_inserted = 0
    # Process in chunks to avoid large payload issues
    for i in range(0, len(data), chunk_size):
        chunk = data[i:i + chunk_size]
        try:
            print(f"  -> Uploading chunk {i//chunk_size + 1} ({len(chunk)} records)...")
            response = requests.post(url, json=chunk, auth=(user, password))
            response.raise_for_status()
            res_json = response.json()
            total_inserted += res_json.get('inserted', 0)
        except Exception as e:
            print(f"Error pushing chunk to {url}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response: {e.response.text}")
            elif 'response' in locals() and response is not None:
                 print(f"Response: {response.text}")
            return total_inserted
    return total_inserted

def main():
    parser = argparse.ArgumentParser(description="LFM Local Data Publisher")
    parser.add_argument("--host", required=True, help="Remote host URL (e.g. https://www.mfmunshi.com)")
    parser.add_argument("--user", required=True, help="API User")
    parser.add_argument("--pass", dest="password", required=True, help="API Password")
    parser.add_argument("--db", default="liquidity_monitor.db", help="Local DB path")
    parser.add_argument("--limit", type=int, default=10, help="Number of records to sync (if not backfilling)")
    parser.add_argument("--backfill", action="store_true", help="Sync ALL historical data")
    parser.add_argument("--chunk", type=int, default=100, help="Records per API request")
    
    args = parser.parse_args()
    
    limit = None if args.backfill else args.limit
    base_url = args.host.rstrip('/')
    
    # 1. Sync Margins
    print(f"Syncing margins to {base_url}/lfm/api/upload/margins ...")
    margins = get_data_from_db(args.db, "margin_logs", limit)
    if margins:
        inserted = push_to_api(f"{base_url}/lfm/api/upload/margins", margins, args.user, args.password, args.chunk)
        print(f"Success: {inserted} margin records synced.")
            
    # 2. Sync Yields
    print(f"Syncing yields to {base_url}/lfm/api/upload/yields ...")
    yields = get_data_from_db(args.db, "yield_logs", limit)
    if yields:
        inserted = push_to_api(f"{base_url}/lfm/api/upload/yields", yields, args.user, args.password, args.chunk)
        print(f"Success: {inserted} yield records synced.")

    # 3. Sync Holidays (Always sync all if backfilling or small set)
    print(f"Syncing holidays to {base_url}/lfm/api/upload/holidays ...")
    holidays = get_data_from_db(args.db, "trading_holidays", None if args.backfill else 50)
    if holidays:
        inserted = push_to_api(f"{base_url}/lfm/api/upload/holidays", holidays, args.user, args.password, args.chunk)
        print(f"Success: {inserted} holiday records synced.")

if __name__ == "__main__":
    main()
