import sqlite3
import yfinance as yf
import pandas as pd
from tabulate import tabulate
import os

DB_PATH = "liquidity_monitor.db"

def get_core_symbols():
    """Fetches symbols from the CORE watchlist."""
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file {DB_PATH} not found.")
        return []
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    query = """
    SELECT symbol FROM watchlist_items 
    WHERE watchlist_id = (SELECT id FROM watchlists WHERE name = 'CORE');
    """
    try:
        cursor.execute(query)
        symbols = [row[0] for row in cursor.fetchall()]
        return symbols
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return []
    finally:
        conn.close()

def check_splits(symbols):
    """Checks for splits for each symbol using yFinance."""
    results = []
    
    for symbol in symbols:
        # Append .NS for NSE stocks
        yf_symbol = f"{symbol}.NS"
        print(f"Checking {yf_symbol}...")
        
        try:
            ticker = yf.Ticker(yf_symbol)
            splits = ticker.splits
            
            if not splits.empty:
                for date, ratio in splits.items():
                    results.append({
                        "Symbol": symbol,
                        "Date": date.strftime('%Y-%m-%d'),
                        "Split Ratio": ratio
                    })
            else:
                # Some tickers might not have splits in the dedicated 'splits' property
                # or we might want to be sure by checking history if needed, 
                # but 'splits' is usually the right place.
                pass
                
        except Exception as e:
            print(f"Error fetching data for {yf_symbol}: {e}")

    return results

def main():
    import sys
    
    if len(sys.argv) > 1:
        symbols = sys.argv[1:]
        print(f"Checking provided symbols: {', '.join(symbols)}")
    else:
        print("Fetching symbols from CORE watchlist...")
        symbols = get_core_symbols()
        if not symbols:
            print("No symbols found in CORE watchlist.")
            return
        print(f"Found {len(symbols)} symbols: {', '.join(symbols)}")
    
    print("-" * 50)
    
    split_data = check_splits(symbols)
    
    if split_data:
        df = pd.DataFrame(split_data)
        # Rename column to be more inclusive
        df.columns = ["Symbol", "Date", "Action Ratio"]
        print("\nCorporate Actions Found (Splits/Bonuses)")
        print(tabulate(df, headers='keys', tablefmt='grid', showindex=False))
        print("\nNote: Action Ratio > 1 indicates a Split or Bonus.")
        print("For example, 10.0 = 1:10 Split, 2.0 = 1:1 Bonus.")
        print("Please cross-check these with official NSE notifications.")
    else:
        print("\nNo corporate actions (splits/bonuses) found.")

if __name__ == "__main__":
    main()
