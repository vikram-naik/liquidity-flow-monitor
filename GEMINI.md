# Liquidity Flow Monitor (LFM) - Core Mandates

## System Architecture
- **Data Pipeline**: `DivergenceEngine.run()` produces a ledger DataFrame (~40+ columns: CTS, BT, CWVAP, PSZ, coherence, PDD, regime, etc.) using 6 sequential modules.
- **Signal Package**: `src/trading/signals/savgol_cts/` orchestrates mean-reversion signals.
- **Execution Model (EOD-Lag)**:
  - Signal fires on bar `i`.
  - Trade opens on bar `i+1`.
  - Exit checks begin on bar `i+2`.
- **Simulation**: `scripts/walk_forward.py` is the primary entry point for backtesting.

## Development Standards
- **Environment**: Use the root `venv`.
- **Outputs**: All generated outputs (tests, studies, backtests) MUST be placed in `./output/`.
- **UI Data**: `src/divergence_engine/chart.py` prepares data for the web UI (`src/web/`).

