# CTS Analysis & Strategy Reference

This document serves as the "Ground Truth" for the Continuous Trend Score (CTS) development within the Liquidity Flow Monitor project. It captures architectural decisions, mathematical findings from our simulations, and the roadmap for future enhancements.

## 1. Core Architecture: The Savitzky-Golay (Savgol) Strategy
After comparing multiple smoothing techniques (EMA, DEMA, KAMA), the **Savitzky-Golay** filter was selected as the primary engine for CTS due to its superior responsiveness to trend pivots.

### Configuration Parameters
- **Window Length**: 11 bars (Odd, provides local granularity).
- **Polynomial Order**: 2 (Optimized for "Spot-On" turn detection without noise-driven over-fitting).
- **Metric**: Computed on the **Composite VWAP (CWVAP)** series.
- **Normalization**: Velocity (1st Deriv) and Acceleration (2nd Deriv) are normalized by **ATR_20** and a scale factor (5.0).

### Signal Refinement (Regime-Adaptive Threshold)
Filtered out absolute "micro-wiggles" by applying a dynamic, regime-adaptive threshold:
- **Threshold**: Rolling 35th percentile of the absolute `cts_slope` (acceleration) over a 60-bar window.
- **Logic**: A zero-cross in CTS is only valid if the confirming acceleration exceeds this rolling percentile.
- **Rationale**: A rolling window adapts to the stock's current volatility regime, rather than using a single global scalar blended across all market conditions.

---

## 2. Execution Discipline: Walk-Forward vs. Hindsight
During development, we identified a critical distinction between "History" and "Execution."

### The "Ground Truth" of EOD Workflow
1.  **Non-Causal Implementation**: We use the centered (non-causal) Savgol filter. While this technically "repaints" history on a chart, it provides the most accurate mathematical fit for **Post-Market analysis**.
2.  **Real-time Stability**: Our walk-forward simulation (`scripts/test_savgol_walkforward.py`) confirms that while the curve "drifts" slightly as new bars are added, the **Buy/Sell timing typically only shifts by ~1 or few bar(s)**.
3.  **Omni vs. Realized**:
    *   **Omniscient (Hindsight)**: The "ideal" signals seen looking back at a full chart.
    *   **Realized (Walk-Forward)**: The actual EOD signals generated day-by-day. These are the only signals used for P&L and execution.

---

## 3. High-Conviction Conjunction: Savgol + PSZ
We integrated **Price Slope Z-score (PSZ)** to act as a confirmation layer for Savgol signals.

### Current Filter Logic (Adaptive Thresholds — Tuned)
PSZ thresholds are **per-bar, rolling percentiles** derived from the stock's own PSZ distribution (**60-bar window**):
- **Buy (Entry)**: Savgol positive flip AND `psz_buy_threshold ≤ PSZ ≤ 0`.
  - `psz_buy_threshold` = **rolling 10th percentile** of PSZ (avg ~-0.31).
  - *Goal*: Enter when price momentum is in the stock's own bottom decile but stabilizing.
- **Sell (Exit)**: Savgol negative flip OR `PSZ ≥ psz_sell_threshold`.
  - `psz_sell_threshold` = **rolling 70th percentile** of PSZ (avg ~0.24).
  - *Goal*: Capture exits during parabolic extensions relative to the stock's own momentum profile.
  - **Observation**: CTS reaching its maximum value of **1.0** is frequently a high-conviction exhaustion signal and often represents an optimal exit point.

---

## 4. Development Backlog (Future Analysis)
The following items represent the next frontier for improving signal alpha:

### A. ~~Empirical PSZ Thresholds (Adaptive Z)~~ — ✅ Implemented
- Replaced static `-0.3` / `0.2` with rolling 20th/80th percentile thresholds (120-bar window) in `trend_participation.py`.
- See Section 3 for current logic.

### B. Savgol-Filtered PSZ (Normalized Acceleration)
- **Concept**: Apply the Savitzky-Golay filter *to the PSZ series itself*.
- **Benefit**: PSZ can be jittery due to rolling standard deviation noise. SG-filtered PSZ would provide a "Clean Momentum" line, and its derivative would be a powerful indicator of **Velocity of Momentum**.

### C. Rolling CTS Slope Threshold (Regime-Adaptive)
- **Problem**: The current `cts_slope_threshold` is a single 35th-percentile scalar computed over the **entire available history**. This produces a blended value that may be too loose during quiet regimes and too tight during volatile regimes.
- **Goal**: Replace the global percentile with a **rolling-window percentile** (e.g., rolling 60-bar window of `|cts_slope|`) so the threshold adapts to the stock's current volatility regime, not its lifetime average.
- **Impact**: Should improve signal quality in stocks undergoing regime transitions (e.g., low-vol consolidation → breakout).

### D. Code Hygiene
- Dead code in `base.py:70` — unused `mode='same'` convolution result (`conv_accel`).
- Copy-paste artifact in `kama.py:73-76` — stale `dema_21` reference guarded by `locals()` check, immediately overwritten.
- Duplicate imports in `test_savgol_walkforward.py:15-21` — `DivergenceEngine`, `CompositeVWAP`, `load_symbol_data` imported twice.
- Unused import — `DivergenceEngine` imported but never used in the test script.
- Typo in `test_savgol_walkforward.py:125` — comment says "PSX" instead of "PSZ".

---

## 5. Verification Tools
Run these scripts to reproduce these findings:

- **Strategy Comparison**: `python scripts/compare_cts_strategies.py --ticker <SYMBOL> --days 150`
  - *Compares EMA, DEMA, KAMA vs Savgol Responsiveness.*
- **Walk-Forward Drift**: `python scripts/test_savgol_walkforward.py --ticker <SYMBOL> --window 150`
  - *Simulates daily EOD execution to visualize signal drift and PSZ confirmation.*

---
**Date Published**: March 18, 2026
**Lead Objective**: Precision EOD Trend Pivots
