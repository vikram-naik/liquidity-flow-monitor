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
            SELECT l.*, i.symbol, i.asset_class, e.name as exchange 
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
        if table == "treasury_issuance_plan":
            sort_col = "auction_date"
        else:
            sort_col = "record_date" if "treasury" in table else "timestamp"
        query = f"SELECT * FROM {table} ORDER BY {sort_col} DESC {limit_str}"
        
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

    # 4. Sync Treasury Auctions
    print(f"Syncing treasury auctions to {base_url}/lfm/api/upload/treasury-auctions ...")
    auctions = get_data_from_db(args.db, "treasury_auctions", limit)
    if auctions:
        inserted = push_to_api(f"{base_url}/lfm/api/upload/treasury-auctions", auctions, args.user, args.password, args.chunk)
        print(f"Success: {inserted} auction records synced.")

    # 5. Sync Treasury Liquidity
    print(f"Syncing treasury liquidity to {base_url}/lfm/api/upload/treasury-liquidity ...")
    liquidity = get_data_from_db(args.db, "treasury_liquidity", limit)
    if liquidity:
        inserted = push_to_api(f"{base_url}/lfm/api/upload/treasury-liquidity", liquidity, args.user, args.password, args.chunk)
        print(f"Success: {inserted} liquidity records synced.")

    # 6. Sync Treasury Debt
    print(f"Syncing treasury debt to {base_url}/lfm/api/upload/treasury-debt ...")
    debt = get_data_from_db(args.db, "treasury_debt_profile", limit)
    if debt:
        inserted = push_to_api(f"{base_url}/lfm/api/upload/treasury-debt", debt, args.user, args.password, args.chunk)
        print(f"Success: {inserted} debt records synced.")

    # 7. Sync Treasury Buybacks
    print(f"Syncing treasury buybacks to {base_url}/lfm/api/upload/treasury-buybacks ...")
    buybacks = get_data_from_db(args.db, "treasury_buybacks", limit)
    if buybacks:
        inserted = push_to_api(f"{base_url}/lfm/api/upload/treasury-buybacks", buybacks, args.user, args.password, args.chunk)
        print(f"Success: {inserted} buyback records synced.")

    # 8. Sync Treasury Flows
    print(f"Syncing treasury flows to {base_url}/lfm/api/upload/treasury-flows ...")
    flows = get_data_from_db(args.db, "treasury_daily_debt_flows", limit)
    if flows:
        inserted = push_to_api(f"{base_url}/lfm/api/upload/treasury-flows", flows, args.user, args.password, args.chunk)
        print(f"Success: {inserted} flow records synced.")

    # 9. Sync Maturity Schedule
    print(f"Syncing maturity schedule to {base_url}/lfm/api/upload/maturity-schedule ...")
    schedule = get_data_from_db(args.db, "treasury_maturity_schedule", limit)
    if schedule:
        inserted = push_to_api(f"{base_url}/lfm/api/upload/maturity-schedule", schedule, args.user, args.password, args.chunk)
        print(f"Success: {inserted} schedule records synced.")

    # 10. Sync Avg Interest Rates
    print(f"Syncing avg rates to {base_url}/lfm/api/upload/avg-rates ...")
    avg_rates = get_data_from_db(args.db, "treasury_avg_interest_rates", limit)
    if avg_rates:
        inserted = push_to_api(f"{base_url}/lfm/api/upload/avg-rates", avg_rates, args.user, args.password, args.chunk)
        print(f"Success: {inserted} avg rate records synced.")

    # 11. Sync Issuance Plan
    print(f"Syncing issuance plan to {base_url}/lfm/api/upload/issuance-plan ...")
    issuance_plan = get_data_from_db(args.db, "treasury_issuance_plan", limit)
    if issuance_plan:
        inserted = push_to_api(f"{base_url}/lfm/api/upload/issuance-plan", issuance_plan, args.user, args.password, args.chunk)
        print(f"Success: {inserted} issuance plan records synced.")

    # 12. Update sync timestamp on remote
    print(f"Updating sync timestamp on {base_url}/lfm/api/sync-timestamp ...")
    try:
        sync_time = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        response = requests.post(
            f"{base_url}/lfm/api/sync-timestamp",
            json={"timestamp": sync_time},
            auth=(args.user, args.password)
        )
        response.raise_for_status()
        print(f"Success: Sync timestamp updated to {sync_time}")
    except Exception as e:
        print(f"Warning: Could not update sync timestamp: {e}")
        # Non-fatal, continue

if __name__ == "__main__":
    main()

