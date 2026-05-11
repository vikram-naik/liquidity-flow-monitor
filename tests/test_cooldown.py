import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from src.trading.signals.savgol_cts.signal import SavgolCTSSignal
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.enums import ExitReason

def test_cooldown_disabled_global_config():
    """Verify that cooldown does not block entry when disabled in global config."""
    signal = SavgolCTSSignal()
    
    # Manually set the last exit state to something that would trigger cooldown
    signal._last_exit_idx = 10
    signal._last_exit_reason = ExitReason.SUPPRESSED_EXIT
    
    cfg = SavgolCTSEntryConfig()
    # The flag should be False because we just modified config.py
    assert cfg.cooldown_enabled is False
    
    # Mock data for check_entry
    # idx=11 is only 1 bar since exit (within cooldown_bars=10)
    row = {"cts": -0.95}
    prev_row = {"cts": -1.0}
    
    # We mock entry_universal_cross to return True so we can see if it's reached
    with patch("src.trading.signals.savgol_cts.signal.entry_universal_cross") as mock_entry:
        mock_entry.return_value = (True, 90, {"entry_tag": "Universal-Cross"})
        
        passed, intensity, meta = signal.check_entry(row, prev_row, cfg, idx=11)
        
        assert passed is True
        assert meta.get("cooldown") is not True
        assert meta.get("reason") != "Cooldown active"
        mock_entry.assert_called_once()

def test_cooldown_behavior_when_enabled_manually():
    """Verify that cooldown blocks entry when enabled manually (sanity check of the logic)."""
    signal = SavgolCTSSignal()
    signal._last_exit_idx = 10
    signal._last_exit_reason = ExitReason.SUPPRESSED_EXIT
    
    cfg = SavgolCTSEntryConfig()
    cfg.cooldown_enabled = True # Force enable for this test
    
    row = {"cts": -0.95}
    prev_row = {"cts": -1.0}
    
    passed, intensity, meta = signal.check_entry(row, prev_row, cfg, idx=11)
    
    assert passed is False
    assert meta.get("cooldown") is True
    assert meta.get("reason") == "Cooldown active"
