#!/usr/bin/env python3
import sqlite3
import sys
import os
import pandas as pd
from typing import Dict, List, Set

# Add the project root to sys.path to allow imports from src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.database import get_db_connection, DB_PATH
from src.analysis.data import get_stock_data

def init_screener_watchlists(cursor: sqlite3.Cursor) -> Dict[str, int]:
    """
    Ensures screener watchlists exist and clears them for a fresh scan.
    Returns a mapping of watchlist names to their IDs.
    """
    screener_names = [
        "SCR: Ignition",
        "SCR: Coil"
    ]
    
    watchlist_ids = {}
    for name in screener_names:
        # Ensure watchlist exists
        cursor.execute("INSERT OR IGNORE INTO watchlists (name) VALUES (?)", (name,))
        cursor.execute("SELECT id FROM watchlists WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row:
            w_id = row[0]
            watchlist_ids[name] = w_id
            # Clear existing items for a fresh slate
            cursor.execute("DELETE FROM watchlist_items WHERE watchlist_id = ?", (w_id,))
            
    return watchlist_ids

def get_scan_universe(cursor: sqlite3.Cursor) -> List[str]:
    """
    Fetches the symbols to scan from NIFTY 500 and CORE watchlists.
    """
    symbols: Set[str] = set()
    
    # 1. Fetch NIFTY 500 symbols
    cursor.execute("SELECT id FROM watchlists WHERE name LIKE 'NIFTY 500'")
    nifty_row = cursor.fetchone()
    if nifty_row:
        nifty_id = nifty_row[0]
        cursor.execute("SELECT symbol FROM watchlist_items WHERE watchlist_id = ?", (nifty_id,))
        symbols.update(row[0] for row in cursor.fetchall())
    else:
        print("Warning: 'NIFTY 500' watchlist not found.")
        
    # 2. Fetch CORE symbols
    cursor.execute("SELECT id FROM watchlists WHERE name LIKE 'CORE'")
    core_row = cursor.fetchone()
    if core_row:
        core_id = core_row[0]
        cursor.execute("SELECT symbol FROM watchlist_items WHERE watchlist_id = ?", (core_id,))
        symbols.update(row[0] for row in cursor.fetchall())
    else:
        print("Warning: 'CORE' watchlist not found.")
        
    return sorted(list(symbols))

def run_screener():
    """Main function to run the screener."""
    # Use a longer timeout to handle potential locks from other processes
    conn = sqlite3.connect(DB_PATH, timeout=30)
    cursor = conn.cursor()
    
    try:
        # Initialize watchlists and clear old data
        watchlist_map = init_screener_watchlists(cursor)
        conn.commit() # Commit here to release the lock after initialization
        
        # Get universe of stocks
        universe = get_scan_universe(cursor)
        if not universe:
            print("No symbols found in scan universe. Exiting.")
            return

        print(f"Starting scan for {len(universe)} symbols...")
        
        results_counts = {name: 0 for name in watchlist_map.keys()}
        
        for symbol in universe:
            sys.stdout.write(f"Scanning {symbol: <15}")
            sys.stdout.flush()
            
            try:
                # lookback_days=5 is enough to get today's status
                df, _, _ = get_stock_data(symbol, lookback_days=5, agg_period='daily')
                
                if df.empty:
                    sys.stdout.write(" [EMPTY]\n")
                    continue
                
                latest = df.iloc[-1]
                
                # Priority markers
                assigned = False
                if latest.get('is_ignition') == True:
                    w_id = watchlist_map["SCR: Ignition"]
                    results_counts["SCR: Ignition"] += 1
                    cursor.execute(
                        "INSERT OR IGNORE INTO watchlist_items (watchlist_id, symbol, display_order) VALUES (?, ?, ?)",
                        (w_id, symbol, results_counts["SCR: Ignition"])
                    )
                    sys.stdout.write(" [IGNITION]\n")
                    assigned = True
                # Priority markers
                assigned = False
                if latest.get('is_ignition') == True:
                    w_id = watchlist_map["SCR: Ignition"]
                    results_counts["SCR: Ignition"] += 1
                    cursor.execute(
                        "INSERT OR IGNORE INTO watchlist_items (watchlist_id, symbol, display_order) VALUES (?, ?, ?)",
                        (w_id, symbol, results_counts["SCR: Ignition"])
                    )
                    sys.stdout.write(" [IGNITION]\n")
                    assigned = True
                elif latest.get('is_coil') == True:
                    w_id = watchlist_map["SCR: Coil"]
                    results_counts["SCR: Coil"] += 1
                    cursor.execute(
                        "INSERT OR IGNORE INTO watchlist_items (watchlist_id, symbol, display_order) VALUES (?, ?, ?)",
                        (w_id, symbol, results_counts["SCR: Coil"])
                    )
                    sys.stdout.write(" [COIL]\n")
                    assigned = True
                
                if not assigned:
                    sys.stdout.write(" [SKIP]\n")
                else:
                    conn.commit() # Commit each insertion to keep transaction short
                    
            except Exception as e:
                sys.stdout.write(f" [ERROR: {str(e)}]\n")

        
        # Summary
        print("\n" + "="*30)
        print("SCREENER SUMMARY")
        print("="*30)
        for name, count in results_counts.items():
            print(f"{name: <20}: {count}")
        print("="*30)
        
        conn.commit()
    except Exception as e:
        print(f"An error occurred: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    run_screener()
