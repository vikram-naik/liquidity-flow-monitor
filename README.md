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

### 4. Resilience & Data Quality
*   **UTC Standardization**: All timestamps are normalized to UTC for global consistency.
*   **Weekend Guard**: Automatic skipping of weekend/holiday extraction to prevent "zero data" pollution.
*   **Per-Instrument Tracking**: Independent sync tracking for each instrument (GOLD, SILVER, etc.) ensures that a failure in one feed doesn't block others.
*   **Zero-Value Scrubbing**: Guards against invalid 0.0 margin/price insertions from external feeds.

## 🛠 Scripts Reference

The system includes a suite of helper scripts in the `scripts/` directory for lifecycle management.

### Local Development
*   **`./scripts/sync_data_local.sh`**: The daily workhorse. Runs all agents (Flow -> Margin -> Historical) sequentially.
    ```bash
    ./scripts/sync_data_local.sh
    ```
*   **`./scripts/clean_restart_local.sh`**: **DESTRUCTIVE**. Wipes the local database (`liquidity_monitor.db`) and re-initializes it from scratch. Use this if your local data is corrupted.
    ```bash
    ./scripts/clean_restart_local.sh
    ```

### Production Publishing
*   **`./scripts/publish_lfm.sh`**: Pushes your clean local data to the remote Production API.
    *   **Normal Push** (New data only):
        ```bash
        ./scripts/publish_lfm.sh YOUR_PASSWORD
        ```
    *   **Full Backfill** (Overwrite remote DB with local history):
        ```bash
        ./scripts/publish_lfm.sh YOUR_PASSWORD --backfill
        ```

### Docker Management (AWS/Prod)
*   **`./scripts/build_lfm.sh`**: Builds the Docker image locally and pushes it to Docker Hub (`vjdev/lfm-app`).
    ```bash
    ./scripts/build_lfm.sh
    ```
*   **`./scripts/deploy_lfm.sh`**: Pulls the latest image and starts the stack using `docker-compose`.
    ```bash
    ./scripts/deploy_lfm.sh
    ```
*   **`./scripts/stop_lfm.sh`**: Gracefully stops and removes the running containers.
    ```bash
    ./scripts/stop_lfm.sh
    ```

### Yield Data Backfill
The `backfill_yields.py` script provides on-demand backfill from multiple sources:

*   **FRED (Primary Source)**: Backfill all yield series from FRED API.
    ```bash
    python3 scripts/backfill_yields.py --source=fred --days=30
    ```

*   **YFinance (Fallback)**: Backfill VIX and US 10Y when FRED lags.
    ```bash
    python3 scripts/backfill_yields.py --source=yfinance --series=VIX,US10Y --days=7
    ```

*   **NY Fed API (RRP)**: Backfill Reverse Repo data from NY Fed.
    ```bash
    python3 scripts/backfill_yields.py --source=nyfed --days=30
    ```

*   **MOF Japan (JPY 10Y)**: Backfill Japan 10Y yield with daily data (vs FRED monthly).
    ```bash
    python3 scripts/backfill_yields.py --source=mof
    ```

## 🚀 Quick Start
### Initialize System (Local)
```bash
./run.sh
```
*Note: This wrapper script effectively calls `sync_data_local.sh` and then launches the Dashboard.*

### API Requirements
Ensure your environment variables are set in `.env` or exported:
*   `FRED_API_KEY`: Required for macro flow data.

