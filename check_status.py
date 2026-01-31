
import sqlite3
import pandas as pd
from datetime import datetime

DB_PATH = "liquidity_monitor.db"

def check_status():
    conn = sqlite3.connect(DB_PATH)
    
    print("=== Database Status Check ===\n")
    
    # Check Exchanges
    print("--- Exchanges ---")
    exchanges = pd.read_sql_query("SELECT * FROM exchanges", conn)
    print(exchanges)
    print("\n")
    
    # Check Instruments
    print("--- Instruments ---")
    instruments = pd.read_sql_query("""
        SELECT i.id, i.symbol, i.asset_class, e.name as exchange_name 
        FROM instruments i 
        JOIN exchanges e ON i.exchange_id = e.id
    """, conn)
    print(instruments)
    print("\n")
    
    # Check Margin Logs Summary
    print("--- Margin Data Summary ---")
    margin_summary = pd.read_sql_query("""
        SELECT 
            e.name as exchange,
            i.symbol,
            COUNT(*) as count,
            MIN(m.timestamp) as first_date,
            MAX(m.timestamp) as last_date
        FROM margin_logs m
        JOIN instruments i ON m.instrument_id = i.id
        JOIN exchanges e ON i.exchange_id = e.id
        GROUP BY e.name, i.symbol
        ORDER BY e.name, i.symbol
    """, conn)
    
    if margin_summary.empty:
        print("No margin data found.")
    else:
        print(margin_summary)
    print("\n")
    
    # Check Yield Logs (FRED)
    print("--- FRED / Yield Data Summary ---")
    yield_summary = pd.read_sql_query("""
        SELECT 
            currency,
            tenor,
            COUNT(*) as count,
            MIN(timestamp) as first_date,
            MAX(timestamp) as last_date
        FROM yield_logs
        GROUP BY currency, tenor
    """, conn)
    
    if yield_summary.empty:
        print("No yield data found.")
    else:
        print(yield_summary)
    print("\n")

    conn.close()

if __name__ == "__main__":
    check_status()
