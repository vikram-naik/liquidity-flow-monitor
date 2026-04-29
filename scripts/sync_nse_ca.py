import sys
import os
import requests
import sqlite3
import pandas as pd
from tabulate import tabulate
from datetime import datetime, timedelta
import re
import argparse
import json

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.database import get_db_connection

def load_local_overrides():
    """Load overrides from the ca_overrides database table."""
    conn = get_db_connection()
    try:
        # Fetch all overrides
        df = pd.read_sql_query("SELECT symbol, ex_date, ratio_factor FROM ca_overrides", conn)
        overrides = {}
        for _, row in df.iterrows():
            sym = row['symbol'].upper()
            if sym not in overrides:
                overrides[sym] = {}
            # Ensure ex_date is string YYYY-MM-DD
            dt_str = str(row['ex_date'])[:10]
            overrides[sym][dt_str] = row['ratio_factor']
        return overrides
    except Exception as e:
        print(f"Warning: Could not load overrides from DB: {e}")
        return {}
    finally:
        conn.close()

def parse_nse_description(subject):
    """
    Parses NSE corporate action subject strings into a ratio factor.
    Examples:
    - "Bonus 4:1" -> factor 5.0 (1 share becomes 5)
    - "Face Value Split (Sub-Division) - From Rs 2/- Per Share To Re 1/- Per Share" -> factor 2.0
    - "Demerger" -> flag for ESTIMATE_RATIO
    """
    subject = subject.lower()
    total_factor = 1.0
    types = []
    
    # Bonus Parsing: "Bonus A:B" means B shares become B+A shares.
    # Factor = (A+B)/B = A/B + 1
    bonus_match = re.search(r'bonus (\d+):(\d+)', subject)
    if bonus_match:
        a, b = map(int, bonus_match.groups())
        total_factor *= (a + b) / b
        types.append("BONUS")
        
    # Split Parsing: "From Rs X to Re Y"
    # Factor = X/Y
    # Handle "Rs" or "Re" or "R[se]?"
    split_match = re.search(r'from r[se]\.? (\d+).+to r[se]\.? (\d+)', subject)
    if split_match:
        old_fv, new_fv = map(int, split_match.groups())
        total_factor *= old_fv / new_fv
        types.append("SPLIT")

    # Demerger / Amalgamation Parsing (No explicit ratio in string usually)
    if not types:
        if any(kw in subject for kw in ["demerger", "amalgamation", "arrangement", "capital reduction", "spin-off"]):
            return 'ESTIMATE_RATIO', 'DEMERGER_OR_SIMILAR'
        return None, None
    
    return total_factor, "+".join(sorted(types))

def estimate_ratio_factor(symbol, ex_date_str):
    """
    Estimates the ratio factor by looking at the price drop in the DB.
    Factor = Close(Ex-Date - 1) / Open(Ex-Date)
    """
    conn = get_db_connection()
    try:
        # Get the row on or immediately after the ex-date
        query_after = """
            SELECT record_date, price_open 
            FROM nse_delivery_log 
            WHERE symbol = ? AND record_date >= ?
            ORDER BY record_date ASC LIMIT 1
        """
        row_after = conn.execute(query_after, (symbol, ex_date_str)).fetchone()
        
        # Get the row immediately before the ex-date
        query_before = """
            SELECT record_date, price_close 
            FROM nse_delivery_log 
            WHERE symbol = ? AND record_date < ?
            ORDER BY record_date DESC LIMIT 1
        """
        row_before = conn.execute(query_before, (symbol, ex_date_str)).fetchone()
        
        if row_after and row_before:
            open_after = row_after[1]
            close_before = row_before[1]
            if open_after and open_after > 0:
                est_factor = close_before / open_after
                return round(est_factor, 4)
    finally:
        conn.close()
        
    print(f"  [Warning] Could not estimate DB ratio for {symbol} on {ex_date_str}. Defaulting to 1.0.")
    return 1.0

def get_nse_data(symbol, overrides=None):
    if overrides is None:
        overrides = {}
        
    # Load file-based overrides and merge with CLI overrides
    file_overrides = load_local_overrides().get(symbol.upper(), {})
    # CLI overrides take precedence over file-based overrides
    merged_overrides = {**file_overrides, **overrides}

    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Referer': f'https://www.nseindia.com/get-quotes/equity?symbol={symbol}'
    }
    session = requests.Session()
    session.headers.update(headers)
    
    try:
        # 1. Establish session
        session.get("https://www.nseindia.com", timeout=10)
        
        # 2. Fetch API
        url = f"https://www.nseindia.com/api/corporates-corporateActions?index=equities&symbol={symbol}"
        response = session.get(url, timeout=10)
        if response.status_code != 200:
            print(f"Error fetching data: {response.status_code}")
            return []
            
        data = response.json()
        parsed_actions = []
        
        for item in data:
            ex_date_str = item.get('exDate')
            subject = item.get('subject', '')
            
            factor, ca_type = parse_nse_description(subject)
            if factor:
                # ex_date usually "16-Jun-2025"
                dt = datetime.strptime(ex_date_str, '%d-%b-%Y')
                iso_date = dt.strftime('%Y-%m-%d')
                
                final_factor = 1.0
                source = ""
                
                # Check for merged overrides first
                if iso_date in merged_overrides:
                    final_factor = merged_overrides[iso_date]
                    source = "(Manual Override)"
                elif factor == 'ESTIMATE_RATIO':
                    final_factor = estimate_ratio_factor(symbol, iso_date)
                    source = "(DB Estimate)"
                else:
                    final_factor = factor
                    
                if source:
                    print(f"Parsed {ca_type} on {iso_date}: Factor {final_factor} {source}")
                
                parsed_actions.append({
                    'ex_date': iso_date,
                    'factor': final_factor,
                    'type': ca_type,
                    'subject': subject
                })
                
        return parsed_actions
    except Exception as e:
        print(f"Exception during NSE fetch: {e}")
        return []

