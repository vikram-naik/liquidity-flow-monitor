import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
from scripts.validate_data_integrity import reconcile_with_internet

@patch("yfinance.Ticker")
@patch("scripts.validate_data_integrity.DivergenceEngine")
def test_reconcile_with_internet_match_with_alignment(mock_engine_cls, mock_ticker_cls):
    """
    Test that reconcile_with_internet aligns dates when local database has a special session
    date (e.g., Saturday session mapped to Monday 2024-05-20) that is missing in yfinance.
    """
    # 1. Mock yfinance data (Missing 2024-05-20)
    mock_ticker = MagicMock()
    mock_ticker_cls.return_value = mock_ticker
    
    # yfinance history around 2024-05-21
    # Note: 2024-05-20 is omitted in external source (holiday)
    dates = pd.to_datetime(["2024-05-15", "2024-05-16", "2024-05-17", "2024-05-21", "2024-05-22"])
    yf_df = pd.DataFrame(
        {
            "Open": [3218.78, 3433.69, 3628.98, 4200.11, 4641.71],
            "High": [3414.95, 3619.16, 3724.16, 4574.79, 4759.47],
            "Low": [3218.78, 3433.69, 3628.98, 4200.11, 4641.71],
            "Close": [3393.06, 3590.70, 3630.84, 4574.79, 4368.21],
        },
        index=dates
    )
    mock_ticker.history.return_value = yf_df
    mock_ticker.dividends = pd.Series(dtype=float)
    
    # 2. Mock DivergenceEngine local ledger (Includes 2024-05-20)
    mock_engine = MagicMock()
    mock_engine_cls.return_value = mock_engine
    
    ledger_dates = pd.to_datetime(["2024-05-15", "2024-05-16", "2024-05-17", "2024-05-20", "2024-05-21", "2024-05-22"])
    ledger_df = pd.DataFrame(
        {
            "date": ledger_dates,
            "close": [3457.6, 3659.0, 3699.9, 3884.85, 4661.8, 4451.3],
        }
    )
    mock_engine.run.return_value.ledger = ledger_df
    
    # Call reconcile_with_internet
    # Local return on 2024-05-21 was 20.00% (computed relative to 2024-05-20)
    # yfinance daily return on 2024-05-21 was 26.00% (computed relative to 2024-05-17)
    status, ext_ohlc, ext_ret, div_impact = reconcile_with_internet(
        symbol="BBL",
        date="2024-05-21",
        local_close=4661.8,
        local_ret=20.00
    )
    
    # The return difference should align, resulting in a verified Match!
    assert status.startswith("Match")
    assert ext_ohlc["c"] == 4574.79
    # The external return computed relative to 2024-05-17 is 25.998% (~26.0%)
    assert abs(ext_ret - 26.0) < 0.1
    # Check that yfinance was queried
    mock_ticker.history.assert_called_once()

@patch("yfinance.Ticker")
@patch("scripts.validate_data_integrity.DivergenceEngine")
def test_reconcile_with_internet_standard_match(mock_engine_cls, mock_ticker_cls):
    """
    Test standard reconciliation where there are no date discrepancies.
    """
    mock_ticker = MagicMock()
    mock_ticker_cls.return_value = mock_ticker
    
    dates = pd.to_datetime(["2024-05-15", "2024-05-16", "2024-05-17"])
    yf_df = pd.DataFrame(
        {
            "Open": [3218.78, 3433.69, 3628.98],
            "High": [3414.95, 3619.16, 3724.16],
            "Low": [3218.78, 3433.69, 3628.98],
            "Close": [3393.06, 3590.70, 3630.84],
        },
        index=dates
    )
    mock_ticker.history.return_value = yf_df
    mock_ticker.dividends = pd.Series(dtype=float)
    
    mock_engine = MagicMock()
    mock_engine_cls.return_value = mock_engine
    ledger_df = pd.DataFrame(
        {
            "date": dates,
            "close": [3393.06, 3590.70, 3630.84],
        }
    )
    mock_engine.run.return_value.ledger = ledger_df
    
    # 2024-05-17 return in yfinance is 1.117% relative to 2024-05-16
    status, ext_ohlc, ext_ret, div_impact = reconcile_with_internet(
        symbol="BBL",
        date="2024-05-17",
        local_close=3630.84,
        local_ret=1.117
    )
    
    assert status.startswith("Match")
    assert ext_ohlc["c"] == 3630.84
