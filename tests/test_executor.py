import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime

from src.trading.executor import OrderExecutor

class TestOrderExecutorLag(unittest.TestCase):

    @patch("src.trading.executor.TradingRepository")
    @patch("src.trading.executor.run_engine")
    def test_executor_filters_today_signals(self, mock_run_engine, mock_repo_class):
        # Setup mock repository
        mock_repo = MagicMock()
        mock_repo_class.return_value = mock_repo

        # Setup config mock
        mock_repo.get_config.return_value = {
            "capital": "1000000",
            "max_concurrent_positions": "8",
            "execution_mode": "paper",
            "price_resolver": "historical",
            "sizing_strategy": "equal_weight",
            "brokerage_model": "zerodha",
        }
        mock_repo.get_funds.return_value = {
            "net_worth": 1000000.0,
            "available_capital": 1000000.0,
        }

        # Setup today's date
        today = datetime.now().strftime("%Y-%m-%d")

        # Mock entries: one from yesterday, one from today
        pending_entries = [
            {"id": 1, "symbol": "RELIANCE", "status": "pending_entry", "signal_date": "2026-07-08"},
            {"id": 2, "symbol": "TCS", "status": "pending_entry", "signal_date": today},
        ]
        mock_repo.get_pending_entries.return_value = pending_entries

        # Mock exits: one from yesterday, one from today
        pending_exits = [
            {"id": 3, "symbol": "INFY", "status": "pending_exit", "exit_signal_date": "2026-07-08", "quantity": 10, "entry_price": 1500.0},
            {"id": 4, "symbol": "HDFCBANK", "status": "pending_exit", "exit_signal_date": today, "quantity": 20, "entry_price": 1600.0},
        ]
        mock_repo.get_pending_exits.return_value = pending_exits

        # Mock engine runs
        mock_records = [
            {"date": today, "open": 2000.0, "close": 2010.0, "atr_20": 40.0, "high": 2020.0, "low": 1990.0, "cwvap": 2005.0}
        ]
        mock_run_engine.return_value = (MagicMock(), mock_records)

        # Mock place_order to return dummy broker ID
        executor = OrderExecutor(dry_run=False)
        executor.broker = MagicMock()
        executor.broker.place_order.return_value = {"order_id": "broker-123"}

        # Run execute_entries and execute_exits
        entry_results = executor.execute_entries()
        exit_results = executor.execute_exits()

        # Assertions:
        # RELIANCE (yesterday's entry) should be executed
        # TCS (today's entry) should be skipped
        self.assertEqual(len(entry_results), 1)
        self.assertEqual(entry_results[0]["symbol"], "RELIANCE")

        # INFY (yesterday's exit) should be executed
        # HDFCBANK (today's exit) should be skipped
        self.assertEqual(len(exit_results), 1)
        self.assertEqual(exit_results[0]["symbol"], "INFY")

if __name__ == "__main__":
    unittest.main()
