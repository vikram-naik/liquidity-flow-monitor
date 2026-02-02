
import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "liquidity_monitor.db")

def fix_asset_classes():
    print(f"Applying asset_class fixes to {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    updates = [
        ('Precious', 'SILVER'),
        ('Precious', 'GOLD'),
        ('Industrial', 'COPPER'),
        ('Energy', 'CRUDE_OIL'),
        ('Equity Index', 'ES'),
        ('Equity Index', 'NQ')
    ]
    
    for asset_class, symbol in updates:
        cursor.execute("UPDATE instruments SET asset_class = ? WHERE symbol = ?", (asset_class, symbol))
        if cursor.rowcount > 0:
            print(f"  ✓ Updated {symbol} -> {asset_class}")
        else:
            print(f"  ! Symbol {symbol} not found in database")
            
    conn.commit()
    conn.close()
    print("Fixes complete.")

if __name__ == "__main__":
    fix_asset_classes()
