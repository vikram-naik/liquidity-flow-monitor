import pytest
import numpy as np
from src.trading.signals.base import Trade
from src.trading.signals.enums import EntryTag, ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.savgol_cts.entries.universal_cross import entry_universal_cross
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross
from src.trading.signals.savgol_cts.state import SavgolCTSExitState
from src.trading.signals.savgol_cts.ml_guard import MLGuard
from unittest.mock import MagicMock, patch

@pytest.fixture
def mock_ml_guard():
    with patch("src.trading.signals.savgol_cts.ml_guard.MLGuard.get_instance") as mock_get:
        instance = MagicMock()
        mock_get.return_value = instance
        yield instance

def test_universal_cross_entry_prt_inflection(mock_ml_guard):
    """Verify entry triggers on PRT slope inflection."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True
    cfg.universal_cross.min_ml_score = 80.0
    
    # Mock ML Guard to return a passing score (0.85 -> 85%)
    mock_ml_guard.score_setup.return_value = 0.85
    
    # Setup inflection: prev_prt <= 0, prt > 0
    row = {"prt_slope": 0.01, "fas": -1.0, "cts": -1.0, "cts_accel": -0.1}
    prev_row = {"prt_slope": -0.01, "fas": -1.0, "cts": -1.0, "cts_accel": -0.1}
    
    passed, score, meta = entry_universal_cross(row, prev_row, cfg, [prev_row, row], 1)
    
    assert passed is True
    assert score == 85
    assert meta["entry_tag"] == EntryTag.UNIVERSAL_CROSS.value
    assert meta["trigger_prt"] is True

def test_universal_cross_entry_rejection_low_score(mock_ml_guard):
    """Verify rejection when ML score is below threshold."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.min_ml_score = 90.0
    
    # Mock ML Guard to return 0.85 (85%)
    mock_ml_guard.score_setup.return_value = 0.85
    
    row = {"prt_slope": 0.01, "fas": -1.0, "cts": -1.0, "cts_accel": -0.1}
    prev_row = {"prt_slope": -0.01, "fas": -1.0, "cts": -1.0, "cts_accel": -0.1}
    
    passed, score, meta = entry_universal_cross(row, prev_row, cfg, [prev_row, row], 1)
    
    assert passed is False
    assert "rejected" in meta["reason"]

def test_universal_cross_exit_cts_trail():
    """Verify exit on CTS crossing down through ST threshold."""
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True
    
    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0, soft_filters_passed=0,
        entry_tag=EntryTag.UNIVERSAL_CROSS.value
    )
    
    # Crossover: prev_cts >= prev_st, cts < st
    row = {"close": 105.0, "cts": 0.49, "cts_sell_threshold": 0.50}
    prev_row = {"close": 104.0, "cts": 0.51, "cts_sell_threshold": 0.50}
    
    reason, state_val = exit_universal_cross(row, prev_row, trade, 105.0, 5, 0, cfg)
    
    assert reason == ExitReason.ST_CROSS

def test_universal_cross_exit_hard_stop():
    """Verify 8% hard stop exit."""
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True
    cfg.hard_stop_enabled = True
    cfg.hard_stop_pct = 8.0
    
    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0, soft_filters_passed=0,
        entry_tag=EntryTag.UNIVERSAL_CROSS.value
    )
    
    # Price dropped to 91 (-9%)
    row = {"close": 91.0, "cts": 0.6, "cts_sell_threshold": 0.5}
    prev_row = {"close": 95.0, "cts": 0.6, "cts_sell_threshold": 0.5}
    
    reason, state_val = exit_universal_cross(row, prev_row, trade, 100.0, 1, 0, cfg)
    
    assert reason == ExitReason.HARD_STOP
