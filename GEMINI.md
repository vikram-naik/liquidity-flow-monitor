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

## Signal Mechanics (savgol_cts)
- **Entry Paths**:
  1. **Slope Bottom**: CTS slope rising from P5 bottom in downtrend/notrend.
  2. **Institutional Floor**: Sustained PSZ recovery + Institutional alignment below CWVAP.
- **Exit Logic**: Dispatched by entry tag, followed by the **CWVAP Guard**.
- **Intensity Scoring**: Computed in `scoring.py` (Range 0-100; >=80 is "STRONG").
