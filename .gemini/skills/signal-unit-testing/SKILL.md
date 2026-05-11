---
name: signal-unit-testing
description: Workflow for creating isolated unit tests for SavgolCTS entry and exit logic using mocked data rows. Use when mandated to add a test case during an investigation or when implementing a new signal path to prevent regressions.
---

# Signal Unit Testing

This skill provides a standardized workflow for implementing isolated unit tests for trading signals, adhering to the project mandate of test-driven investigation.

## 1. Test Structure and Location

All signal-related tests live in the `tests/` directory.
- **Entries**: Test against the logic in `src/trading/signals/savgol_cts/entries/`.
- **Exits**: Test against the logic in `src/trading/signals/savgol_cts/exits/`.

## 2. Mocking Data Rows

LFM signals operate on `row` (current bar) and `prev_row` (previous bar) dictionaries. You do not need a full database to test them.

### Minimal Mock Row
```python
row = {
    "date": "2024-01-02",
    "close": 100.0,
    "high": 105.0,
    "low": 95.0,
    "cts": -0.95,
    "cts_buy_threshold": -0.90,
    "cts_accel": 0.05,
    "cts_accel_threshold": 0.02,
    "psz_v": 0.01,
    "prt_slope": -0.01,
    "range_pos_10": 0.15,
    "range_pos_252": 0.20,
    "cwvap": 102.0
}
```

## 3. Testing Entry Logic

Use `pytest` to verify that a specific combination of indicators triggers or rejects a signal.

```python
from src.trading.signals.savgol_cts.entries.universal_cross import entry_universal_cross
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig

def test_universal_cross_entry_success():
    cfg = SavgolCTSEntryConfig()
    row = { ... } # Setup winning conditions
    prev = { ... }
    records = [prev, row] # Simplified history
    
    passed, intensity, meta = entry_universal_cross(row, prev, cfg, records, idx=1)
    
    assert passed is True
    assert intensity >= 85
    assert "reason" in meta
```

## 4. Testing Exit Logic

Exits require a `Trade` object and often a `state_val` (bitfield) for multi-phase logic.

```python
from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross
from src.trading.signals.savgol_cts.state import SavgolCTSExitState

def test_universal_cross_exit_trigger():
    trade = Trade(symbol="TEST", entry_price=100.0, entry_date="2024-01-01")
    cfg = SavgolCTSExitConfig().universal_cross
    
    # Test state transition
    row = { "close": 95.0, "cts": -0.99, "cts_sell_threshold": -0.90 }
    reason, state_val = exit_universal_cross(row, prev, trade, peak_close=100.0, bars_held=5, state_val=0, cfg=cfg)
    
    assert reason == ExitReason.ST_CROSS
    # Verify bitfield state if applicable
    st = SavgolCTSExitState.from_int(state_val)
```

## 5. Failure Shields & Regression Tests

When a bug is found (e.g., a trade failed to exit), follow these steps:
1. **Reproduce**: Create a test case with the exact indicator values from the failed trade (found via `debug_stock_features.py`).
2. **Verify Failure**: Run the test to confirm it currently fails (or incorrectly passes).
3. **Fix**: Apply the logic change in the `entries/` or `exits/` module.
4. **Confirm Fix**: Run the test again to verify success.

## 6. Execution

Run all tests from the root using `pytest`:
```bash
./venv/bin/python -m pytest tests/test_my_new_logic.py
```
