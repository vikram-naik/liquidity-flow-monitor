import os
import sys
import sqlite3
import pandas as pd
import yfinance as yf
from datetime import datetime, date, timedelta

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from src.utils.data_sync import get_last_instrument_date, BASE_DATE
from src.utils.trading_calendar import is_cme_trading_day

DB_PATH = "liquidity_monitor.db"

# Multipliers
MULTIPLIERS = {
    'GOLD': 100,
    'SILVER': 5000,
    'COPPER': 25000,
    'ES': 50,
    'NQ': 20
}

# Researched Margin Change Events (Maintenance Margins)
MARGIN_HISTORY = {
    'GOLD': [
        ('2025-01-01', 8500),   # Baseline Jan 2025
        ('2025-10-01', 9500),   # Q4 Volatility hike
        ('2025-12-29', 24000),  # Major surge hike
        ('2026-01-13', "5.5%"), # Switch to % based
        ('2026-01-30', "8.0%")  # Latest hike
    ],
    'SILVER': [
        ('2025-01-01', 9500),   # Baseline Jan 2025
        ('2025-09-25', 12000),  # First major hike
        ('2025-12-12', 22000),  # Surge hike
        ('2025-12-31', 32500),  # Year-end peak
        ('2026-01-13', "9.0%"), # Switch to % based
        ('2026-01-30', "15.0%") # Latest hike
    ],
    'COPPER': [
        ('2025-01-01', 5000),   # Baseline Jan 2025
        ('2025-05-15', 6000),   # Mid-year surge
        ('2025-12-30', 10000),  # End-year level
        ('2026-01-30', 12000)   # Latest hike
    ],
    'ES': [
        ('2025-01-01', 12000),   # Baseline Jan 2025
        ('2026-01-13', "12.0%")  # Switch to % based
    ],
    'NQ': [
        ('2025-01-01', 18000),   # Baseline Jan 2025
        ('2026-01-13', "15.0%")  # Switch to % based
    ]
}

# Mapping for Yahoo Finance
YF_TICKERS = {
    'GOLD': 'GC=F',
    'SILVER': 'SI=F',
    'COPPER': 'HG=F',
    'ES': 'ES=F',
    'NQ': 'NQ=F'
}

def get_margin_for_date(symbol, target_date, current_price):
    """Resolve margin based on historical event list and current price"""
    mult = MULTIPLIERS.get(symbol, 1)
    events = MARGIN_HISTORY.get(symbol, [])
    
    current_val = None
    for event_date_str, val in events:
        event_date = datetime.strptime(event_date_str, "%Y-%m-%d").date()
        if event_date <= target_date:
            current_val = val
        else:
            break
            
    if isinstance(current_val, str) and "%" in current_val:
        # Percentage based on Notional Value
        pct = float(current_val.replace("%", "")) / 100.0
        return (current_price * mult) * pct 
        
    return float(current_val) if current_val else 0.0

def backfill_cme_historical():
    """Fetch prices from YF and apply margin history to seed DB"""
    print(f"=== CME Resilient Backfill ===")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get Instrument IDs
    cursor.execute("SELECT id, symbol FROM instruments")
    instr_map = {symbol: idx for idx, symbol in cursor.fetchall()}
    
    today_str = datetime.now().strftime("%Y-%m-%d")

    for symbol, ticker in YF_TICKERS.items():
        if symbol not in instr_map:
            print(f"  ⚠️ Skipping {symbol} (not in DB)")
            continue
            
        last_date = get_last_instrument_date(symbol)
        start_date = last_date + timedelta(days=1)
        
        if start_date >= date.today():
             print(f"  ✓ {symbol} is already up to date ({last_date})")
             continue

        print(f"  Catching up {symbol} ({ticker}) from {start_date}...")
        instr_id = instr_map[symbol]
        
        # Download data
        try:
            df = yf.download(ticker, start=start_date.strftime("%Y-%m-%d"), end=today_str, progress=False)
        except Exception as e:
            print(f"  ❌ Error fetching YF data for {symbol}: {e}")
            continue

        if df.empty:
            print(f"  ⚠️ No price history found for {symbol} in requested range.")
            continue
            
        count = 0
        mult = MULTIPLIERS.get(symbol, 1)
        
        for index, row in df.iterrows():
            current_date = index.date()
            
            # Weekend/Holiday double-check (YF sometimes returns weekend data with last Friday's price)
            if not is_cme_trading_day(current_date):
                 continue

            price = float(row['Close'])
            if price <= 0:
                 print(f"    ⚠️ Skipping {current_date}: Price is 0")
                 continue
            
            # Use get_margin_for_date for correct historical margin value
            margin = get_margin_for_date(symbol, current_date, price)
            
            if margin <= 0:
                 print(f"    ⚠️ Skipping {current_date}: Margin resolved to 0")
                 continue
            
            # Timestamp (EOD UTC aligned)
            ts = f"{current_date} 16:00:00"
            
            # Margin Percent = (Margin / Notional) * 100
            notional = price * mult
            margin_pct = (margin / notional) * 100 if notional > 0 else 0
            
            if margin_pct <= 0:
                 continue

            cursor.execute("""
                INSERT OR REPLACE INTO margin_logs (timestamp, instrument_id, margin_percent, contract_price, open_interest)
                VALUES (?, ?, ?, ?, ?)
            """, (ts, instr_id, margin_pct, price, 0))
            count += 1
            
        print(f"  ✅ Inserted {count} records for {symbol}")
        conn.commit()
        
    conn.close()
    print("=== CME Resilient Backfill Complete ===")

if __name__ == "__main__":
    backfill_cme_historical()
