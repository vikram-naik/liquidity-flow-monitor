---
name: signal-telemetry-performance
description: Architectural pattern for SavgolCTS signal logic to balance rich telemetry for debugging with high-speed execution for watchlist backtesting using conditional early returns. Use when implementing new entry/exit signals or fixing performance regressions in backtests.
---

# Signal Telemetry & Performance

This skill codifies the "Conditional Early Return" pattern used in SavgolCTS to ensure that rich debugging data (telemetry) doesn't compromise the speed of watchlist-wide backtesting.

## 1. The Conflict: Visibility vs. Velocity

- **Visibility (Debugging)**: When debugging a specific setup (e.g., using `debug_cts_accel_scoring.py`), the signal logic must evaluate *all* gates and calculate *all* scoring components, even if a primary gate fails. This allows for a complete "Why was this rejected?" report.
- **Velocity (Backtesting)**: When running a backtest on 50+ stocks over 5+ years, the signal logic must return `False` as fast as possible. Executing expensive calculations (like Spearman rank correlation) on the 98% of bars that will never result in a trade causes massive slowdowns.

## 2. The Solution: Conditional Early Returns

Implement signal logic using a "Master Gate" check and a conditional telemetry bypass.

### The Design Pattern (ML Funnel)

```python
def check_universal_cross(row, prev_row, cfg, records, idx):
    # 1. THE MASTER GATE (Cheap binary checks)
    trigger_prt = 1 if (prev_prt <= 0 and prt > 0) else 0
    trigger_fas = 1 if (prev_fas <= fas_bt and fas > fas_bt) else 0
    
    # PERFORMANCE BYPASS: Return immediately if no inflection detected.
    if not any([trigger_prt, trigger_fas, ...]):
        return False, 0, {"reason": "No structural inflection"}

    # 2. HEAVY GUARD (ML Inference)
    # Only call the ML Guard if the master gate passed.
    guard = MLGuard.get_instance()
    ml_score_pct = guard.score_setup(row_dict)
    
    if ml_score_pct is None:
        return False, 0, {"reason": "ML Guard unavailable"}

    ml_score_pct *= 100.0

    if ml_score_pct < cfg.universal_cross.min_ml_score:
        return False, 0, {"reason": f"ML Guard rejected ({ml_score_pct:.1f}%)"}

    # 3. Success path
    return True, int(ml_score_pct), {"ml_score": ml_score_pct, ...}
```

## 3. Creating a Debug Tool

Every complex signal path should have a companion debug script (`scripts/debug_<path>_scoring.py`) that allows surgical inspection.

### Script Boilerplate

```python
def main():
    # 1. Load symbol and date from args
    # 2. Initialize DivergenceEngine(ticker=symbol)
    # 3. Run engine to get ledger
    # 4. Extract target row and prior records
    # 5. Call signal logic directly
    passed, intensity, meta = entry_universal_cross(row, prev_row, cfg, records, idx)
    
    # 6. Print final summary
    print(f"Passed: {passed}")
    print(f"Result: {meta.get('reason')}")
```

## 4. Verification Checklist

1.  **Backtest Speed**: Run `scripts/walk_forward.py`. If it takes more than 30-60 seconds per symbol (on average), a heavy guard or ML inference is likely leaking into the "False" path.
2.  **No Redundant Calculation**: Verify that expensive logic (like MLGuard.score_setup) is ONLY invoked inside the `if is_master_triggered` block.
