
import pytest
import numpy as np
from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import CtsAccelCrossExitConfig
from src.trading.signals.savgol_cts.exits.cts_accel_cross import exit_cts_accel_cross
from src.trading.signals.savgol_cts.state import SavgolCTSExitState

def test_cwvap_rejection_exit():
    cfg = CtsAccelCrossExitConfig(cwvap_rejection_limit=3)
    trade = Trade(
        symbol="TEST",
        entry_date="2024-01-01",
        entry_price=100.0,
        entry_idx=10,
        atr_at_entry=2.0,
        soft_filters_passed=0
    )
    
    # Bar 1: Rejection
    row1 = {"close": 98.0, "high": 102.0, "cwvap": 100.0, "cts": 0.0, "cts_sell_threshold": 0.5}
    prev1 = {"cts": 0.0, "cts_sell_threshold": 0.5}
    reason, state_val = exit_cts_accel_cross(row1, prev1, trade, 100.0, 1, 0, cfg)
    assert reason is None
    st = SavgolCTSExitState.from_int(state_val)
    assert st.cwf_count == 1
    
    # Bar 2: Rejection
    row2 = {"close": 99.0, "high": 101.5, "cwvap": 100.0, "cts": 0.0, "cts_sell_threshold": 0.5}
    reason, state_val = exit_cts_accel_cross(row2, row1, trade, 100.0, 2, state_val, cfg)
    assert reason is None
    st = SavgolCTSExitState.from_int(state_val)
    assert st.cwf_count == 2
    
    # Bar 3: Rejection -> Trigger Exit
    row3 = {"close": 97.0, "high": 103.0, "cwvap": 100.0, "cts": 0.0, "cts_sell_threshold": 0.5}
    reason, state_val = exit_cts_accel_cross(row3, row2, trade, 100.0, 3, state_val, cfg)
    assert reason == ExitReason.CWVAP_REJECTION
    st = SavgolCTSExitState.from_int(state_val)
    assert st.cwf_count == 3

def test_cwvap_rejection_reset():
    cfg = CtsAccelCrossExitConfig(cwvap_rejection_limit=3)
    trade = Trade(
        symbol="TEST",
        entry_date="2024-01-01",
        entry_price=100.0,
        entry_idx=10,
        atr_at_entry=2.0,
        soft_filters_passed=0
    )
    
    # Bar 1: Rejection
    row1 = {"close": 98.0, "high": 102.0, "cwvap": 100.0, "cts": 0.0, "cts_sell_threshold": 0.5}
    reason, state_val = exit_cts_accel_cross(row1, row1, trade, 100.0, 1, 0, cfg)
    assert SavgolCTSExitState.from_int(state_val).cwf_count == 1
    
    # Bar 2: Hold above CWVAP -> Reset
    row2 = {"close": 101.0, "high": 102.0, "cwvap": 100.0, "cts": 0.0, "cts_sell_threshold": 0.5}
    reason, state_val = exit_cts_accel_cross(row2, row1, trade, 101.0, 2, state_val, cfg)
    assert reason is None
    assert SavgolCTSExitState.from_int(state_val).cwf_count == 0
