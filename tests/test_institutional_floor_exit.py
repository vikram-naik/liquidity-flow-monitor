import numpy as np
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason, EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState
from src.trading.signals.savgol_cts.exits.institutional_floor import exit_institutional_floor

def test_institutional_floor_exit_max_trail():
    """Verify that Institutional Floor exit trails CTS from above."""
    cfg = SavgolCTSExitConfig()
    trade = Trade(
        symbol="TEST",
        entry_date="2026-01-01",
        entry_price=100.0,
        entry_idx=0,
        atr_at_entry=2.0,
        soft_filters_passed=5,
        entry_tag=EntryTag.INSTITUTIONAL_FLOOR.value,
    )
    
    # Start in Phase 1 (psz_was_above=False)
    state = SavgolCTSExitState()
    state_val = state.to_int()
    
    # 1. PSZ crosses above 0
    row1 = {"close": 105.0, "price_slope_z": 0.1, "cts": 0.2, "cts_sell_threshold": 0.5}
    reason, state_val = exit_institutional_floor(row1, {}, trade, 105.0, 1, state_val, cfg, None, 0)
    assert reason is None
    st = SavgolCTSExitState.from_int(state_val)
    assert st.price_above_cwvap is True # psz_was_positive
    assert st.psz_was_above is False    # not in Phase 2 yet
    
    # 2. PSZ crosses below 0, CTS < ST -> Enter Phase 2
    row2 = {"close": 104.0, "price_slope_z": -0.1, "cts": 0.3, "cts_sell_threshold": 0.5}
    reason, state_val = exit_institutional_floor(row2, row1, trade, 105.0, 2, state_val, cfg, None, 1)
    assert reason is None
    st = SavgolCTSExitState.from_int(state_val)
    assert st.psz_was_above is True # Now in Phase 2
    assert st.cts_above_bt is False # Not reached ST yet
    
    # 3. CTS crosses ABOVE ST -> Should NOT exit (max trail)
    row3 = {"close": 106.0, "price_slope_z": -0.05, "cts": 0.6, "cts_sell_threshold": 0.5}
    reason, state_val = exit_institutional_floor(row3, row2, trade, 106.0, 3, state_val, cfg, None, 2)
    assert reason is None
    st = SavgolCTSExitState.from_int(state_val)
    assert st.cts_above_bt is True # Marked as reached ST
    
    # 4. CTS still ABOVE ST -> Should NOT exit
    row4 = {"close": 107.0, "price_slope_z": 0.0, "cts": 0.7, "cts_sell_threshold": 0.5}
    reason, state_val = exit_institutional_floor(row4, row3, trade, 107.0, 4, state_val, cfg, None, 3)
    assert reason is None
    st = SavgolCTSExitState.from_int(state_val)
    assert st.cts_above_bt is True
    
    # 5. CTS crosses BELOW ST -> Exit
    row5 = {"close": 106.5, "price_slope_z": 0.05, "cts": 0.4, "cts_sell_threshold": 0.5}
    reason, state_val = exit_institutional_floor(row5, row4, trade, 107.0, 5, state_val, cfg, None, 4)
    assert reason == ExitReason.ST_CROSS
    
def test_institutional_floor_exit_already_above_st():
    """Verify that if CTS is already above ST when PSZ cycle ends, it trails."""
    cfg = SavgolCTSExitConfig()
    trade = Trade(
        symbol="TEST",
        entry_date="2026-01-01",
        entry_price=100.0,
        entry_idx=0,
        atr_at_entry=2.0,
        soft_filters_passed=5,
        entry_tag=EntryTag.INSTITUTIONAL_FLOOR.value,
    )
    
    # State: PSZ was already positive
    state = SavgolCTSExitState(price_above_cwvap=True)
    state_val = state.to_int()
    
    # PSZ drops below 0, but CTS is already above ST (e.g. 0.6 > 0.5)
    row = {"close": 104.0, "price_slope_z": -0.1, "cts": 0.6, "cts_sell_threshold": 0.5}
    reason, state_val = exit_institutional_floor(row, {}, trade, 105.0, 2, state_val, cfg, None, 1)
    
    # Before the fix, this would have returned PSZ_GLIDE.
    # Now it should enter Phase 2 and mark as already above threshold.
    assert reason is None
    st = SavgolCTSExitState.from_int(state_val)
    assert st.psz_was_above is True
    assert st.cts_above_bt is True
    
    # Next bar: CTS crosses below ST -> Exit
    row2 = {"close": 103.5, "price_slope_z": -0.15, "cts": 0.45, "cts_sell_threshold": 0.5}
    reason, state_val = exit_institutional_floor(row2, row, trade, 105.0, 3, state_val, cfg, None, 2)
    assert reason == ExitReason.ST_CROSS
