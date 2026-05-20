#!/usr/bin/env python3
"""
Script to automatically revalidate (add / delete) index constituents in watchlists.
It checks existing watchlists against the supported NSE indices. If a watchlist's
name matches an index, it downloads the latest constituents from the NSE archives
and updates the local database by adding missing symbols and deleting stale ones.
"""

import sys
import os
import sqlite3
import pandas as pd
import requests
import io
import traceback
import argparse
from datetime import datetime

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from src.database import DB_PATH
from src.api.main import NSE_INDICES

def sync_watchlists(quiet=False):
    if quiet:
        sys.stdout = open(os.devnull, 'w')
        
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting Index Watchlists Sync...")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, name FROM watchlists")
        watchlists = cursor.fetchall()
        
        updated_count = 0
        
        for wl_id, wl_name in watchlists:
            idx_name = wl_name.strip().upper()
            csv_file = None
            
            for name, filename in NSE_INDICES.items():
                if name.upper() == idx_name:
                    csv_file = filename
                    break
                    
            if not csv_file:
                continue  # Not an index-based watchlist or unsupported
                
            print(f"Checking index: {idx_name} (Watchlist ID: {wl_id})")
            url = f"https://nsearchives.nseindia.com/content/indices/{csv_file}"
            
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                response = requests.get(url, headers=headers, timeout=15)
                
                if response.status_code != 200:
                    print(f"  [ERROR] Failed to fetch from NSE: HTTP {response.status_code}")
                    continue
                    
                df = pd.read_csv(io.StringIO(response.text))
                df.columns = [c.strip().lower() for c in df.columns]
                
                if 'symbol' not in df.columns:
                    print(f"  [ERROR] NSE CSV format changed: 'symbol' column missing.")
                    continue
                    
                latest_symbols = df['symbol'].dropna().astype(str).tolist()
                latest_symbols = {s.strip().upper() for s in latest_symbols if s.strip()}
                
                if not latest_symbols:
                    print(f"  [WARNING] No symbols found in index file.")
                    continue
                    
                # Fetch existing symbols in this watchlist
                cursor.execute("SELECT symbol FROM watchlist_items WHERE watchlist_id = ?", (wl_id,))
                existing_symbols = {row[0].upper() for row in cursor.fetchall()}
                
                symbols_to_add = latest_symbols - existing_symbols
                symbols_to_remove = existing_symbols - latest_symbols
                
                if not symbols_to_add and not symbols_to_remove:
                    print(f"  [OK] Watchlist '{idx_name}' is already up-to-date ({len(latest_symbols)} symbols).")
                    continue
                    
                # Handle removals
                if symbols_to_remove:
                    print(f"  [REMOVING] {len(symbols_to_remove)} symbols: {', '.join(symbols_to_remove)}")
                    for sym in symbols_to_remove:
                        cursor.execute("DELETE FROM watchlist_items WHERE watchlist_id = ? AND symbol = ?", (wl_id, sym))
                        
                # Handle additions
                if symbols_to_add:
                    print(f"  [ADDING] {len(symbols_to_add)} symbols: {', '.join(symbols_to_add)}")
                    cursor.execute("SELECT MAX(display_order) FROM watchlist_items WHERE watchlist_id = ?", (wl_id,))
                    max_order = cursor.fetchone()[0] or 0
                    
                    for i, sym in enumerate(symbols_to_add):
                        cursor.execute("""
                            INSERT INTO watchlist_items (watchlist_id, symbol, display_order)
                            VALUES (?, ?, ?)
                        """, (wl_id, sym, max_order + i + 1))
                        
                conn.commit()
                updated_count += 1
                print(f"  [SUCCESS] Watchlist '{idx_name}' updated successfully.")
                
            except Exception as e:
                print(f"  [ERROR] Processing index '{idx_name}' failed: {e}")
                traceback.print_exc()
                
        conn.close()
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Index Watchlists Sync Complete. Updated {updated_count} watchlists.")
        
    except Exception as e:
        print(f"Error connecting to database or syncing: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync index constituents with local watchlists.")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only log errors")
    args = parser.parse_args()
    
    sync_watchlists(quiet=args.quiet)
