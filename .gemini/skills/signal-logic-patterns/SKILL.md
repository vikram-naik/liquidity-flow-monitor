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

### Distribution Zone Guard (Big Money Dump)
Prevents entering signals where institutions are aggressively selling into the move.
- **Logic**: `REJECT IF accum_div > 0.010`.
- **Why**: High `accum_div` values indicate significant institutional selling. Even if momentum looks green, we reject if "Big Money" is dumping volume.

### Adaptive Peak Proximity (Momentum Health)
Prevents entering "fizzle" setups where momentum is already retracing from a local peak.
- **Logic**: `REJECT IF current_val < Peak - (Range * 0.15)`
- **Application**: Use on `cts_accel` or `psz_v` over a 5-bar lookback.
- **Why**: Ensures we only enter while momentum is within 15% of its recent peak, filtering out setups that are "fading" on the signal day.

### Institutional Floor Pin (Capitulation)
Rejects entries where the "Big Money" is still dumping at maximum intensity.
- **Logic**: `REJECT IF cts <= -0.95 AND cts_slope < 0`.
- **Why**: A технических bounce is irrelevant if institutional selling has not even begun to decelerate (slope must be at least flattening).

## 3. Scored Quality Filtering (Soft Guards)

Modern paths (like Path 13) use penalties instead of hard gates to allow "Elite Clean-Thrusts" to pass while killing marginal traps.

### Correction Depth Penalty (The ONGC Rule)
- **Extremely Shallow (> -1.0%)**: **-20.0 pts** (Kills signals firing at local highs).
- **Shallow (-1.0% to -4.0%)**: **-10.0 pts**.
- **Why**: Shallow corrections are risky. Using a penalty instead of a hard gate allows high-momentum moves (clean thrusts) to pass if their score is high enough.

### Red Bar Penalty (Intraday Divergence)
- **Logic**: `IF Close < Open THEN -15.0 pts`.
- **Why**: A red candle on a crossover day is a divergence. We only allow it if the institutional thrust is powerful enough to overcome the intraday selling pressure.

## 4. Utility-Based Trend Gating

Leverage standardized helpers in `src/trading/signals/savgol_cts/entries/utils.py` to implement robust momentum guards.

### Adaptive Flatness Check
Prevents entering when the engine or momentum has "no pulse."
- **Helper**: `is_flattish_line_adaptive(y1, y2, y3, lookback_window_data, sensitivity=0.15)`.
- **Logic**: Calculates tolerance dynamically based on the recent range of the data.
- **Why**: A fixed tolerance fails when switching between low-volatility and high-volatility symbols.

### Spearman Trend Consistency
Ensures the move is structural and not just a one-bar spike.
- **Helper**: `evaluate_spearman_trend(y_values)`.
- **Application**:
    - `psz_v` (Momentum Quality): Scored via linear interpolation.
    - `cts_accel` (Accel Quality): Scored via linear interpolation.
- **Thresholds**:
    - `> 0.80`: Strong, visually obvious trend.
    - `0.50 - 0.80`: Choppy but rising.
    - `< 0.30`: Weak or sideways (Candidate for penalty).

## 5. Persistent State Machine Exits

For complex exits that require multiple confirmation phases (e.g., reclaim CWVAP -> reach target -> exit on fade), use bitfield latches in `delivery_bad_count`.

1.  **Phase 1: Recovery Confirmation**: Latch a bit (e.g., `recovery_passed = 1 << 10`) once the trade has successfully escaped the "Danger Zone."
2.  **Phase 2: Target Reached**: Latch a bit once a momentum target is hit (e.g., `psz >= 0.25`).
3.  **Phase 3: Exhaustion Exit**: Trigger the final exit only *after* Phase 1 and 2 are latched and momentum starts to fade (e.g., `psz < 0.0`).
4.  **Implementation**: Define constants in `src/trading/signals/savgol_cts/state.py` and wrap the integer in `SavgolCTSExitState`.

## 6. ML Guarded Entry Logic (Universal Master Path)

Modern signals (Universal Cross) replace manual Bayesian scoring with a machine-learning model (`XGBoost`) to rank setups.

- **Trigger Inflections**: Identify cheap structural inflections (PRT Cross, FAS Cross, CTS Cross).
- **ML Scoring**: Feed the entire indicator state at the inflection bar to the `MLGuard`.
- **Confidence Threshold**: Only accept setups where the ML model's probability of a "Good Trade" (2.5% PnL) exceeds a high threshold (e.g., 85.0%).
- **Telemetry**: The confidence score overrides the standard intensity score for UI visualization.

## 7. Implementation Guidelines
- **Master Gate**: Always use the cheapest binary check first. Bypass the ML Guard or "Heavy Guards" if no trigger occurred to maintain backtest speed.
- **EOD-Lag**: All indicators and ML scoring must be evaluated on Bar `i` (Signal Bar) to affect the entry at Bar `i+1`.
