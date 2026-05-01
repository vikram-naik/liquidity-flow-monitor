---
name: data-integrity-recovery
description: Workflow for identifying and fixing unexplained price whip-saws using the automated reconciliation and recovery loop. Use when asked to validate data integrity, fix corporate action mismatches, or identify real vs erroneous market events.
---

# Data Integrity and Corporate Action Recovery Skill

This skill provides a standardized workflow for identifying and fixing unexplained price whip-saws (sharp drops or rises) that often indicate missed corporate actions (splits, bonuses, demergers) or source data corruption.

## 1. The One-Pass Recovery Loop

Whenever you detect a large daily price change (>10%) that isn't confirmed as a market event, run the automated recovery loop. This command executes a complete cycle: **Detect -> Official Sync -> Internet Reconciliation -> Auto-Patching -> Final Verification**.

```bash
./venv/bin/python scripts/validate_data_integrity.py --watchlist "NIFTY 50" --auto-fix --auto-patch
```

*Note: For a single stock, use `--symbol <SYMBOL>` instead of `--watchlist`.*

## 2. Recovery Phases

The script processes every identified "whip-saw" through these phases:

### Phase 1: Official NSE Sync (`--auto-fix`)
- Triggers `scripts/sync_nse_ca.py` for the symbol to fetch official data from the exchange.
- Flushes the Redis cache to remove stale adjusted price data.
- **Verification:** Re-runs the internal detection logic. If the move disappears, it was a simple missed corporate action.

### Phase 2: Internet Reconciliation (`yfinance`)
- If the whip-saw persists, the system fetches adjusted OHLC data from `yfinance`.
- **Crucial Metric:** It compares the **daily percentage return** on the day of the event, NOT the absolute price.
- **Why Return-Based?** Absolute prices often drift over time due to dividend adjustments in external sources. Comparing daily returns safely ignores this "dividend contamination" and proves if the demerger/split ratio was correct.

### Phase 3: Automated Patching (`--auto-patch`)
- If a mismatch is found (local return != internet return), the system calculates the **Golden Correction Factor** required to align the data.
- The correction is saved permanently to the `ca_overrides` database table.
- The script then automatically re-triggers a sync/flush to apply the patch.

## 3. Interpreting the Report

The script generates a categorized console report:

- **`[OK] VERIFIED MARKET EVENTS`**: These are sharp price moves confirmed by the internet source. They are **not** errors; they represent real volatility (e.g., COVID crash). Do not attempt to fix these.
- **`[!!!] INTEGRITY FAILURES`**: These are persistent mismatches where local data deviates from the internet source even after a sync.
- **`[RESOLVED]`**: Issues that were successfully fixed during the official sync or via auto-patching.

## 4. Manual Overrides (Edge Cases)

If automation fails or you have a specific "Golden Factor" from a company announcement:
1. Identify the problematic `ex_date`.
2. Add a manual entry to the `ca_overrides` table or use the CLI:
   ```bash
   ./venv/bin/python scripts/sync_nse_ca.py <SYMBOL> --ca-override "<YYYY-MM-DD>:<FACTOR>" --yes
   ```
3. Run the validation script with `--auto-fix` to verify the impact.
