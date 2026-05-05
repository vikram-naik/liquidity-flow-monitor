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

## 4. Identifying the Lever

Compare the trace of a **Hard Stop** setup vs. a **Winner** setup. Look for the "Lever"—the specific indicator value that separates them.

- **Gate Overlap:** If a winner has a deeper price drop than a loser, do NOT use a hard gate.
- **Scoring Penalty (Lever):** If indicators are conflicting, implement a heavy penalty (e.g., `-11.0`) in the scoring section. This forces the signal to have overwhelmingly positive secondary alignment to pass.

## 5. Implementing Surgical Levers

### Entry Lever (Penalty)
Apply in `src/trading/signals/savgol_cts/entries/<path>.py`:
```python
# Penalty: Structural Free-Fall
if prt < -0.45 and prt_slope < -0.02:
    base_score -= 11.0  # Soft-rejection lever
```

### Exit Lever (Stateful)
Apply in `src/trading/signals/savgol_cts/exits/<path>.py` using `SavgolCTSExitState` bits.
- **Stall Exit:** If price momentum (`PSZ`) fails a second time (Cycle 2) without hitting the profit target, exit immediately with `ExitReason.PSZ_STALL`.

## 6. Thrust Guards (Adaptive Flatness)

Use `is_flattish_line_adaptive` from `src/trading/signals/savgol_cts/entries/utils.py` to filter out anemic or "lazy" signals.
- **Thrust Requirement:** Require the `range_spread` of the acceleration or velocity to be greater than the `dynamic_tolerance` calculated from recent volatility.

## 7. Cleanup

ALWAYS remove investigative columns from `dump_trades.py` and delete temporary trace scripts before finishing the task.
