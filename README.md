# 🌊 Global Liquidity Flow Monitor

A specialized real-time analytics system to track **CME Margin Requirements** and correlate them with **Global Macro Liquidity (Yields/FX)** to identify "Induced Shakeouts".

## 🏗 System Architecture

### 1. Data Ingestion Layer (`src/agents/`)
The system focuses on core CME instruments and global macro benchmarks:
*   **CME (US Commodities & Indices)**:
    *   **Metals**: SILVER (SI), GOLD (GC), COPPER (HG).
    *   **Equities**: S&P 500 E-mini (ES), Nasdaq 100 E-mini (NQ).
    *   **Method**: `Selenium` for daily margin capture and `yfinance` for high-fidelity historical price/margin backfilling.
*   **FRED (Macro Flow)**:
    *   **Source**: Federal Reserve Economic Data (FRED).
    *   **Benchmarks**: US 10Y Yield (DGS10), Japan 10Y Yield (IRLTLT01JPM156N), USD/JPY (DEXJPUS).
    *   **Method**: API-based ingestion of benchmark rates to monitor the "Cost of Money".

### 2. Analytics Layer (`src/analytics.py`)
Proprietary logic for monitoring both instant shocks and sustained pressure:

*   **Pressure Index (Sustained Stress)**
    *   **Goal**: Measure the cumulative load on a position relative to a 30-day baseline.
    *   **Formula**: `Index = (Current Margin %) / (Rolling 30-Day Min Margin %)`
    *   **Thresholds**:
        *   **1.0x**: Baseline (Normal).
        *   **1.5x**: Stress Threshold (Significant squeeze risk).

*   **Shock Score (Instant Event)**
    *   **Goal**: Detect sudden 24h margin hikes used to "shake out" over-leveraged players.
    *   **Formula**: `Score = (Current Margin - Prev Margin) / Prev Margin`

*   **Systemic Alert Engine**
    *   Triggers when **3+ concurrent instruments** show margin spikes, signaling an exchange-level liquidity event.

### 3. Dashboard Layer (`src/dashboard/app.py`)
Built with **Streamlit** and **Plotly**.
*   **Macro Traffic Light**: Dynamic status banner interpreting Yen Carry Trade health and US Treasury pressure.
*   **Yield Spread Monitor**: Real-time gauge of the US-JP Yield Spread (the primary carry trade incentive).
*   **Global Stress Monitor**: Multi-line comparison of the Pressure Index across all assets.
*   **Correlation Matrix**: Pearson correlation of margin changes to identify systemic lockstep moves.

## 🚀 Usage

### Initialize System
```bash
./run.sh
```
*Note: This script will verify dependencies, sync the database, run backfills, and launch the dashboard.*

### Manual Data Sync
```bash
# Fetch Latest CME Margins
python src/agents/global_margin_agent.py

# Fetch Macro Yields
python src/agents/flow_agent.py

# Backfill Historical Data (2025-Jan to present)
python src/agents/cme_historical_agent.py
```

### API Requirements
Ensure your environment variables are set:
*   `FRED_API_KEY`: Required for macro flow data.
