import yfinance as yf
import pandas as pd

def check_ba_ca():
    symbol = "BAJFINANCE.NS"
    ticker = yf.Ticker(symbol)
    print(f"--- Checking {symbol} ---")
    
    print("\nTicker Info Keys:")
    # print(ticker.info.keys())
    # Standard info might contain bonus factors?
    info = ticker.info
    for key in ['lastSplitDate', 'lastSplitFactor', 'bonusRow']: # Guessing keys
        if key in info:
            print(f"{key}: {info[key]}")
    
    # Try getting dividends/splits specifically in a way that might show bonus
    print("\nSplits:")
    print(ticker.splits)

if __name__ == "__main__":
    check_ba_ca()
