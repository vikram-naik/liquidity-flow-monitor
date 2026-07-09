import unittest
from unittest.mock import MagicMock, patch
import numpy as np

from src.trading.scanner import Scanner
from src.trading.signals.base import Trade

class TestScannerBarsHeld(unittest.TestCase):

    @patch("src.trading.scanner.run_engine")
    @patch("src.trading.scanner.TradingRepository")
    def test_bars_held_calculation(self, mock_repo_class, mock_run_engine):
        # 1. Setup mocked repository
        mock_repo = MagicMock()
        mock_repo_class.return_value = mock_repo

        # Define active open position in DB
        open_position = {
            "id": 42,
            "symbol": "M&M",
            "entry_date": "2026-05-26",
            "entry_price": 3000.0,
            "atr_at_entry": 60.0,
            "bars_held": 10,  # Old count in DB
            "delivery_bad_count": 0,
            "peak_close": 3100.0,
            "mfe_pct": 3.33,
            "mae_pct": 0.0,
            "psz_peak": 0.0,
            "entry_tag": "Universal-Cross"
        }
        mock_repo.get_open_positions.return_value = [open_position]
        mock_repo.get_config.return_value = {"watchlist": "NIFTY 50", "signal_strategy": "savgol_cts"}

        # Mock check_exit to return no exit reason
        mock_signal = MagicMock()
        mock_signal.check_exit.return_value = (None, 0)
        
        # 2. Test Case A: Entry date exists in records
        # 2026-05-26 is index 1, len(records) = 4, so bars_held = 4 - 1 - 1 = 2
        mock_records = [
            {"date": "2026-05-25 00:00:00", "close": 2980.0, "cwvap": 2990.0},
            {"date": "2026-05-26 00:00:00", "close": 3000.0, "cwvap": 2995.0},
            {"date": "2026-05-27 00:00:00", "close": 3050.0, "cwvap": 3000.0},
            {"date": "2026-05-28 00:00:00", "close": 3070.0, "cwvap": 3005.0},
        ]
        mock_run_engine.return_value = (MagicMock(), mock_records)

        with patch("src.trading.scanner.SignalFactory.get_signal", return_value=mock_signal):
            scanner = Scanner(dry_run=False)
            scanner._phase1_check_exits()

            # Verify repository update is called with bars_held = 2
            mock_repo.update_position.assert_called_with(
                42,
                peak_close=3100.0,
                psz_peak=0.0,
                delivery_bad_count=0,
                bars_held=2,
                current_pnl_pct=2.33,
                mfe_pct=3.33,
                mae_pct=0.0
            )

        # 3. Test Case B: Entry date is missing, but later date exists (Fallback)
        # Entry date = "2026-05-26". Records start at "2026-05-27".
        # 2026-05-27 is index 0 (first record >= 2026-05-26), len(records) = 2, so bars_held = 2 - 1 - 0 = 1
        mock_records_fallback = [
            {"date": "2026-05-27 00:00:00", "close": 3050.0, "cwvap": 3000.0},
            {"date": "2026-05-28 00:00:00", "close": 3070.0, "cwvap": 3005.0},
        ]
        mock_run_engine.return_value = (MagicMock(), mock_records_fallback)
        mock_repo.reset_mock()

        with patch("src.trading.scanner.SignalFactory.get_signal", return_value=mock_signal):
            scanner = Scanner(dry_run=False)
            scanner._phase1_check_exits()

            mock_repo.update_position.assert_called_with(
                42,
                peak_close=3100.0,
                psz_peak=0.0,
                delivery_bad_count=0,
                bars_held=1,
                current_pnl_pct=2.33,
                mfe_pct=3.33,
                mae_pct=0.0
            )

        # 4. Test Case C: Entry date completely missing (e.g. entry date in future relative to records)
        # Should fallback to previous bars_held + 1 = 10 + 1 = 11
        mock_records_future = [
            {"date": "2026-05-24 00:00:00", "close": 2950.0, "cwvap": 2990.0},
            {"date": "2026-05-25 00:00:00", "close": 2980.0, "cwvap": 2995.0},
        ]
        mock_run_engine.return_value = (MagicMock(), mock_records_future)
        mock_repo.reset_mock()

        with patch("src.trading.scanner.SignalFactory.get_signal", return_value=mock_signal):
            scanner = Scanner(dry_run=False)
            scanner._phase1_check_exits()

            mock_repo.update_position.assert_called_with(
                42,
                peak_close=3100.0,
                psz_peak=0.0,
                delivery_bad_count=0,
                bars_held=11,
                current_pnl_pct=-0.67,  # (2980/3000 - 1)*100
                mfe_pct=3.33,
                mae_pct=0.67  # -(-0.67) = 0.67
            )

    @patch("src.trading.scanner.run_engine")
    @patch("src.trading.scanner.TradingRepository")
    def test_exit_signal_triggers_proposed_exit(self, mock_repo_class, mock_run_engine):
        mock_repo = MagicMock()
        mock_repo_class.return_value = mock_repo

        open_position = {
            "id": 42,
            "symbol": "M&M",
            "entry_date": "2026-05-26",
            "entry_price": 3000.0,
            "atr_at_entry": 60.0,
            "bars_held": 10,
            "delivery_bad_count": 0,
            "peak_close": 3100.0,
            "mfe_pct": 3.33,
            "mae_pct": 0.0,
            "psz_peak": 0.0,
            "entry_tag": "Universal-Cross"
        }
        mock_repo.get_open_positions.return_value = [open_position]
        mock_repo.get_config.return_value = {"watchlist": "NIFTY 50", "signal_strategy": "savgol_cts"}

        # Mock check_exit to return a valid exit reason
        mock_signal = MagicMock()
        mock_signal.check_exit.return_value = ("CTS_CROSS", 0)

        mock_records = [
            {"date": "2026-05-26 00:00:00", "close": 3000.0, "cwvap": 2995.0},
            {"date": "2026-05-27 00:00:00", "close": 2900.0, "cwvap": 3000.0},
        ]
        mock_run_engine.return_value = (MagicMock(), mock_records)

        with patch("src.trading.scanner.SignalFactory.get_signal", return_value=mock_signal):
            scanner = Scanner(dry_run=False)
            scanner._phase1_check_exits()

            # Verify repository update is called with proposed_exit status, exit_signal_date and reason
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            mock_repo.update_position.assert_called_with(
                42,
                status="proposed_exit",
                exit_signal_date=today,
                exit_signal_reason="CTS_CROSS",
                peak_close=3100.0,
                delivery_bad_count=0,
                bars_held=1,
                current_pnl_pct=-3.33,  # (2900/3000 - 1)*100
                mfe_pct=3.33,
                mae_pct=3.33
            )

