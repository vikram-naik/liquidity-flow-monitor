import sqlite3
import os
import pandas as pd
from tabulate import tabulate

DB_PATH = "liquidity_monitor.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    print("--- trading_positions count ---")
    pos_count = conn.execute("SELECT COUNT(*) FROM trading_positions").fetchone()[0]
    print(f"Total positions in DB: {pos_count}")
    
    print("\n--- trading_positions since 2025-12-01 ---")
    pos_df = pd.read_sql_query(
        "SELECT * FROM trading_positions WHERE entry_date >= '2025-12-01' OR exit_date >= '2025-12-01'", 
        conn
    )
    if pos_df.empty:
        print("No positions since 2025-12-01")
    else:
        print(tabulate(pos_df, headers='keys', tablefmt='psql'))
        
    print("\n--- trading_signals since 2025-12-01 ---")
    sig_df = pd.read_sql_query(
        "SELECT * FROM trading_signals WHERE signal_date >= '2025-12-01' OR entry_date >= '2025-12-01'", 
        conn
    )
    if sig_df.empty:
        print("No signals since 2025-12-01")
    else:
        print(f"Total signals since 2025-12-01: {len(sig_df)}")
        print(tabulate(sig_df.head(20), headers='keys', tablefmt='psql'))

    conn.close()

if __name__ == "__main__":
    main()
