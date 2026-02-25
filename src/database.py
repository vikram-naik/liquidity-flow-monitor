
import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "liquidity_monitor.db")

def get_db_connection():
    # Add timeout to handle concurrent writes better (default is 5s)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    # Use WAL mode for better concurrency (multiple readers, one writer)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Enable Foreign Keys and WAL mode
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute("PRAGMA journal_mode = WAL;")


    


    # --- NEW: NSE Delivery Monitor ---
    
    # Table: nse_delivery_log
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS nse_delivery_log (
        record_date DATE,
        symbol TEXT,
        price_close REAL,
        price_open REAL,
        price_high REAL,
        price_low REAL,
        volume_total INTEGER,
        delivery_qty INTEGER,
        delivery_pct REAL,
        price_change_pct REAL,
        volume_change_pct REAL,
        delivery_change_pct REAL,
        pe_ratio REAL,
        pb_ratio REAL,
        dividend_yield REAL,
        turnover_crs REAL,
        PRIMARY KEY (record_date, symbol)
    );
    """)

    # Migration: Add price_change_pct if it doesn't exist
    try:
        cursor.execute("SELECT price_change_pct FROM nse_delivery_log LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating: Adding price_change_pct column to nse_delivery_log...")
        cursor.execute("ALTER TABLE nse_delivery_log ADD COLUMN price_change_pct REAL DEFAULT 0.0")

    # Migration: Add OHLC columns if they don't exist
    try:
        cursor.execute("SELECT price_open FROM nse_delivery_log LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating: Adding OHLC columns to nse_delivery_log...")
        cursor.execute("ALTER TABLE nse_delivery_log ADD COLUMN price_open REAL")
        cursor.execute("ALTER TABLE nse_delivery_log ADD COLUMN price_high REAL")
        cursor.execute("ALTER TABLE nse_delivery_log ADD COLUMN price_low REAL")

    # Migration: Add index functional columns if they don't exist
    try:
        cursor.execute("SELECT pe_ratio FROM nse_delivery_log LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating: Adding fundamental columns to nse_delivery_log...")
        cursor.execute("ALTER TABLE nse_delivery_log ADD COLUMN pe_ratio REAL")
        cursor.execute("ALTER TABLE nse_delivery_log ADD COLUMN pb_ratio REAL")
        cursor.execute("ALTER TABLE nse_delivery_log ADD COLUMN dividend_yield REAL")
        cursor.execute("ALTER TABLE nse_delivery_log ADD COLUMN turnover_crs REAL")

    
    # Table: watchlists
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS watchlists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Table: watchlist_items
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS watchlist_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        watchlist_id INTEGER REFERENCES watchlists(id) ON DELETE CASCADE,
        symbol TEXT,
        display_order INTEGER,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(watchlist_id, symbol)
    );
    """)

    # Table: symbol_anchors (Persistence for EOD AVWAP)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS symbol_anchors (
        symbol TEXT PRIMARY KEY,
        anchor_date DATE,
        anchor_type TEXT,
        meta TEXT,
        confirmed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Table: corporate_actions
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS corporate_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        ex_date DATE,
        ca_type TEXT,
        ratio_factor REAL,
        notes TEXT,
        UNIQUE(symbol, ex_date, ca_type)
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ca_symbol ON corporate_actions(symbol);")

    # --- Secondary Indices for Performance ---
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_nse_symbol_date ON nse_delivery_log (symbol, record_date);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlists_created ON watchlists (created_at DESC);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_items_order ON watchlist_items (watchlist_id, display_order);")




    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")

if __name__ == "__main__":
    init_db()
