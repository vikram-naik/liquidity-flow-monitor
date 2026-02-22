import sys
import os
import requests
import sqlite3
import pandas as pd
from tabulate import tabulate
from datetime import datetime
import re

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.database import get_db_connection

def parse_nse_description(subject):
    """
    Parses NSE corporate action subject strings into a ratio factor.
    Examples:
    - "Bonus 4:1" -> factor 5.0 (1 share becomes 5)
    - "Face Value Split (Sub-Division) - From Rs 2/- Per Share To Re 1/- Per Share" -> factor 2.0
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

    if not types:
        return None, None
    
    return total_factor, "+".join(sorted(types))

def get_nse_data(symbol):
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
                parsed_actions.append({
                    'ex_date': iso_date,
                    'factor': factor,
                    'type': ca_type,
                    'subject': subject
                })
                
        return parsed_actions
    except Exception as e:
        print(f"Exception during NSE fetch: {e}")
        return []

def sync_symbol(symbol):
    symbol = symbol.upper()
    print(f"\n--- Investigating NSE Corporate Actions for {symbol} ---", flush=True)
    
    nse_raw = get_nse_data(symbol)
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

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python sync_nse_ca.py <SYMBOL>")
    else:
        sync_symbol(sys.argv[1])
