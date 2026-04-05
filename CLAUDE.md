# Liquidity Flow Monitor (LFM) - Core Mandates

## System Architecture
- **Data Pipeline**: `DivergenceEngine.run()` produces a ledger DataFrame (~40+ columns: CTS, BT, CWVAP, PSZ, coherence, PDD, regime, etc.) using 6 sequential modules.
- **Signal Package**: `src/trading/signals/savgol_cts/` orchestrates mean-reversion signals.
- **Execution Model (EOD-Lag)**:
  - Signal fires on bar `i`.
  - Trade opens on bar `i+1`.
  - Exit checks begin on bar `i+2`.
- **Simulation**: `scripts/walk_forward.py` is the primary entry point for backtesting.

## Trading Pipeline (Scan → Approve → Execute)
- **Scanner** (`src/trading/scanner.py`): Post-market signal detection only. Phase 1: check exits on open positions → mark `pending_exit`. Phase 2: scan new entry signals → `proposed`. Phase 3: P&L snapshot. Does NOT place orders.
- **Executor** (`src/trading/executor.py`): Resolves prices and places orders for `pending_entry` (BUY) and `pending_exit` (SELL). Triggered via `POST /de/api/trading/execute` or CLI `venv/bin/python3 -m src.trading.executor`.
- **Price Resolver** (`src/trading/price_resolver/`): Package with ABC + implementations. `HistoricalResolver`: BUY=execution-day close, SELL=execution-day open (matches walk_forward.py). `LiveResolver`: stub for Kite API.
- **Broker** (`src/trading/broker/`): Package with ABC + implementations. `PaperBroker` for simulation, `KiteBroker` stub for live.
- **Position lifecycle**: `proposed` → `pending_entry` (approved) → `open` → `pending_exit` (exit signal) → `closed`.
- All orders are LIMIT orders. Config key `price_resolver` in `trading_config` controls which resolver is used (default: `historical`).

## Development Standards
- **Environment**: Use the root `venv`.
- **Outputs**: All generated outputs (tests, studies, backtests) MUST be placed in `./output/`.
- **UI Data**: `src/divergence_engine/chart.py` prepares data for the web UI (`src/web/`).
