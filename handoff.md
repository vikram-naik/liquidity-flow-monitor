# Handoff — Liquidity Flow Monitor

## Current Status
**Clean slate rebuild** — stripped all signal generation, scoring, CEI, backtesting,
and screener code on 2026-03-15. Only the core computation pipeline and UI remain.

## What's Live

### Core Engine Pipeline (6 modules)
The engine computes delivery-weighted indicators across rolling windows [10, 30, 60, 120]:

1. `base_calc.py` — ATR20 (Wilder), RDV, MFM, TP, MFM_TP
2. `regime.py` — ADX/DMI market regime classification (uptrend/downtrend/notrend/transition)
3. `dvl_ledger.py` — DVL, DVL_rate, Velocity (norm), Price_distance, ARS, PDD per window
4. `cwvap.py` — DVWAP, POC, CWVAP, CPOC, POC_spread, VA boundaries, price_location
5. `cwc.py` — Cross-Window Coherence, CWC_delta, CWC_slope
6. `mcs.py` — MCS, MCS_MFM, MCS_composite, MCS_composite_slope
7. `analysis.py` — price_slope_z, rdv_slope_z, coherence, accum_div, distrib_div

### UI
- OHLC chart with CWVAP/CPOC overlays and VA trendlines
- Configurable sub-panels (slopes, coherence, RDV, CWC, etc.)
- Sidebar with engine state metrics + watchlist management
- Symbol search

### API
- `/de/api/divergence-engine/{symbol}` — engine data endpoint
- `/de/dashboard/{symbol}` — chart UI
- `/de/api/watchlists/*` — watchlist CRUD + index import
- `/de/api/analysis/stocks` — symbol listing
- `/de/help` — help guide

### Data Ingestion
- `src/agents/nse_agent.py` — NSE Bhavcopy download + smart_sync
- `src/agents/nse_indices_agent.py` — NSE indices sync
- `scripts/sync_ca.py` — yfinance corporate action sync
- `scripts/sync_nse_ca.py` — NSE API corporate action sync

### Infrastructure
- Docker: `Dockerfile` + `docker-compose.yml`
- Build/deploy: `scripts/build_lfm.sh`, `scripts/deploy_lfm.sh`, `scripts/stop_lfm.sh`
- Cache: Redis-based (`src/cache/`)

## What Was Removed (2026-03-15)
- Scoring system (signal_strength, demand/supply direction detection, factor weights)
- CEI module (cumulative evidence index, markers, assisters)
- Backtester (`backtest_cei.py`)
- Signal quality report + UI
- Screener (`run_screener.py`) + production pipeline (`prod_run.sh`)
- Settings UI (factor weight sliders)
- All ML/XGBoost experiments
- Config manager (scoring config)
- YAML config file (settings now hardcoded in engine.py)
- PSZ/RSZ/MCS delta columns (multi-period inflection tracking)

## Key File Paths
- Engine orchestrator: `src/divergence_engine/engine.py`
- Config: hardcoded in engine.py (VA_PCT=0.70)
- Chart data: `src/divergence_engine/chart.py`
- API: `src/api/main.py`
- DB helper: `src/database.py`
- Cache: `src/cache/`

## Next
- Rebuild signal generation from scratch
