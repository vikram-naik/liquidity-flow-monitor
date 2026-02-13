
import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "liquidity_monitor.db")

def get_db_connection():
    return sqlite3.connect(DB_PATH)


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
    
    # Table: silver_intraday_log (for SilverBees Live Monitor)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS silver_intraday_log (
        timestamp DATETIME PRIMARY KEY,
        price_nse REAL,
        inav_nippon REAL,
        spot_usd REAL,
        usdinr REAL
    );
    """)

    # Table: gold_intraday_log (for GoldBees Live Monitor)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS gold_intraday_log (
        timestamp DATETIME PRIMARY KEY,
        price_nse REAL,
        inav_nippon REAL,
        spot_usd REAL,
        usdinr REAL
    );
    """)

    # --- NEW: NSE Delivery Monitor ---
    
    # Table: nse_delivery_log
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS nse_delivery_log (
        record_date DATE,
        symbol TEXT,
        price_close REAL,
        volume_total INTEGER,
        delivery_qty INTEGER,
        delivery_pct REAL,
        price_change_pct REAL,
        volume_change_pct REAL,
        delivery_change_pct REAL,
        PRIMARY KEY (record_date, symbol)
    );
    """)

    # Migration: Add price_change_pct if it doesn't exist
    try:
        cursor.execute("SELECT price_change_pct FROM nse_delivery_log LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating: Adding price_change_pct column to nse_delivery_log...")
        cursor.execute("ALTER TABLE nse_delivery_log ADD COLUMN price_change_pct REAL DEFAULT 0.0")

    # --- NEW: Treasury Monitor Tables ---
    
    # Table: treasury_auctions
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS treasury_auctions (
        record_date DATE,
        auction_date DATE,
        security_type TEXT,
        maturity TEXT,
        bid_to_cover REAL,
        tail_bps REAL,
        high_yield REAL,
        offering_amount REAL,
        total_accepted REAL,
        primary_dealer_accepted REAL,
        direct_bidder_accepted REAL,
        indirect_bidder_accepted REAL,
        soma_accepted REAL,
        soma_maturing REAL,
        noncomp_accepted REAL,
        is_new_issuance BOOLEAN,
        PRIMARY KEY (record_date, security_type, maturity)
    );
    """)
    
    # Migration: Add soma_maturing if it doesn't exist
    try:
        cursor.execute("SELECT soma_maturing FROM treasury_auctions LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating: Adding soma_maturing column to treasury_auctions...")
        cursor.execute("ALTER TABLE treasury_auctions ADD COLUMN soma_maturing REAL")

    # Table: treasury_liquidity
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS treasury_liquidity (
        record_date DATE PRIMARY KEY,
        tga_balance REAL,
        rrp_balance REAL,
        cds_spread REAL
    );
    """)

    # Table: treasury_debt_profile
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS treasury_debt_profile (
        record_date DATE PRIMARY KEY,
        maturing_1yr REAL,
        maturing_5yr REAL,
        total_debt REAL
    );
    """)

    # Table: treasury_buybacks
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS treasury_buybacks (
        record_date DATE,
        total_offered REAL,
        total_accepted REAL,
        security_type TEXT,
        maturity_bucket TEXT,
        PRIMARY KEY (record_date, security_type, maturity_bucket)
    );
    """)

    # Table: treasury_daily_debt_flows
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS treasury_daily_debt_flows (
        record_date DATE,
        security_type TEXT,
        transaction_type TEXT,
        amount_mil REAL,
        PRIMARY KEY (record_date, security_type, transaction_type)
    );
    """)

    # Table: treasury_maturity_schedule
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS treasury_maturity_schedule (
        record_date DATE,
        maturity_date DATE,
        security_class TEXT,
        amount_mil REAL,
        issue_date DATE,
        PRIMARY KEY (record_date, maturity_date, security_class)
    );
    """)

    # Table: treasury_avg_interest_rates
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS treasury_avg_interest_rates (
        record_date DATE,
        security_desc TEXT,
        avg_interest_rate_amt REAL,
        PRIMARY KEY (record_date, security_desc)
    );
    """)

    # Table: treasury_interest_delta
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS treasury_interest_delta (
        record_date DATE,
        security_class TEXT,
        historical_rate REAL,
        new_rate REAL,
        delta_bps REAL,
        PRIMARY KEY (record_date, security_class)
    );
    """)

    # Table: treasury_issuance_plan
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS treasury_issuance_plan (
        auction_date DATE,
        security_term TEXT,
        offering_amount REAL,
        is_new_issuance BOOLEAN,
        PRIMARY KEY (auction_date, security_term)
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
        (cme_id, 'CRUDE_OIL', 'Energy'),
        (cme_id, 'NATURAL_GAS', 'Energy'),
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
