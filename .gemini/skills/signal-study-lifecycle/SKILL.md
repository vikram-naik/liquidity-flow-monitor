---
name: signal-study-lifecycle
description: Lifecycle workflow for executing ad-hoc signal studies, including script creation, single-symbol validation, and watchlist-wide performance analysis with standard metric reporting. Use when asked to test a new signal idea or iterate on entry/exit logic.
---

# Signal Study Lifecycle

This skill defines the end-to-end lifecycle for evolving a new trading signal path through isolated studies before core integration.

## Phase 1: Thesis & Study Script
1. **Define Signal Logic:** Express the new entry/exit rules in plain English and pseudocode.
2. **Scaffold Study:** Create `scripts/study_<name>.py`.
   - Use `get_watchlist_symbols("NIFTY 50")` to load the universe.
   - Use `DivergenceEngine` to load full history (indicator warmup).
   - Implement `simulate_trades_study` to track `Trade` objects.
3. **MFE/MAE Extraction:** Ensure the study script extracts `mfe_pct` and `mae_pct` for every trade by searching forward from entry to exit in the ledger.

## Phase 2: Isolation & Logic Debugging
Before running a full backtest, validate the logic on a known symbol.
1. **Target Run:** Run the study for a specific symbol:
   ```bash
   ./venv/bin/python scripts/study_<name>.py --symbol <SYMBOL>
   ```
2. **Missing Signal Debug:** If a expected signal is missing, create a surgical debug script (e.g., `scripts/debug_<symbol>.py`) that prints the exact gate-by-gate pass/fail status for the target date.
3. **Logic Iteration:** Relax or tighten conditions based on the debug output (e.g., switching from "strictly rising" to "A0 > A3").

## Phase 3: Performance Analysis & Grading
1. **Watchlist Run:** Execute the study on the "NIFTY 50" watchlist.
2. **Signal Grading:** Implement a multi-factor score (e.g., ELITE, STRONG, WEAK) based on how many supportive conditions were met (confluences).
3. **Metric Comparison:** Use the "Before vs After" reporting standard.

## Phase 4: Standard Reporting
When sharing results, you MUST report the following comparison metrics for the TEST period:
- **Total Trades**
- **Win Rate %**
- **Avg P&L %**
- **Profit Factor**
- **Expectancy %**
- **Avg MFE / MAE %**
- **Avg Duration**

## Pitfalls & Failure Shields
- **EOD-Lag Error:** Ensure signals trigger on bar `i` and entries happen on bar `i+1` open.
- **Premature Exit Bias:** Use MFE analysis to detect if a logic change is "cutting winners too short" (evidenced by a sharp drop in MFE on winning trades).
- **NaN Handling:** Always use `not np.isnan(val)` guards before comparing metrics like `cts` or `fas`.
