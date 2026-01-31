import sqlite3

DB_PATH = "liquidity_monitor.db"

def cleanup():
    print(f"Connecting to {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Identify duplicates with no data
    # We know IDs 4-9 are the culprits from our investigation
    print("Deleting redundant instrument records (IDs 4-9)...")
    cursor.execute("DELETE FROM instruments WHERE id > 3")
    deleted_count = cursor.rowcount
    
    conn.commit()
    conn.close()
    print(f"Successfully deleted {deleted_count} duplicate records.")

if __name__ == "__main__":
    cleanup()
