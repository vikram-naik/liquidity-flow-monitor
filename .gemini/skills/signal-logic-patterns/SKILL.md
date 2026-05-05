---
name: signal-logic-patterns
description: Architectural patterns and advanced gating techniques for implementing robust mean-reversion signals in the SavgolCTS package. Use when designing or implementing new signal entry/exit logic to ensure consistency with the Gate-Guard-Score (GGS) model and advanced guards like Typical Price Spearman and Adaptive Peak Proximity.
---

# Signal Logic Patterns

This skill codifies the architectural and mathematical patterns used to build high-conviction mean-reversion signals in the LFM system.

## 1. The GGS (Gate-Guard-Score) Architecture

All modern SavgolCTS entry paths follow a hierarchical logic structure to balance sensitivity (not missing winners) with specificity (rejecting traps).

1.  **Gates (Binary Setup)**: The minimum technical conditions for the signal.
    - *Example*: `cts` crosses `buy_threshold` from below.
    - *Role*: Identifies the potential setup.
    - **Master Gate**: The first, cheapest binary check. If this fails, the system should bypass all "Heavy Guards" to maintain backtest speed.
2.  **Guards (Hard Safety Filters)**: Binary rejection rules for structural failure modes.
    - *Example*: `Typical Price Spearman < -0.85` (Free-fall detection).
    - *Role*: Failure shield to eliminate high-probability losing environments.
    - **Heavy Guards**: Calculations involving window slicing or external math libraries (`scipy`).
3.  **Scoring (Multi-Factor Weighting)**: Soft penalties and rewards that aggregate supporting evidence.
    - *Example*: `-10 pts` for shallow corrections, `+20 pts` for high momentum velocity.
    - *Role*: Differentiates "Elite" setups from "Dud" setups.
4.  **Intensity Mapping**: Normalization of the final score to a 90-99 range for ranking.

## 2. Advanced Entry Gating Patterns

### Typical Price Spearman (Free-Fall Detection)
Standard "Close Price" Spearman often misses intraday collapses.
- **Logic**: Calculate 10-bar Spearman correlation on `Typical Price` (`(High + Low + Close) / 3`).
- **Threshold**: Reject if `Spearman < -0.85`.
- **Why**: Captures intraday weakness (lows) that closing-price "fake bounces" hide.

### Adaptive Peak Proximity (Momentum Health)
Prevents entering "fizzle" setups where momentum is already retracing from a local peak.
- **Logic**: `REJECT IF current_val < Peak - (Range * 0.15)`
- **Application**: Use on `cts_accel` or `psz_v` over a 5-bar lookback.
- **Why**: Ensures we only enter while momentum is within 15% of its recent peak, filtering out setups that are "fading" on the signal day.

### Institutional Floor Pin (Capitulation)
Rejects entries where the "Big Money" is still dumping at maximum intensity.
- **Logic**: `REJECT IF cts <= -0.95 AND cts_slope < 0`.
- **Why**: A технических bounce is irrelevant if institutional selling has not even begun to decelerate (slope must be at least flattening).

## 3. Persistent State Machine Exits

For complex exits that require multiple confirmation phases (e.g., reclaim CWVAP -> reach target -> exit on fade), use bitfield latches in `delivery_bad_count`.

1.  **Phase 1: Recovery Confirmation**: Latch a bit (e.g., `recovery_passed = 1 << 10`) once the trade has successfully escaped the "Danger Zone."
2.  **Phase 2: Target Reached**: Latch a bit once a momentum target is hit (e.g., `psz >= 0.25`).
3.  **Phase 3: Exhaustion Exit**: Trigger the final exit only *after* Phase 1 and 2 are latched and momentum starts to fade (e.g., `psz < 0.0`).
4.  **Implementation**: Define constants in `src/trading/signals/savgol_cts/state.py` and wrap the integer in `SavgolCTSExitState`.

## 4. Bayesian Scoring and Intensity Mapping

When implementing `src/trading/signals/savgol_cts/scoring.py` or path-specific scoring:

- **ScoreTracker Pattern**: Use a `ScoreTracker` class to aggregate points and reasons.
- **Base Score**: Start with a base score (e.g., 15.0 or 20.0).
- **Linear Interpolation**: Use `_interp(val, min_val, max_val, min_pts, max_pts)` for smooth scoring.
- **Intensity Mapping**: Map the final internal score to the 90-99 range for the UI:
  ```python
  intensity_pts = 90.0 + (tracker.total - min_possible) / (max_possible - min_possible) * 9.0
  ```

## 5. Implementation Guidelines
- **Telemetry**: Always use `ScoreTracker` (if available) or verbose logging to print the result of every Gate, Guard, and Score component during `check_entry`.
- **Conditional Early Returns**: Use the "Conditional Early Return" pattern to ensure that "Heavy Guards" only execute if the "Master Gate" passes or telemetry is explicitly enabled. This prevents backtest slowdowns.
- **EOD-Lag**: All guards and scores must be evaluated on Bar `i` (Signal Bar) to affect the entry at Bar `i+1`.
