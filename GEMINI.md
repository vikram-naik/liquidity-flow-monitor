# Liquidity Flow Monitor (LFM) - Core Mandates

## System Architecture
- **Data Pipeline**: `DivergenceEngine.run()` produces a ledger DataFrame (~40+ columns: CTS, BT, CWVAP, PSZ, coherence, PDD, regime, etc.) using 6 sequential modules.
- **Signal Package**: `src/trading/signals/savgol_cts/` orchestrates mean-reversion signals.
- **Execution Model (EOD-Lag)**:
  - Signal fires on bar `i`.
  - Trade opens on bar `i+1`.
  - Exit checks begin on bar `i+2`.
- **Simulation**: `scripts/walk_forward.py` is the primary entry point for backtesting.
- **Debugging Universal Scoring**: To inspect the gate checks and detailed scoring telemetry for the ``UniversalCross`` entry on a specific stock and date, run the debug script:
  ```bash
  ./venv/bin/python scripts/debug_universal_scoring.py --symbol <SYMBOL> --date <YYYY-MM-DD>
  ```

## Development Standards
- **Environment**: Use the root `venv`.
- **Outputs**: All generated outputs (tests, studies, backtests) MUST be placed in `./output/`.
- **UI Data**: `src/divergence_engine/chart.py` prepares data for the web UI (`src/web/`).
- **DivergenceEngine Initialization**: NEVER pass `start_date` and `end_date` during `DivergenceEngine` initialization (e.g. `DivergenceEngine(ticker)`). Always load the entire history first so that indicators warm up correctly, then filter the resulting ledger `DataFrame` by date.
- **Test-Driven Investigation**: Whenever you are investigating an issue (bug, unexpected signal/exit, etc.) and find that a unit test case for that scenario is missing, you MUST add it to the relevant test file (or create a new one) to verify the fix and prevent future regressions.
- **Cache Management**: After making code changes that affect `DivergenceEngine` calculations or signal logic, you MUST flush the Redis cache to ensure stale data does not interfere with debugging or backtesting:
  ```bash
  ./venv/bin/python scripts/flush_cache.py --all
  ```
- **Data Integrity Validation**: To identify and fix unexplained price whip-saws (potential missed corporate actions), run the validation script. It will automatically trigger `sync_nse_ca.py` if `--auto-fix` is provided. If whip-saws persist after sync, it performs an automated reconciliation with `yfinance` to verify if the move was a real market event by comparing daily percentage returns (to ignore historical dividend drift). This check is part of the daily EOD sync.
- **Corporate Action Overrides**: If a specific demerger or corporate action ratio is inaccurate, the system uses a permanent override stored in the `ca_overrides` database table. `sync_nse_ca.py` prioritizes these over its own estimates. Use `--auto-patch` in the integrity script to automatically calculate and apply these from internet data in a single automated loop (detect -> fix -> reconcile -> patch -> re-sync -> verify):
  ```bash
  # Manual sync with one-pass auto-patching and recovery
  ./venv/bin/python scripts/validate_data_integrity.py --watchlist "NIFTY 50" --auto-fix --auto-patch
  ```

