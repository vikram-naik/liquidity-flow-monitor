---
name: signal-logic-patterns
description: Architectural patterns and advanced gating techniques for implementing robust mean-reversion signals in the SavgolCTS package. Use when designing or implementing new signal entry/exit logic to ensure consistency with the Gate-Guard-Score (GGS) model and advanced guards like Typical Price Spearman and Adaptive Peak Proximity.
---

# Signal Logic Patterns

This skill codifies the architectural and mathematical patterns used to build high-conviction mean-reversion signals in the LFM system.

## 1. The Universal Funnel Architecture (Modern Standard)

The system has transitioned from multiple complex, hard-wired mechanical paths (Heuristic-Heavy) to a single **Universal Master Path** (ML-Heavy).

### Conceptual Shift
- **Old (Heuristic-Heavy)**: 8+ paths with unique `if/else` rules (e.g., `rp10_max < 0.60`). These are hard to maintain and often over-fit to human intuition.
- **New (ML-Heavy)**: A single wide funnel catches *any* valid structural inflection (PRT, FAS, CTS, or Accel). An XGBoost model classifies the structural state surrounding that inflection to decide if it constitutes a profitable setup.

### The Universal Cross (Path 0)
1. **Trigger OR Logic**: `(PRT Cross) OR (FAS Cross) OR (CTS Cross) OR (Accel Cross)`.
2. **Standardized Context**: Feed the entire technical ledger (40+ columns) to the ML Guard.
3. **ML Decision Boundary**: Rely entirely on the model to find non-linear boundaries between indicators rather than human-written thresholds.

---

## 2. Advanced Entry Gating Patterns (Features for ML)

These patterns are used either as hard guards in secondary sniper paths or as critical input features for the Universal ML model.

### Typical Price Spearman (Free-Fall Detection)
Standard "Close Price" Spearman often misses intraday collapses.
- **Logic**: Calculate 10-bar Spearman correlation on `Typical Price` (`(High + Low + Close) / 3`).
- **Application**: Reject if `Spearman < -0.85` or feed as feature `typ_sp10` to the model.
- **Why**: Captures intraday weakness (lows) that closing-price "fake bounces" hide.

### Distribution Zone Guard (Big Money Dump)
Prevents entering signals where institutions are aggressively selling into the move.
- **Logic**: `REJECT IF accum_div > 0.010`.
- **Why**: High `accum_div` indicates significant institutional selling. Even if momentum indicators look green, we reject if "Big Money" is dumping volume.

### Adaptive Peak Proximity (Momentum Health)
Prevents entering "fizzle" setups where momentum is already retracing from a local peak.
- **Logic**: `REJECT IF current_val < Peak - (Range * 0.15)`
- **Application**: Use on `cts_accel` or `psz_v` over a 5-bar lookback.
- **Why**: Ensures entry occurs while momentum is within 15% of its recent peak, filtering out setups that are "fading" on the signal day.

---

## 3. The GGS (Gate-Guard-Score) Methodology

For specialized "sniper" paths that exist alongside the Universal Funnel, follow this hierarchical structure:

1.  **Gates (Binary Setup)**: The minimum technical conditions (e.g., `cts` crosses `bt`).
2.  **Guards (Hard Safety Filters)**: Binary rejection rules for structural failure modes (e.g., `typ_sp10 < -0.85`).
3.  **Scoring (Multi-Factor Weighting)**: Soft penalties/rewards (e.g., `-10 pts` for shallow correction, `+20 pts` for momentum velocity).
4.  **Intensity Mapping**: Normalization of the final score to a 90-99 range for ranking.

### Correction Depth Penalty (The ONGC Rule)
- **Extremely Shallow (> -1.0%)**: **-20.0 pts** (Kills signals firing at local highs).
- **Shallow (-1.0% to -4.0%)**: **-10.0 pts**.
- **Why**: Using a penalty instead of a hard gate allows high-momentum moves (clean thrusts) to pass if their score is high enough.

---

## 4. Persistent State Machine Exits

For complex exits that require multiple confirmation phases (e.g., reclaim CWVAP -> reach target -> exit on fade), use bitfield latches.

1.  **Phase 1: Recovery Confirmation**: Latch a bit (e.g., `1 << 10`) once the trade escapes the "Danger Zone."
2.  **Phase 2: Target Reached**: Latch a bit once a momentum target is hit (e.g., `psz >= 0.25`).
3.  **Phase 3: Exhaustion Exit**: Trigger the final exit only *after* Phase 1 and 2 are latched and momentum starts to fade (e.g., `psz < 0.0`).

---

## 5. Implementation Guidelines
- **Cheapest Check First**: Always evaluate binary triggers (e.g., `cts > bt`) before invoking the ML Guard or computing heavy Spearman correlations to maintain backtest speed.
- **EOD-Lag Consistency**: All indicators and ML scoring must be evaluated on Bar `i` (Signal Bar) to affect the entry at Bar `i+1`.
- **UI Marker Fix**: Always return a truthy signal intensity (e.g., `int(ml_score_pct)`) to ensure the web UI renders the entry marker correctly.
