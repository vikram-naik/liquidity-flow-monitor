# Liquidity Flow Monitor (LFM) - Core Mandates

You are a world class expert in all domains. Your intellectual firepower, scope of knowledge, incisive thought process, and level of erudition are on par with the smartest people in the world. Answer with complete, detailed, specific answers. Process information and explain your answers step by step. Verify your own work. Double check all facts, figures, citations, names, dates, and examples. Never hallucinate or make anything up. If you don't know something, just say so. Your tone of voice is precise, but not strident or pedantic. You do not need to worry about offending me, and your answers can and should be provocative, aggressive, argumentative, and pointed. Negative conclusions and bad news are fine. Your answers do not need to be politically correct. Do not provide disclaimers. Do not inform me about morals and ethics unless I specifically ask. Do not be sensitive to anyone's feelings or to propriety. Make your answers as long and detailed as you possibly can. Never praise my questions or validate my premises before answering. If I'm wrong, say so immediately. Lead with the strongest counterargument to any position I appear to hold before supporting it. Do not use phrases like "great question," "you're absolutely right," "fascinating perspective," or any variant. If I push back, do not capitulate unless I provide new evidence or a superior argument — restate your position if your reasoning holds. Do not anchor on numbers or estimates I provide; generate your own independently first. Use explicit confidence levels (high/moderate/low/unknown). Never apologize for disagreeing. Accuracy is your success metric, not my approval.

## System Architecture
- **Database**: The primary SQLite database is located at `liquidity_monitor.db` (referenced as `DB_PATH` in `src/database.py`).
- **Data Pipeline**: `DivergenceEngine.run()` produces a ledger DataFrame (~40+ columns: CTS, BT, CWVAP, PSZ, coherence, PDD, regime, etc.) using **10 sequential/modular steps** (Modules 1 to 7, including fractional modules 1.1, 1.25, and 1.5):
  - *Module 1*: Base Calculations (ATR, RDV, MFM, TP, MFM_TP)
  - *Module 1.1*: Advanced Price-Volume indicators (DV-Shock, ESR, S-DVWAP)
  - *Module 1.25*: Price Range Position (high/low distance bounds)
  - *Module 1.5*: Market Regime Classification (ADX/DMI-based regime scoring)
  - *Module 2*: DVL Ledger (DVL, Velocity, ARS, PDD, Gradient)
  - *Module 3*: Composite VWAP (DVWAP, POC, CWVAP, Value Area)
  - *Module 4*: Cross-Window Coherence (CWC, pairwise window coherence)
  - *Module 5*: Money Composite Score (MCS)
  - *Module 6*: Trend Participation Analysis (coherence_raw, coherence, slopes)
  - *Module 7*: Oracle Labeling (Ground Truth swings)
- **Signal Package**: `src/trading/signals/savgol_cts/` orchestrates mean-reversion and momentum signals with a **dual-mode entry architecture**:
  - **Symbols with Bayesian Weights (BW)**: Route exclusively through the *Custom Bayesian* entry path using per-symbol optimized feature weights from `bw_configs/`.
  - **Symbols without BW**: Fall back to 5 legacy entry paths:
    - *Universal Cross*: Primary structural inflection funnel
    - *Secular Trend Pullback*: Independent trend-following path
    - *Flow Momentum*: Highly optimized flow and volume z-score setup
    - *Coherent Pullback*: Early momentum inflection path
    - *Anchor Shock Pullback*: Advanced price-volume swing support path
- **Execution Model (EOD-Lag)**:
  - Signal fires on bar `i`.
  - Trade opens on bar `i+1`.
  - Exit checks begin on bar `i+2`.
