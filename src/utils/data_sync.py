
"""
Data Sync Utilities
Tracks last download dates for incremental fetching
Base date: 1-Nov-2025
"""
import sqlite3
from datetime import date, datetime, timedelta

DB_PATH = "liquidity_monitor.db"
BASE_DATE = date(2025, 1, 1)

def get_last_download_date(exchange: str) -> date:
    """
    Get the last date data was downloaded for an exchange.
    Returns BASE_DATE if no data exists.
    
    Args:
        exchange: Exchange name (e.g., 'MCX', 'NSE', 'CME')
        
    Returns:
        Last download date or BASE_DATE if none found
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT MAX(DATE(l.timestamp)) 
        FROM margin_logs l
        JOIN instruments i ON l.instrument_id = i.id
        JOIN exchanges e ON i.exchange_id = e.id
        WHERE e.name = ?
    """, (exchange,))
    
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0]:
        return datetime.strptime(result[0], '%Y-%m-%d').date()
    
    return BASE_DATE

def get_last_instrument_date(symbol: str) -> date:
    """
    Get the last date data was downloaded for a specific instrument.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT MAX(DATE(l.timestamp)) 
        FROM margin_logs l
        JOIN instruments i ON l.instrument_id = i.id
        WHERE i.symbol = ?
    """, (symbol,))
    
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0]:
        return datetime.strptime(result[0], '%Y-%m-%d').date()
    
    return BASE_DATE

def get_last_yield_date() -> date:
    """
    Get the last date yield data was downloaded.
    Returns BASE_DATE if no data exists.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT MAX(DATE(timestamp)) FROM yield_logs")
    
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0]:
        return datetime.strptime(result[0], '%Y-%m-%d').date()
    
    return BASE_DATE

def get_dates_to_fetch(exchange: str, is_trading_day_fn) -> list:
    """
    Get list of dates that need to be fetched for an exchange.
    
    Args:
        exchange: Exchange name
        is_trading_day_fn: Function to check if date is a trading day
        
    Returns:
        List of dates to fetch (from last+1 to today)
    """
    last_date = get_last_download_date(exchange)
    today = date.today()
    
    dates_to_fetch = []
    current = last_date + timedelta(days=1)
    
    while current <= today:
        if is_trading_day_fn(current):
            dates_to_fetch.append(current)
        current += timedelta(days=1)
        
    return dates_to_fetch

def clear_exchange_data(exchange: str):
    """Clear all data for an exchange (for clean restart)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        DELETE FROM margin_logs 
        WHERE instrument_id IN (
            SELECT i.id FROM instruments i
            JOIN exchanges e ON i.exchange_id = e.id
            WHERE e.name = ?
        )
    """, (exchange,))
    
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    
    return deleted

if __name__ == "__main__":
    print(f"Base Date: {BASE_DATE}")
    print(f"Last MCX Date: {get_last_download_date('MCX')}")
    print(f"Last NSE Date: {get_last_download_date('NSE')}")
    print(f"Last CME Date: {get_last_download_date('CME')}")
    print(f"Last Yield Date: {get_last_yield_date()}")
