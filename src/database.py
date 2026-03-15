
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

    # Table: nse_trading_holidays
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS nse_trading_holidays (
        holiday_date DATE PRIMARY KEY,
        description TEXT,
        segment TEXT
    );
    """)

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

    # Table: user_settings (key-value store for configurable parameters)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Seed divergence engine defaults (INSERT OR IGNORE = only on first run)
    cursor.executemany(
        "INSERT OR IGNORE INTO user_settings (key, value) VALUES (?, ?)",
        [
            ('div_swing_n',      '5'),
            ('div_min_spacing',  '10'),
            ('div_min_dvl_pct',  '0.08'),
        ]
    )

    # Migration: Add instrument_type column if it doesn't exist
    try:
        cursor.execute("SELECT instrument_type FROM nse_delivery_log LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating: Adding instrument_type column to nse_delivery_log...")
        cursor.execute("ALTER TABLE nse_delivery_log ADD COLUMN instrument_type TEXT DEFAULT 'STOCK'")

    # --- Secondary Indices for Performance ---
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_nse_instrument_type ON nse_delivery_log (instrument_type);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_nse_symbol_date ON nse_delivery_log (symbol, record_date);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlists_created ON watchlists (created_at DESC);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_items_order ON watchlist_items (watchlist_id, display_order);")

    # --- Trading Module Tables ---

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trading_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        signal_date TEXT NOT NULL,
        entry_date TEXT,
        signal_type TEXT NOT NULL,
        psz_at_signal REAL,
        prev_psz REAL,
        pdd_120 REAL,
        rsz_delta REAL,
        regime TEXT,
        soft_filters_passed INTEGER,
        rdv_pass INTEGER DEFAULT 0,
        mcs_pass INTEGER DEFAULT 0,
        cwc_pass INTEGER DEFAULT 0,
        grad_pass INTEGER DEFAULT 0,
        acted_upon INTEGER DEFAULT 0,
        skip_reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(symbol, signal_date, signal_type)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trading_positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'paper',
        status TEXT NOT NULL DEFAULT 'pending_entry',
        signal_date TEXT,
        entry_date TEXT,
        entry_price REAL,
        atr_at_entry REAL,
        quantity INTEGER,
        soft_filters_passed INTEGER,
        rdv_pass INTEGER DEFAULT 0,
        mcs_pass INTEGER DEFAULT 0,
        cwc_pass INTEGER DEFAULT 0,
        grad_pass INTEGER DEFAULT 0,
        regime_at_entry TEXT,
        psz_at_entry REAL,
        peak_close REAL,
        psz_peak REAL,
        delivery_bad_count INTEGER DEFAULT 0,
        bars_held INTEGER DEFAULT 0,
        current_pnl_pct REAL,
        mfe_pct REAL DEFAULT 0,
        mae_pct REAL DEFAULT 0,
        exit_date TEXT,
        exit_price REAL,
        exit_reason TEXT,
        final_pnl_pct REAL,
        broker_order_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trading_daily_pnl (
        date TEXT PRIMARY KEY,
        open_positions INTEGER,
        total_invested REAL,
        unrealized_pnl_pct REAL,
        realized_pnl_today REAL,
        cumulative_realized_pnl REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trading_config (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Seed trading config defaults
    cursor.executemany(
        "INSERT OR IGNORE INTO trading_config (key, value) VALUES (?, ?)",
        [
            ('max_concurrent_positions', '8'),
            ('watchlist', 'NIFTY 50'),
            ('execution_mode', 'paper'),
            ('position_size_pct', '12.5'),
            ('capital', '1000000'),
        ]
    )

    # Trading indices
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trading_positions_status ON trading_positions (status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trading_positions_symbol_status ON trading_positions (symbol, status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trading_signals_date ON trading_signals (signal_date);")


    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")

if __name__ == "__main__":
    init_db()
