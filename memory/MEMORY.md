# Liquidity Flow Monitor — Project Memory

## Project Overview
NSE stock analysis tool with a Divergence Engine. Computes delivery-weighted indicators
across rolling windows [10, 30, 60, 120] for institutional participation analysis.

## Venv
Always use `venv/bin/python3` to run scripts. Never use the system Python.

## Database
- `liquidity_monitor.db` (SQLite) at project root — git-ignored, access via Python scripts using venv.
- **3,717,561 rows** in `nse_delivery_log`, **3,483 symbols**, dates **2019-01-01 to 2026-03-04**, **1,842 trading days**.
- Key tables: `nse_delivery_log`, `corporate_actions`, `watchlists`, `watchlist_items`, `user_settings`, `symbol_anchors`, `nse_trading_holidays`.

## Current Pipeline
6-module pipeline in `src/divergence_engine/`:
1. `base_calc.py` — ATR20 (Wilder), RDV, MFM, TP, MFM_TP
1.5. `regime.py` — ADX/DMI market regime classification (uptrend/downtrend/notrend/transition)
2. `dvl_ledger.py` — DVL, DVL_rate, Velocity (norm), Price_distance, ARS, PDD per window [10,30,60,120]
3. `cwvap.py` — DVWAP, POC, CWVAP, CPOC, POC_spread, va_high/va_low (delivery-profile VA), price_location
4. `cwc.py` — Cross-Window Coherence, CWC_delta, CWC_slope
5. `mcs.py` — MCS, MCS_MFM, MCS_composite, MCS_composite_slope
6. `analysis.py` — price_slope_z, rdv_slope_z, coherence_raw, coherence, accum_div, distrib_div

## Key File Paths
- Engine orchestrator: `src/divergence_engine/engine.py`
- Chart data: `src/divergence_engine/chart.py`
- API: `src/api/main.py` (FastAPI)
- DB helper: `src/database.py` (DB_PATH env var, default `liquidity_monitor.db`)
- Cache: `src/cache/` (Redis — interface.py, redis_provider.py, factory.py)
- Handoff doc: `handoff.md`

## Data Ingestion
- `src/agents/nse_agent.py` — NSE Bhavcopy download + smart_sync + holiday management
- `scripts/sync_nse_ca.py` — corporate action sync from NSE API
- `scripts/sync_ca.py` — yfinance-based split sync

## Cache Layer
- Redis-based: `src/cache/` with `CacheInterface` abstraction
- `get()`, `set(key, value, ttl)`, `delete(key)`, `clear()`, `delete_pattern(glob)`
- Cache key prefix: `de:` (engine results)

## Dev Preferences
- **Always use latest versions** of frameworks/libs. Verify actual version availability via CDN/registry before using.
