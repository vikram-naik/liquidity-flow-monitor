import sqlite3
from datetime import datetime

# Path to DB for holiday checks
DB_PATH = "liquidity_monitor.db"

def is_cme_trading_day(target_date):
    """
    Check if a date is a trading day for CME.
    Rules: 
    1. Not a Saturday or Sunday.
    2. Not a US Federal Holiday (from DB).
    """
    # Weekends
    if target_date.weekday() >= 5:
        return False
        
    date_str = target_date.strftime("%Y-%m-%d")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if it's in the holidays table for CME (Exch ID 1 usually, but let's be safe)
        query = """
            SELECT h.id FROM trading_holidays h
            JOIN exchanges e ON h.exchange_id = e.id
            WHERE e.name = 'CME' AND h.holiday_date = ? AND h.is_partial = 0
        """
        cursor.execute(query, (date_str,))
        res = cursor.fetchone()
        conn.close()
        
        if res:
            return False # It's a full holiday
            
        return True
    except:
        # Fallback if DB not ready
        return target_date.weekday() < 5

def is_nse_trading_day(target_date):
    """Legacy wrapper for NSE (always False now since we removed NSE)"""
    return False

def is_mcx_trading_day(target_date):
    """Legacy wrapper for MCX (always False now)"""
    return False
