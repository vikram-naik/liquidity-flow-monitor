import pytest
import numpy as np
from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState
from src.trading.signals.savgol_cts.exits.cwvap_guard import apply_cwvap_guard

def test_cwvap_guard_hybrid_exit_enabled_triggers_on_negative_momentum():
    """Verify hybrid active exit triggers when price is below CWVAP, PNL < 0, and momentum fades."""
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.hybrid_exit_enabled = True
    
    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0
    )
    
    st = SavgolCTSExitState()
    
    # Below CWVAP (close=95, cwvap=100)
    # PNL is negative (close=95 < entry=100)
    # Momentum is fading (fas=-0.1, psz_v=-0.1)
    row = {
        "close": 95.0,
        "cwvap": 100.0,
        "fas": -0.1,
        "psz_v": -0.1
    }
    
    reason, next_state = apply_cwvap_guard(row, trade, None, st.to_int(), cfg, [], 1)
    
    assert reason == ExitReason.CWVAP_EXHAUSTION

def test_cwvap_guard_hybrid_exit_enabled_ignores_positive_pnl():
    """Verify hybrid active exit ignores winning trades even if below CWVAP."""
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.hybrid_exit_enabled = True
    
    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=90.0,
        entry_idx=0, atr_at_entry=2.0
    )
    
    st = SavgolCTSExitState()
    
    # Below CWVAP (close=95, cwvap=100)
    # PNL is POSITIVE (close=95 > entry=90)
    # Momentum is fading (fas=-0.1, psz_v=-0.1)
    row = {
        "close": 95.0,
        "cwvap": 100.0,
        "fas": -0.1,
        "psz_v": -0.1
    }
    
    reason, next_state = apply_cwvap_guard(row, trade, None, st.to_int(), cfg, [], 1)
    
    # Should not initiate exit
    assert reason is None

def test_cwvap_guard_hybrid_exit_disabled():
    """Verify hybrid active exit does not trigger if disabled via config."""
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.hybrid_exit_enabled = False
    
    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0
    )
    
    st = SavgolCTSExitState()
    
    row = {
        "close": 95.0,
        "cwvap": 100.0,
        "fas": -0.1,
        "psz_v": -0.1
    }
    
    reason, next_state = apply_cwvap_guard(row, trade, None, st.to_int(), cfg, [], 1)
    
    assert reason is None


def test_cwvap_guard_cwc_slope_early_release():
    """Verify that a suppressed exit is released early when cwc_slope drops below threshold."""
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.cwc_slope_early_release_enabled = True
    cfg.cwvap_guard.cwc_slope_early_release_threshold = -0.01

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0
    )

    # State: Suppressed exit is active
    st = SavgolCTSExitState()
    st.exit_suppressed = True

    # Row features: Price > CWVAP, but cwc_slope is -0.015 (< -0.01)
    row = {
        "close": 105.0,
        "cwvap": 100.0,
        "cwc_slope": -0.015,
        "price_slope_z": 0.5,
        "cts": 0.5
    }

    reason, next_state = apply_cwvap_guard(row, trade, None, st.to_int(), cfg, [], 1)

    # Should be released early
    assert reason == ExitReason.CWC_SLOPE_EARLY_RELEASE
    next_st = SavgolCTSExitState.from_int(next_state)
    assert next_st.exit_suppressed is False


def test_cwvap_guard_cwc_slope_immediate_release():
    """Verify that an exit proposed on the current bar is released immediately if cwc_slope is below threshold."""
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.cwc_slope_early_release_enabled = True
    cfg.cwvap_guard.cwc_slope_early_release_threshold = -0.01

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0
    )

    st = SavgolCTSExitState()

    row = {
        "close": 105.0,
        "cwvap": 100.0,
        "cwc_slope": -0.015,
        "price_slope_z": 0.5,
        "cts": 0.5
    }

    reason, next_state = apply_cwvap_guard(row, trade, ExitReason.ST_CROSS, st.to_int(), cfg, [], 1)

    # Should not be suppressed; should be released immediately with the proposed reason
    assert reason == ExitReason.ST_CROSS
    next_st = SavgolCTSExitState.from_int(next_state)
    assert next_st.exit_suppressed is False

