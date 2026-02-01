
import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "liquidity_monitor.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Enable Foreign Keys support in SQLite
    cursor.execute("PRAGMA foreign_keys = ON;")

    # Table: exchanges
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS exchanges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        country TEXT
    );
    """)

    # Table: instruments
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS instruments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exchange_id INTEGER REFERENCES exchanges(id),
        symbol TEXT,
        asset_class TEXT,
        UNIQUE(exchange_id, symbol)
    );
    """)

    # Table: margin_logs
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS margin_logs (
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        instrument_id INTEGER REFERENCES instruments(id),
        margin_percent REAL,
        contract_price REAL,
        open_interest INTEGER,
        PRIMARY KEY (timestamp, instrument_id)
    );
    """)

    # Table: yield_logs
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS yield_logs (
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        currency TEXT,
        tenor TEXT,
        rate REAL,
        PRIMARY KEY (timestamp, currency, tenor)
    );
    """)
    
    # Table: trading_holidays
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trading_holidays (
        exchange_id INTEGER REFERENCES exchanges(id),
        holiday_date DATE,
        holiday_name TEXT,
        is_partial BOOLEAN DEFAULT 0,
        notes TEXT,
        PRIMARY KEY (exchange_id, holiday_date)
    );
    """)
    
    # Pre-populate exchanges and instruments
    print("Seeding/Updating essential data...")
    exchanges = [
        ('CME', 'USA')
    ]
    cursor.executemany("INSERT OR IGNORE INTO exchanges (name, country) VALUES (?, ?)", exchanges)
    
    # Helper to get exchange ID
    def get_exch_id(name):
        cursor.execute("SELECT id FROM exchanges WHERE name = ?", (name,))
        res = cursor.fetchone()
        return res[0] if res else None

    cme_id = get_exch_id('CME')

    instruments = [
        (cme_id, 'SILVER', 'Precious'),
        (cme_id, 'GOLD', 'Precious'),
        (cme_id, 'COPPER', 'Industrial'),
        (cme_id, 'ES', 'Equity Index'),
        (cme_id, 'NQ', 'Equity Index')
    ]
    cursor.executemany("INSERT OR IGNORE INTO instruments (exchange_id, symbol, asset_class) VALUES (?, ?, ?)", instruments)

    # Seed Holidays (CME / US Federal)
    print("Seeding holidays...")
    cme_hols = [
        ('2025-01-01', 'New Year Day', 0), ('2025-01-20', 'MLK Day', 0), ('2025-02-17', 'Presidents Day', 0),
        ('2025-04-18', 'Good Friday', 0), ('2025-05-26', 'Memorial Day', 0), ('2025-06-19', 'Juneteenth', 0),
        ('2025-07-04', 'Independence Day', 0), ('2025-09-01', 'Labor Day', 0), ('2025-11-27', 'Thanksgiving', 0),
        ('2025-12-25', 'Christmas', 0), ('2026-01-01', 'New Year Day', 0), ('2026-01-19', 'MLK Day', 0),
        ('2026-02-16', 'Presidents Day', 0), ('2026-04-03', 'Good Friday', 0), ('2026-05-25', 'Memorial Day', 0),
        ('2026-06-19', 'Juneteenth', 0), ('2026-07-03', 'Independence Day (Observed)', 0), ('2026-09-07', 'Labor Day', 0),
        ('2026-11-26', 'Thanksgiving', 0), ('2026-12-25', 'Christmas', 0)
    ]

    hol_data = []
    for d, n, p in cme_hols: hol_data.append((cme_id, d, n, p))

    cursor.executemany("""
        INSERT OR REPLACE INTO trading_holidays (exchange_id, holiday_date, holiday_name, is_partial)
        VALUES (?, ?, ?, ?)
    """, hol_data)

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")

if __name__ == "__main__":
    init_db()
