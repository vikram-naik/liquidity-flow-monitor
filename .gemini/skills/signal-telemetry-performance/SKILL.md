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

### The Design Pattern

```python
def check_my_signal(row, prev_row, cfg, records, idx):
    # 1. Initialize Telemetry (ALWAYS required if telemetry_enabled is True)
    tracker = ScoreTracker(base_score=15.0, min_score=cfg.min_score, enabled=cfg.telemetry_enabled)
    first_fail_reason = None

    # 2. THE MASTER GATE (The "Master Trigger")
    # Use the cheapest possible binary check (e.g., a simple crossover).
    is_master_triggered = (prev_cts <= prev_cts_bt) and (cts > cts_bt)
    
    tracker.add_gate("Master Trigger", is_master_triggered, "Cheap binary check")
    
    # PERFORMANCE BYPASS: Return immediately if not triggered AND telemetry is OFF.
    if not is_master_triggered:
        if not cfg.telemetry_enabled:
            return False, 0, {"reason": "Not triggered"}
        if first_fail_reason is None:
            first_fail_reason = "Not triggered"

    # 3. HEAVY GUARDS (Expensive Calculations)
    # Only slice windows or call scipy functions if master gate passed OR telemetry is ON.
    if is_master_triggered or cfg.telemetry_enabled:
        # Example: Expensive Spearman rank correlation
        vals = [r.get('close') for r in records[idx-9 : idx+1]]
        sp_corr = evaluate_spearman_trend(vals)
        
        is_safe = sp_corr > -0.85
        tracker.add_gate("Price Stability", is_safe, f"Spearman: {sp_corr:.2f}")
        
        if not is_safe and first_fail_reason is None:
            first_fail_reason = "Price Free-fall"

    # 4. FINAL EVALUATION
    passed = tracker.passed_all_gates() and tracker.passed_scoring()
    
    # If telemetry is enabled, print the table (useful for surgical debugging)
    if cfg.telemetry_enabled:
        tracker.print_table()
        
    if not passed:
        return False, 0, {"reason": first_fail_reason or "Scoring failed", "tracker": tracker}
        
    # Success path...
    intensity = compute_intensity(tracker.total)
    return True, intensity, {"score": tracker.total, "tracker": tracker}
```

## 3. Creating a Scored Debug Tool

Every complex signal path should have a companion debug script (`scripts/debug_<path>_scoring.py`) that leverages the telemetry.

### Script Boilerplate

```python
def main():
    # 1. Load symbol and date from args
    # 2. Initialize DivergenceEngine(ticker=symbol)
    # 3. Run engine to get ledger
    # 4. Extract target row and prior records
    # 5. Call signal with telemetry enabled
    cfg = MySignalEntryConfig(telemetry_enabled=True)
    passed, intensity, meta = check_my_signal(row, prev_row, cfg, records, idx)
    
    # 6. Print final summary
    print("\n[FINAL RESULT]")
    print(f"Passed Gates: {passed}")
    if not passed:
        print(f"Failure Reason: {meta.get('reason', 'Unknown')}")
    else:
        print(f"Intensity: {intensity}")
```

## 4. Verification Checklist

1.  **Backtest Speed**: Run `scripts/walk_forward.py` on a single watchlist. If it takes more than 30-60 seconds per symbol (on average), a heavy guard is likely leaking into the "False" path.
2.  **Debug Integrity**: Run the `debug_scoring` script for a known rejected stock. It MUST show the results of *all* gates, not just the one that failed.
3.  **No Redundant Calculation**: Verify that lookback windows (slicing) and external math libraries (`scipy.stats`) are only invoked inside the `if is_master_triggered or cfg.telemetry_enabled` block.
