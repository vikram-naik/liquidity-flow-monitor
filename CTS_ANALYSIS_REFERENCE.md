# CTS Analysis & Strategy Reference

This document serves as the "Ground Truth" for the Continuous Trend Score (CTS) development within the Liquidity Flow Monitor project. It captures architectural decisions, mathematical findings from our simulations, and the roadmap for future enhancements.

## 1. Core Architecture: The Savitzky-Golay (Savgol) Strategy
After comparing multiple smoothing techniques (EMA, DEMA, KAMA), the **Savitzky-Golay** filter was selected as the primary engine for CTS due to its superior responsiveness to trend pivots.

### Configuration Parameters
- **Window Length**: 11 bars (Odd, provides local granularity).
- **Polynomial Order**: 2 (Optimized for "Spot-On" turn detection without noise-driven over-fitting).
- **Metric**: Computed on the **Composite VWAP (CWVAP)** series.
- **Normalization**: Velocity (1st Deriv) and Acceleration (2nd Deriv) are normalized by **ATR_20** and a scale factor (5.0).

### Signal Refinement (Empirical Threshold)
Filtered out absolute "micro-wiggles" by applying a dynamic threshold:
- **Threshold**: 35th percentile of the absolute `cts_slope` (acceleration) history.
- **Logic**: A zero-cross in CTS is only valid if the confirming acceleration exceeds this percentile.

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

### Current Filter Logic
- **Buy (Entry)**: Savgol positive flip AND `-0.3 ≤ PSZ ≤ 0`.
  - *Goal*: Enter when price momentum is deeply oversold but beginning to stabilize.
- **Sell (Exit)**: Savgol negative flip OR `PSZ ≥ 0.2`.
  - *Goal*: Capture exits during parabolic extensions or realized trend exhaustion.

---

## 4. Development Backlog (Future Analysis)
The following items represent the next frontier for improving signal alpha:

### A. Empirical PSZ Thresholds (Adaptive Z)
- **Problem**: `-0.3` and `0.2` are visual estimates, not statistically derived per stock.
- **Goal**: Replace static limits with **percentile-based thresholds** derived from the stock's specific PSZ history (e.g., entering at the 20th percentile of PSZ-lows).

### B. Savgol-Filtered PSZ (Normalized Acceleration)
- **Concept**: Apply the Savitzky-Golay filter *to the PSZ series itself*.
- **Benefit**: PSZ can be jittery due to rolling standard deviation noise. SG-filtered PSZ would provide a "Clean Momentum" line, and its derivative would be a powerful indicator of **Velocity of Momentum**.

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