def sync_symbol(symbol, ca_overrides=None):
    symbol = symbol.upper()
    print(f"\n--- Investigating NSE Corporate Actions for {symbol} ---", flush=True)
    
    nse_raw = get_nse_data(symbol, overrides=ca_overrides)
    if not nse_raw:
        print("No parsable corporate actions found on NSE.", flush=True)
        # We might still want to proceed to delete if user wants to clear? 
        # But usually we want to replace.
        # Let's see if there are current entries anyway.
    
    # Aggregate by date (multiply factors)
    agg_nse = {}
    for act in nse_raw:
        date = act['ex_date']
        if date not in agg_nse:
            agg_nse[date] = {'factor': 1.0, 'types': [], 'subjects': []}
        agg_nse[date]['factor'] *= act['factor']
        agg_nse[date]['types'].append(act['type'])
        agg_nse[date]['subjects'].append(act['subject'])

    new_rows = []
    for date in sorted(agg_nse.keys(), reverse=True):
        info = agg_nse[date]
        new_rows.append([symbol, date, "+".join(sorted(set(info['types']))), round(info['factor'], 4)])

    # Get current DB entries
    conn = get_db_connection()
    current_db = pd.read_sql("SELECT symbol, ex_date, ca_type, ratio_factor FROM corporate_actions WHERE symbol = ?", conn, params=(symbol,))
    conn.close()

    print("\nCURRENT DATABASE ENTRIES (To be DELETED):", flush=True)
    if current_db.empty:
        print("None", flush=True)
    else:
        print(tabulate(current_db, headers='keys', tablefmt='grid', showindex=False), flush=True)

    print("\nPROPOSED NSE ENTRIES (To be INSERTED):", flush=True)
    headers = ["Symbol", "Ex-Date", "Type", "Ratio Factor"]
    if not new_rows:
        print("None", flush=True)
    else:
        print(tabulate(new_rows, headers=headers, tablefmt='grid'), flush=True)

    if not new_rows and current_db.empty:
        print("\nNo changes needed.")
        return

    if getattr(args, 'yes', False):
        confirm = 'y'
    else:
        confirm = input(f"\nProceed with OVERWRITING corporate actions for {symbol}? [y/N]: ")
        
    if confirm.lower() == 'y':
        conn = get_db_connection()
        try:
            with conn:
                conn.execute("DELETE FROM corporate_actions WHERE symbol = ?", (symbol,))
                for row in new_rows:
                    conn.execute("""
                        INSERT INTO corporate_actions (symbol, ex_date, ca_type, ratio_factor)
                        VALUES (?, ?, ?, ?)
                    """, row)
            print(f"Successfully updated corporate actions for {symbol}.")
        except Exception as e:
            print(f"Error updating database: {e}")
        finally:
            conn.close()
    else:
        print("Update cancelled.")

def parse_overrides(override_str):
    overrides = {}
    if not override_str:
        return overrides
    try:
        pairs = override_str.split(',')
        for pair in pairs:
            date_str, factor_str = pair.split(':')
            # Validate date
            datetime.strptime(date_str, '%Y-%m-%d')
            overrides[date_str.strip()] = float(factor_str)
    except Exception as e:
        print(f"Error parsing overrides '{override_str}': {e}")
        print("Format should be 'YYYY-MM-DD:FACTOR,YYYY-MM-DD:FACTOR'")
        sys.exit(1)
    return overrides

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync corporate actions from NSE")
    parser.add_argument("symbol", type=str, nargs="?", help="Stock symbol (e.g., ABFRL)")
    parser.add_argument("--watchlist", type=str, help="Sync all symbols in a specific watchlist")
    parser.add_argument("--all", action="store_true", help="Sync all symbols found in the delivery log")
    parser.add_argument("--ca-override", type=str, help="Manual ratio factor overrides in format 'YYYY-MM-DD:FACTOR,YYYY-MM-DD:FACTOR' (Only for single symbol sync)")
    parser.add_argument("--yes", action="store_true", help="Automatically confirm updates")
    
    args = parser.parse_args()
    
    symbols = []
    if args.symbol:
        symbols.append(args.symbol.upper())
    elif args.watchlist:
        conn = get_db_connection()
        try:
            query = """
                SELECT symbol FROM watchlist_items 
                JOIN watchlists ON watchlists.id = watchlist_items.watchlist_id
                WHERE watchlists.name = ?
            """
            symbols = [row[0] for row in conn.execute(query, (args.watchlist,)).fetchall()]
        finally:
            conn.close()
    elif args.all:
        conn = get_db_connection()
        try:
            # Sync symbols that are in ANY watchlist first
            query = "SELECT DISTINCT symbol FROM watchlist_items"
            symbols = [row[0] for row in conn.execute(query).fetchall()]
        finally:
            conn.close()
    else:
        parser.print_help()
        sys.exit(1)

    if not symbols:
        print("No symbols found to sync.")
        sys.exit(0)

    overrides = parse_overrides(args.ca_override) if args.symbol else {}
    
    for symbol in symbols:
        try:
            sync_symbol(symbol, ca_overrides=overrides)
        except Exception as e:
            print(f"Error syncing {symbol}: {e}")

