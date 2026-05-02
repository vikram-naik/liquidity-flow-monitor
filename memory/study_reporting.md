# Study Reporting Standards - CTS Accel Cross

Whenever reporting on Study Metrics for the Liquidity Flow Monitor, consistently report the "Before" and "After" state of the system when a logic change is introduced.

## Required Metrics for Comparison:
1. **Total Trades** (Volume of signals)
2. **Win Rate %** (Success frequency)
3. **Avg P&L %** (Profitability per trade)
4. **Profit Factor** (Gross Profit / Gross Loss)
5. **Expectancy %** (Expected return per trade)
6. **Avg MFE / MAE %** (Efficiency and drawdown)
7. **Avg Duration** (Hold time)

## ⚠️ Mandatory Simulation Standard (EOD-Lag)
- **Execution Model:** All studies MUST simulate entry on bar `i+1` (next day's close/open) for a signal identified on bar `i`.
- **Reasoning:** The LFM system operates on an End-of-Day Lag model. Reporting results based on bar `i` (same-day momentum) is strictly prohibited as it creates false expectancy (+2.85% vs +0.16% reality).
- **Enforcement:** Any study not explicitly stating "Simulated using EOD-Lag (Entry at i+1)" will be rejected.
