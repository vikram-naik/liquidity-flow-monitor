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
8-module pipeline in `src/divergence_engine/`:
1. `base_calc.py` — ATR20 (Wilder), RDV, MFM, TP, MFM_TP
1.5. `regime.py` — ADX/DMI market regime classification (uptrend/downtrend/notrend/transition)
2. `dvl_ledger.py` — DVL, DVL_rate, Velocity (norm), Price_distance, ARS, PDD per window [10,30,60,120]
3. `cwvap.py` — DVWAP_n, POC_n, CWVAP, CPOC, POC_spread, CVAH/CVAL, price_location
4. `cwc.py` — Cross-Window Coherence, CWC_delta, CWC_slope
5. `mcs.py` — MCS, MCS_MFM, MCS_composite, MCS_composite_slope
6. `analysis.py` — price_slope_z, rdv_slope_z, coherence_raw, coherence, psz_delta_3d/5d
7. `analysis_integrated.py` → delegates to `scoring/` package

### Unified Scoring Model (v2 — LIVE)
Continuous factor scoring replaces binary gates. See `handoff.md` for full details.
- `scoring/__init__.py` — `compute_signal_strength()`, regime-aware direction detection
- `scoring/functions.py` — registry: `higher_is_better`, `lower_is_better`, `abs_higher_is_better`, `directional`, `counter_directional`, `count_ratio`
- YAML v2: `settings` + `factors` (7 active: PSZ Delta 20%, RSZ Delta 10%, PSZ 15%, RSZ 10%, CWC 5%, Coherence 5%, Regime 5%)
- PDD **disabled** — negatively correlated with returns (r=-0.043), Q4 hit=37.6% Supply
- `requires` system: child score capped by parent. `{factor: X}` single parent, `{any: [X,Y]}` max of parents
- Tiered hierarchy: T1 deltas (psz_delta, rsz_delta) → T2 levels (PSZ, RSZ) → T3 confirmation (CWC, Coherence) → T4 context (Regime)
- Factor order in YAML matters — parents must appear before children
- Output: `signal_strength` (0-100), `scoring_details` (per-factor breakdown), `integrated_state`
- UI: progress bars + contributions in sidebar, auto-generated weight sliders in settings
- **Direction detection**: regime-based (uptrend→Demand, downtrend→Supply, transition/notrend→strict CWVAP)
- **Regime alignment factor**: scores 1.0 (trending), 0.5 (transition), 0.15 (notrend) — no hard gates
- `rule_engine.py` — legacy, no longer called (can be deleted)

## Key File Paths
- Engine orchestrator: `src/divergence_engine/engine.py`
- State types: `src/divergence_engine/state_types.py`
- Regime classifier: `src/divergence_engine/regime.py`
- Scoring package: `src/divergence_engine/scoring/` (__init__.py, functions.py)
- Analysis (delegates to scoring): `src/divergence_engine/analysis_integrated.py`
- Rule engine (legacy, unused): `src/divergence_engine/rule_engine.py`
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

### Key Data Findings (Run 4 — v2 tiered requires, 973K signals)
- Supply outperforms Demand: 52.1% vs 46.7% hit rate at 5d
- PDD harmful: r=-0.043, Q4=37.6% Supply hit → disabled
- `mcs_delta` strongest unused predictor: 5.4% Q1→Q4 spread, r=+0.089 Supply
- `mfm` useful for Demand: r=+0.071
- CWC/Coherence are weak discriminators when ungated; effective only as confirmation (Tier 3)

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
- **Add `mcs_delta` as scoring factor** — strongest unused predictor, see handoff.md for details
- **Add `mfm` as scoring factor** — Demand-specific (r=+0.071), lower priority
- **Run 5 signal quality report** — with PDD disabled + CWC/Coherence at 5%
- **Multi-thread signal quality report** — per-symbol runs are independent/IO-bound, use ThreadPoolExecutor
- **Tier-specific weights**: Different factor weights for Large/Mid/Small/Micro tiers
