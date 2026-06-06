import sqlite3
import pandas as pd
from tabulate import tabulate

def main():
    conn = sqlite3.connect("liquidity_monitor.db")
    df = pd.read_sql_query(
        "SELECT id, symbol, signal_date, entry_date, signal_type, acted_upon, skip_reason FROM trading_signals WHERE signal_date >= '2025-12-01'",
        conn
    )
    print("--- Signals since 2025-12-01 ---")
    if df.empty:
        print("None")
    else:
        print(tabulate(df, headers='keys', tablefmt='psql', showindex=False))
    conn.close()

if __name__ == "__main__":
    main()
