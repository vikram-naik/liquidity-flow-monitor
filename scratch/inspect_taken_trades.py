import sqlite3
import pandas as pd
from tabulate import tabulate

def main():
    conn = sqlite3.connect("liquidity_monitor.db")
    df = pd.read_sql_query(
        "SELECT id, symbol, status, entry_date, exit_date, entry_price, exit_price, final_pnl_pct, entry_tag FROM trading_positions WHERE entry_date >= '2025-12-01'",
        conn
    )
    print("--- Executed positions since 2025-12-01 ---")
    if df.empty:
        print("None")
    else:
        print(tabulate(df, headers='keys', tablefmt='psql', showindex=False))
    conn.close()

if __name__ == "__main__":
    main()
