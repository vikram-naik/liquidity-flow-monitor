# Liquidity Flow Monitor — Project Memory

## Project Overview
NSE stock analysis tool with a Divergence Engine. Detects institutional participation patterns using Demand/Supply markers to flag stocks with high probability of making a large price move with massive delivery participation.

## Venv
Always use `venv/bin/python3` to run scripts. Never use the system Python.

## Database
- `liquidity_monitor.db` (SQLite) at project root — git-ignored, access via Python scripts using venv.
- **3,717,561 rows** in `nse_delivery_log`, **3,483 symbols**, dates **2019-01-01 to 2026-03-04**, **1,842 trading days**.
- Key tables: `nse_delivery_log`, `corporate_actions` (516 rows), `watchlists` (12), `watchlist_items` (1,203), `user_settings`, `symbol_anchors`, `nse_trading_holidays`, `signal_quality`.

## Current Production Pipeline
7-module pipeline in `src/divergence_engine/`:
1. `base_calc.py` — ATR20 (Wilder), RDV, MFM, TP, MFM_TP
2. `dvl_ledger.py` — DVL, DVL_rate, Velocity (norm), Price_distance, ARS, PDD per window [10,30,60,120]
3. `cwvap.py` — DVWAP_n, POC_n, CWVAP, CPOC, POC_spread, CVAH/CVAL, price_location
4. `cwc.py` — Cross-Window Coherence, CWC_delta, CWC_slope
5. `mcs.py` — MCS, MCS_MFM, MCS_composite, MCS_composite_slope
6. `analysis.py` — price_slope_z, rdv_slope_z, coherence_raw, coherence, psz_delta_3d/5d
7. `analysis_integrated.py` — Binary gates + dual conviction scoring (BEING REPLACED)

### Current Marker System (to be replaced by unified scoring)
Binary gates + dual conviction scoring. See `handoff.md` for full details.
- `classify_with_gates()` in rule_engine.py provides per-gate pass/fail → `gate_results` column
- Gate diagnostics rendered in sidebar UI (backend-driven, no JS business logic)

### Unified Scoring Rearchitecture (NEXT)
Replacing binary gates + conviction score with a single weighted scoring model.
Full architecture plan in `handoff.md` under "Unified Weighted Scoring Model".
Key concepts: scoring function registry, YAML-driven factors, ATR tolerance band for CWVAP direction, config-driven UI, `scoring/` package.

## Key File Paths
- Engine orchestrator: `src/divergence_engine/engine.py`
- State types: `src/divergence_engine/state_types.py`
- Rule engine: `src/divergence_engine/rule_engine.py` (to be replaced)
- Analysis (markers): `src/divergence_engine/analysis_integrated.py` (to be replaced)
- Rules config: `src/divergence_engine/config/default_rules.yaml`
- Chart data: `src/divergence_engine/chart.py`
- API: `src/api/main.py` (FastAPI)
- Screener: `scripts/run_screener.py`
- Signal quality: `scripts/signal_quality_report.py`
- Signal quality UI: `src/web/signal_quality.html`, `src/web/js/signal_quality.js`
- DB helper: `src/database.py` (DB_PATH env var, default `liquidity_monitor.db`)
- Cache: `src/cache/` (Redis — interface.py, redis_provider.py, factory.py)
- Handoff doc: `handoff.md`

## Signal Quality Analysis
- Script: `scripts/signal_quality_report.py` — runs engine on all symbols with >= 1yr data
- Computes 3/5/10 day forward returns, MFE, MAE, hit/miss per signal
- Returns are **directional**: positive = signal was correct (Supply returns flipped)
- Signals without complete forward data for all 3 horizons (3/5/10d) are excluded
- Volume tiers: Large (>=50cr), Mid (>=10cr), Small (>=2cr), Micro (<2cr)
- Re-run after any gate/scoring change
- Flush `de:*` Redis cache after changes

### Key Data Findings
- **Coherence is strongest discriminator** in large-caps: Q4=53.6% vs Q1=48.1% hit rate
- **Accum score correlates with returns**: Q4: +0.65% vs Q1: +0.35%
- **Conviction score doesn't discriminate**: all quartiles hover 48-50% — motivates unified scoring
- **Best segment: Large-cap Demand, high accum + high coherence: 58.1% hit at 5d (n=544)**

## Cache Layer
- Redis-based: `src/cache/` with `CacheInterface` abstraction
- `get()`, `set(key, value, ttl)`, `delete(key)`, `clear()`, `delete_pattern(glob)`
- Cache key prefixes: `de:` (engine results), `sq:` (signal quality API)

## Data Ingestion
- `src/agents/nse_agent.py` — NSE Bhavcopy download + smart_sync + holiday management
- `scripts/sync_nse_ca.py` — corporate action sync from NSE API
- `scripts/sync_ca.py` — yfinance-based split sync

## Dev Preferences
- **Always use latest versions** of frameworks/libs. Verify actual version availability via CDN/registry before using.

## Backlog
- **Multi-thread signal quality report** — per-symbol runs are independent/IO-bound, use ThreadPoolExecutor
- **Signal clustering**: Investigate whether consecutive signals should be collapsed
- **Tier-specific weights**: Different factor weights for Large/Mid/Small/Micro tiers (post unified scoring)