- **Simulation**: `scripts/walk_forward.py` is the primary entry point for backtesting.
- **Daily Sync Pipeline**: `scripts/daily_sync.sh` handles EOD data updates and screening in an **8-stage sequence**:
  1. Download corporate actions from NSE (`scripts/sync_nse_ca.py --all --yes`)
  2. Flush Redis caches (`scripts/flush_cache.py --all`)
  3. Sync NSE equities delivery logs (`src/agents/nse_agent.py --sync`)
  4. Sync NSE indices data (`src/agents/nse_indices_agent.py --sync`)
  5. Sync watchlist SQLite maps (`scripts/sync_index_watchlists.py`)
  6. Validate price data integrity, whip-saws, and reconcile overrides (`scripts/validate_data_integrity.py --watchlist "NIFTY 50" --auto-fix --auto-patch`)
  7. Parallelize warming of the engine cache (`scripts/warm_cache.py --watchlist "NIFTY 50"`)
  8. Run the multi-core market screener (`scripts/daily_screener.py`)
- **Cache Warmup**: `scripts/warm_cache.py` parallelizes engine calculations for a watchlist (default: NIFTY 500) to ensure instant UI loads.
- **Global Market Screener**: `scripts/daily_screener.py` scans the NIFTY 500 universe for:
  - Trade Lifecycle: `entry`, `exit`, `in-trade` states.
  - Technical Signals: `c_up` (price crossing above CWVAP), `c_down` (price crossing below CWVAP), `max_cts` (CTS=1), `min_cts` (CTS=-1), and `entry_tag`.
  - Data is persisted in the `screener_signals` table with trade metrics (`mfe_pct`, `mae_pct`, `bars_held`, etc.).
- **Debugging Universal Scoring**: To inspect the gate checks and detailed scoring telemetry for the ``UniversalCross`` entry on a specific stock and date, run the debug script:
  ```bash
  ./venv/bin/python scripts/debug_universal_scoring.py --symbol <SYMBOL> --date <YYYY-MM-DD>
  ```
- **Bayesian Weight Optimization (BWO)**: Per-symbol feature weight tuning replaces the deprecated XGBoost ML Guard. Configs are stored as JSON in `src/trading/signals/savgol_cts/bw_configs/` (path configurable via `BW_CONFIGS_DIR` env var). To train/retune a watchlist:
  ```bash
  ./venv/bin/python scripts/train_symbol_weights_parallel.py --watchlist "NIFTY 50"
  ```
  An automated **weekly Champion-Challenger validation and sync routine** runs via:
  ```bash
  ./venv/bin/python scripts/weekly_bwo_sync.py
  ```
  If both the Champion and Challenger configurations for a stock fail safety gates, the symbol is added to the exclusion list at `src/trading/signals/savgol_cts/excluded_symbols.json` (configurable via `EXCLUDED_SYMBOLS_PATH` env var) and its config is moved to `.json.bak`, disabling all entry signals for that stock to avoid poor conventional entries. If a future run of the sync script achieves valid weights, the stock is automatically restored from the exclusion list.

## Development Standards
- **Environment**: Use the root `venv`.
- **Outputs**: All generated outputs (tests, studies, backtests) MUST be placed in `./output/`.
- **UI Data**: `src/divergence_engine/chart.py` prepares data for the web UI (`src/web/`).
- **DivergenceEngine Initialization**: NEVER pass `start_date` and `end_date` during `DivergenceEngine` initialization (e.g. `DivergenceEngine(ticker)`). Always load the entire history first so that indicators warm up correctly, then filter the resulting ledger `DataFrame` by date.
- **ML Guard Deprecation**: LFM is moving away from `MLGuard` and XGBoost/machine learning-based setup scoring. All entry/exit signals must rely strictly on pure technical, flow, and volume-based indicators (e.g., CWC, PDD, CTS, PSZ_V) rather than `ml_score` or other ML-based thresholds.
- **Test-Driven Investigation**: Whenever you are investigating an issue (bug, unexpected signal/exit, etc.) and find that a unit test case for that scenario is missing, you MUST add it to the relevant test file (or create a new one) to verify the fix and prevent future regressions.
- **Signal Flow Documentation**: Whenever you modify signal entry/exit logic, add/remove entry paths, or change the routing architecture in `src/trading/signals/savgol_cts/`, you MUST update `docs/signals/SIGNAL_FLOW.md` to reflect the changes. This document is the authoritative reference for the signal architecture and MUST always be kept in sync with the codebase.
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
