import sqlite3
import unittest
from unittest.mock import patch

from src.trading.repository import TradingRepository

class NonClosingConnection:
    def __init__(self, conn):
        self._conn = conn
    def __getattr__(self, name):
        return getattr(self._conn, name)
    def close(self):
        pass  # No-op during tests to allow nested calls to share the connection

class TestRepositorySummary(unittest.TestCase):
    def setUp(self):
        # 1. Setup in-memory SQLite DB
        self.real_conn = sqlite3.connect(":memory:")
        self.real_conn.row_factory = sqlite3.Row
        self.conn = NonClosingConnection(self.real_conn)
        
        # 2. Patch get_db_connection to use our mock database
        self.patcher = patch("src.trading.repository.get_db_connection", return_value=self.conn)
        self.patcher.start()
        
        # 3. Create necessary tables
        cursor = self.real_conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS trading_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            entry_date TEXT,
            entry_price REAL,
            atr_at_entry REAL,
            quantity INTEGER,
            regime_at_entry TEXT,
            psz_at_entry REAL,
            signal_strategy TEXT,
            entry_tag TEXT,
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
            capital_deployed REAL,
            sizing_method TEXT,
            kelly_f REAL,
            entry_charges REAL DEFAULT 0,
            exit_charges REAL DEFAULT 0,
            total_charges REAL DEFAULT 0,
            net_pnl_pct REAL,
            net_pnl_abs REAL,
            broker_order_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS trading_config (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS trading_capital_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            amount REAL NOT NULL,
            balance_after REAL NOT NULL,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        self.real_conn.commit()

    def tearDown(self):
        self.patcher.stop()
        self.real_conn.close()

    def test_get_summary_fixed_calculations(self):
        cursor = self.real_conn.cursor()
        
        # Set total capital config to 1,000,000
        cursor.execute("INSERT INTO trading_config (key, value) VALUES ('capital', '1000000')")
        
        # Populate closed trades
        # Trade 1: deployed = 10,000, final_pnl_pct = 50.0 (gross +50%), net_pnl_abs = 4,500, total_charges = 500
        cursor.execute("""
        INSERT INTO trading_positions (symbol, mode, status, signal_date, entry_date, entry_price, quantity, capital_deployed, final_pnl_pct, net_pnl_abs, total_charges)
        VALUES ('AAPL', 'paper', 'closed', '2026-05-01', '2026-05-02', 100.0, 100, 10000.0, 50.0, 4500.0, 500.0)
        """)
        # Trade 2: deployed = 100,000, final_pnl_pct = -40.0 (gross -40%), net_pnl_abs = -41,000, total_charges = 1000
        cursor.execute("""
        INSERT INTO trading_positions (symbol, mode, status, signal_date, entry_date, entry_price, quantity, capital_deployed, final_pnl_pct, net_pnl_abs, total_charges)
        VALUES ('TSLA', 'paper', 'closed', '2026-05-10', '2026-05-11', 200.0, 500, 100000.0, -40.0, -41000.0, 1000.0)
        """)
        
        # Populate open positions
        # Open 1: deployed = 200,000, current_pnl_pct = -5.0 (-10,000 gross)
        cursor.execute("""
        INSERT INTO trading_positions (symbol, mode, status, signal_date, entry_date, entry_price, quantity, capital_deployed, current_pnl_pct)
        VALUES ('MSFT', 'paper', 'open', '2026-06-01', '2026-06-02', 400.0, 500, 200000.0, -5.0)
        """)
        # Open 2: deployed = 10,000, current_pnl_pct = 10.0 (+1,000 gross)
        cursor.execute("""
        INSERT INTO trading_positions (symbol, mode, status, signal_date, entry_date, entry_price, quantity, capital_deployed, current_pnl_pct)
        VALUES ('NFLX', 'paper', 'open', '2026-06-05', '2026-06-06', 500.0, 20, 10000.0, 10.0)
        """)
        
        self.real_conn.commit()
        
        # Mock datetime.now to return a fixed date 2026-06-15
        from datetime import datetime as real_datetime
        class MockDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return real_datetime(2026, 6, 15, 8, 0, 0)
        
        repo = TradingRepository()
        with patch("src.trading.repository.datetime", MockDatetime):
            summary = repo.get_summary()
        
        # Verify correctness
        # Total Capital = 1,000,000
        # Closed: AAPL (gross = +5,000, net = +4,500), TSLA (gross = -40,000, net = -41,000)
        # Total Net P&L Abs = 4,500 - 41,000 = -36,500
        # Total Net P&L % = -36,500 / 1,000,000 * 100 = -3.65%
        # Total Gross P&L Abs = (4,500 + 500) + (-41,000 + 1,000) = 5,000 - 40,000 = -35,000
        # Total Gross P&L % = -35,000 / 1,000,000 * 100 = -3.50%
        # Total Charges = 500 + 1000 = 1500
        #
        # Unrealized Open: MSFT (deployed 200k, P&L -5% = -10,000), NFLX (deployed 10k, P&L +10% = +1,000)
        # Unrealized P&L Abs = -10,000 + 1,000 = -9,000
        # Unrealized P&L % = -9,000 / 1,000,000 * 100 = -0.90%
        #
        # CAGR calculation:
        # start_date = 2026-05-02 (AAPL entry_date)
        # end_date = 2026-06-15
        # days = 44 days
        # cagr = ((1000000 - 36500) / 1000000) ** (365.25 / 44) - 1.0 = -26.79%
        
        self.assertEqual(summary["total_closed"], 2)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["win_rate"], 50.0)
        self.assertEqual(summary["total_charges"], 1500.0)
        
        self.assertEqual(summary["total_net_pnl_abs"], -36500.0)
        self.assertEqual(summary["total_net_pnl_pct"], -3.65)
        
        self.assertEqual(summary["total_gross_pnl_abs"], -35000.0)
        self.assertEqual(summary["total_gross_pnl_pct"], -3.50)
        
        self.assertEqual(summary["unrealized_pnl_abs"], -9000.0)
        self.assertEqual(summary["unrealized_pnl_pct"], -0.90)
        
        self.assertEqual(summary["cagr_pct"], -26.56)
