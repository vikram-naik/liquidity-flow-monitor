
import sqlite3
import random
from datetime import datetime, timedelta

DB_PATH = "liquidity_monitor.db"

def seed_history():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Shift existing logs to "Now" if they are old (for demo purpose)
    # Actually, we just insert T-24h records based on current records.
    
    # Get current records
    query = "SELECT instrument_id, margin_percent, contract_price FROM margin_logs"
    cursor.execute(query)
    current_logs = cursor.fetchall()
    
    if not current_logs:
        print("No logs to seed from.")
        return

    print(f"Seeding history based on {len(current_logs)} current logs...")
    
    # Timestamp for yesterday
    yesterday = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    
    for row in current_logs:
        inst_id, margin, price = row
        
        # Create a "previous" state
        # Simulate a hike: Previous was LOWER (+Hike now)
        # Prev Margin = Margin * 0.9 (So now is +11%)
        prev_margin = margin * 0.9 
        
        # Simulate Price Drop (Shakeout)
        # Prev Price = Price * 1.05 (So now is -5%)
        # If price was 0 (missing), keep 0
        if price == 0:
            # Mock price for calculation (Silver ~30, Gold ~2600, Copper ~4, Crude ~70)
            # Need to know asset.
            # Lazy approach: Seed a Random Price for NOW and THEN
            # But converting existing 0 to valuable is tricky in this loop.
            # We insert T-1 row. 
            # We assume the Analytics script will use what's there.
            # To test the Alert, we need > 5% hike. 
            prev_margin = margin * 0.90 # 10% hike today
            prev_price = 100.0 # Dummy
        else:
             prev_price = price * 1.05
             
        # Insert
        cursor.execute("""
            INSERT INTO margin_logs (timestamp, instrument_id, margin_percent, contract_price, open_interest)
            VALUES (?, ?, ?, ?, ?)
        """, (yesterday, inst_id, prev_margin, prev_price, 0))
        
    # Also seed Yields
    cursor.execute("SELECT currency, tenor, rate FROM yield_logs")
    yields = cursor.fetchall()
    for row in yields:
        curr, tenor, rate = row
        # Seed Rising Yields: Yesterday was Lower
        prev_rate = rate * 0.98
        cursor.execute("""
            INSERT INTO yield_logs (timestamp, currency, tenor, rate)
            VALUES (?, ?, ?, ?)
        """, (yesterday, curr, tenor, prev_rate))
        
    conn.commit()
    conn.close()
    print("Seeded historical data (T-24h).")

if __name__ == "__main__":
    seed_history()
