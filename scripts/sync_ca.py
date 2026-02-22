import yfinance as yf
import sqlite3
import pandas as pd
from src.database import get_db_connection
import os

def sync_corporate_actions():
    """
    Fetches stock splits using yfinance for all watchlisted symbols 
    and persists them to the corporate_actions table.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Get all active symbols from watchlists
    try:
        cursor.execute("SELECT DISTINCT symbol FROM watchlist_items")
        symbols = [row[0] for row in cursor.fetchall()]
    except sqlite3.Error as e:
        print(f"Error fetching symbols: {e}")
        conn.close()
        return

    print(f"Found {len(symbols)} symbols to sync.")
    new_inserts = 0

    # 2. Loop through symbols and fetch splits
    for symbol in symbols:
        yf_symbol = f"{symbol.upper()}.NS"
        print(f"Syncing {yf_symbol}...")
        
        try:
            ticker = yf.Ticker(yf_symbol)
            splits = ticker.splits
            
            if not splits.empty:
                for ex_date, ratio in splits.items():
                    ex_date_str = ex_date.strftime('%Y-%m-%d')
                    
                    # 3. Insert into database
                    cursor.execute("""
                        INSERT OR IGNORE INTO corporate_actions 
                        (symbol, ex_date, ca_type, ratio_factor) 
                        VALUES (?, ?, 'SPLIT', ?)
                    """, (symbol.upper(), ex_date_str, float(ratio)))
                    
                    if cursor.rowcount > 0:
                        print(f"  [NEW] {symbol}: Split ratio {ratio} on {ex_date_str}")
                        new_inserts += 1
            
        except Exception as e:
            print(f"  [ERROR] Failed to fetch data for {yf_symbol}: {e}")

    conn.commit()
    conn.close()
    print("-" * 30)
    print(f"Sync complete. New corporate actions inserted: {new_inserts}")

if __name__ == "__main__":
    sync_corporate_actions()
