---
name: signal-lever-tuning
description: Procedure for surgically refining LFM trading signals by identifying "levers" (scoring penalties and state-based exits) to eliminate hard stops and distribution traps without killing profitable setups. Use when a path is underperforming, hitting hard stops, or catching falling knives.
---

# Signal Lever Tuning Skill

This skill provides a surgical procedure for optimizing existing LFM signals. It focuses on finding the "separation threshold" (lever) that distinguishes a failing setup from a winning one and implementing it as a soft penalty or stateful exit.

## 1. Candidate Isolation

Identify setups that consistently hit the `-8.00%` hard stop or bleed out over long durations.

```bash
# Isolate failures in the TEST period
./venv/bin/python scripts/dump_trades.py --entry <tag-alias> --period test > output/trades.txt
grep "Hard Stop Hit" output/trades.txt
```

## 2. Telemetry Exposure

If the dump report lacks the metrics needed to explain the failure, temporarily add them to `scripts/dump_trades.py`. Common investigative metrics:
- `PRT`, `prt_slope`: Structural trend depth and velocity.
- `accum_div`, `distrib_div`: Institutional effort vs price result.
- `pdd_120`: Price-Delivery Divergence.
- `price_spearman`: Consistency of the immediate price fall/rise.

## 3. Bar-by-Bar Trajectory Tracing

Create a temporary trace script (e.g., `scripts/trace_<tag>.py`) to analyze the trade's life cycle. This reveals where the exit logic "stalled" or where the entry was "fooled" by a shallow bounce.

### Trace Script Template:
```python
import pandas as pd
from src.divergence_engine.engine import DivergenceEngine

def trace(symbol, entry_date):
    ledger = DivergenceEngine(symbol).run().ledger
    idx = ledger[ledger["date"].astype(str).str.startswith(entry_date)].index[0]
    # Log 30 bars from entry
    for i in range(idx, idx + 30):
        row = ledger.iloc[i]
        pnl = (row.close / entry_price - 1) * 100
        print(f"Bar {i-idx} | PnL: {pnl:.2f}% | PSZ: {row.price_slope_z:.3f} | CTS: {row.cts:.3f}")
```

## 4. Identifying the Lever (Threshold Tuning)

In the ML-Guarded architecture, the primary "lever" is the `min_ml_score`. Use `scripts/analyze_score_thresholds.py` to find the optimal balance between win rate and trade frequency.

```bash
./venv/bin/python scripts/analyze_score_thresholds.py
```

### Result Analysis:
- **Low Threshold (80%)**: Captures more winners but allows many "Duds" through (lower win rate).
- **High Threshold (95%)**: High specificity (elite winners) but significantly lower trade count.

## 5. Implementing Surgical Levers

### Retraining (Feature Lever)
If a specific indicator consistently predicts failure but the model is missing it:
1. Ensure the indicator is in the `feature_cols` of `train_ml_guard.py`.
2. Re-extract dense features using `scripts/extract_dense_universal_features.py`.
3. Retrain the model.

### Exit Lever (Stateful)
Apply in `src/trading/signals/savgol_cts/exits/universal_cross.py` using `SavgolCTSExitState` bits.
- **Cycle Guard**: If price momentum fails without hitting a profit target, use the CWVAP guard to release the exit earlier.

## 6. Thrust Guards (Adaptive Flatness)

Use `is_flattish_line_adaptive` from `src/trading/signals/savgol_cts/entries/utils.py` to filter out anemic or "lazy" signals.
- **Thrust Requirement:** Require the `range_spread` of the acceleration or velocity to be greater than the `dynamic_tolerance` calculated from recent volatility.

## 7. Cleanup

ALWAYS remove investigative columns from `dump_trades.py` and delete temporary trace scripts before finishing the task.
