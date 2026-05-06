---
name: signal-analysis
description: Standardized workflow for hypothesizing, debugging, and validating changes to entry and exit routines in the Liquidity Flow Monitor (LFM) system. Use this skill when asked to investigate a signal, debug a trade, or analyze the impact of changing entry/exit conditions.
---

# Signal Debugging and Impact Analysis Skill

This skill defines the standardized workflow for hypothesizing, debugging, and validating changes to entry and exit routines in the Liquidity Flow Monitor (LFM) system.

## ⚠️ MANDATORY: EOD-Lag Execution
The LFM system operates strictly on an **End-of-Day Lag (EOD-Lag)** model. You MUST simulate research using this constraint:
- **Signal bar (i):** The bar where indicators/guards are evaluated.
- **Entry bar (i+1):** The bar where the trade is executed (close/open).
- **Exit evaluation (i+2):** Exit checks only begin AFTER entry execution.
- **FAILING TO USE EOD-LAG:** Reporting results based on same-day entry (bar i) is a critical failure that overestimates expectancy and invalidates the study.
  - *Example:* If signal fires on 2026-03-25, entry is 2026-03-26, and the first possible exit check is 2026-03-27.

## Core Mandates
1.  **Universe:** ALWAYS use the `"NIFTY 50"` watchlist for backtesting and trade dumping.
2.  **Decision Making:** ALWAYS base final validation and decisions on the **TEST period** results.
3.  **Environment:** ALWAYS use the virtual environment at the root of the project (`./venv/bin/python`) to execute all scripts.
4.  **Delta Reporting:** When proposing a change, you MUST report the "Before vs After" delta for all core metrics.

## Mandatory Metrics
Consistently report these exact parameters for the **TEST period**:

   - **Number of Trades**
   - Win Rate (%)
   - Avg P&L (%)
   - Profit Factor
   - Expectancy / trade (%)
   - Avg MFE (%)
   - Avg MAE (%)
   - Avg duration (bars)

### Impact Analysis Grid (Example)
| Metric | Baseline (Before) | Optimized (After) | Delta |
| :--- | :--- | :--- | :--- |
| Trades | 142 | 118 | -17% |
| Win Rate | 58.2% | 61.5% | +3.3% |
| Avg P&L | 0.85% | 1.12% | +0.27% |
| Prof. Factor | 1.45 | 1.78 | +0.33 |
| Expectancy | 0.16% | 0.28% | +0.12% |
| Avg MFE | 4.2% | 4.5% | +0.3% |
| Avg Duration | 8.2 | 7.5 | -0.7 |

---

## Phase 1: Isolation & Hypothesis Generation
**Goal:** Data-driven validation of a new trading rule (gate or filter) before modifying core system logic.

1. **Isolate Target Trades:** 
   - Use `scripts/dump_trades.py` to extract historical trades for the specific entry or exit tag. Do NOT use the walk-forward backtester for isolation.
   - **Entry Type Validation:** ALWAYS run the script with `--help` (e.g., `./venv/bin/python scripts/dump_trades.py --help`) to check the exact string choices for the `--entry` argument before execution.
   - Example: `./venv/bin/python scripts/dump_trades.py --entry <entry_type_string> --watchlist "NIFTY 50" --period test`
2. **Build Study Script:** 
   - Create a lightweight, ad-hoc script (e.g., `scripts/study_<feature>.py`) that iterates over these isolated trades.
   - Fetch the historical ledger data for each symbol using `DivergenceEngine`.
   - Compute the new hypothetical metric (e.g., True Gap %, ATR expansion) at the exact index of the historical signal (bar BEFORE the entry date, accounting for EOD-lag).
3. **Threshold Matrix & Impact Analysis:** 
   - Group the historical trades into accepted/rejected buckets across multiple thresholds of the new metric.
   - Output a summary table comparing Win Rate, Avg PnL, and Trade Count for each threshold to find the mathematically optimal cutoff.

## Phase 2: Surgical Signal Debugging
**Goal:** Deep-dive into the exact state and gating logic of a single trade on a specific date to understand why it fired or was rejected.

1. **Identify Edge Case:** Pick a specific symbol and date that represents anomalous behavior (e.g., a massive loss or an incorrectly rejected trade) from the dump or study.
2. **Create/Run Debug Harness:** 
   - Create a minimal script (e.g., `scripts/debug_<feature>.py`) or adapt `scripts/debug_signal.py`.
   - Initialize the `DivergenceEngine` for the target symbol and extract the exact `row`, `prev_row`, and `records` for the signal date.
3. **Enable Telemetry:** Instantiate the specific entry/exit configuration and forcefully enable `telemetry_enabled = True` (or equivalent verbose logging).
4. **Execute & Inspect:** Pass the data directly into the isolated `check_entry` or `check_exit` function to view the gate-by-gate pass/fail console printout and final intensity score.

## Phase 3: Systemic Regression Validation
**Goal:** Ensure that a localized fix does not degrade the broader system or overfit the training data.

1. **Baseline Generation:** 
   - Run the walk-forward backtester BEFORE making code changes.
   - Command: `./venv/bin/python scripts/walk_forward.py --watchlist "NIFTY 50" --signal savgol_cts`
2. **Implement Change:** Apply the new logic and optimal threshold to the core codebase (`src/trading/signals/savgol_cts/`).
   - **Exit Tolerance Pattern**: When fixing "sticky" trades that fail to exit due to decimal noise, add a small tolerance (e.g., `0.01`) to the exit crossover check.
     - *Example*: `IF cts < (cts_sell_threshold + 0.01) THEN exit`.
3. **Validation Run:** Re-run the walk-forward backtester AFTER the change.
4. **Empirical Verification (Grep Guard):**
   - Use `dump_trades.py` to verify that a specific failure case (isolated in Phase 2) is now correctly rejected.
   - *Example*: `./venv/bin/python scripts/dump_trades.py --entry <tag> | grep ETERNAL`.
   - If the failure case still appears, the fix is incomplete or was reverted during scaffolding.
5. **Diff Analysis:** 
   - Extract and compare the exact reporting parameters (Trades, Win Rate, Avg P&L, Profit Factor, Expectancy, MFE, MAE, Avg Bars) for the **TEST period**.
   - Verify that the metrics improved or remained stable without over-constricting the trade frequency.
