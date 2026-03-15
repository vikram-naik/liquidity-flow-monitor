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
9-module pipeline in `src/divergence_engine/`:
1. `base_calc.py` — ATR20 (Wilder), RDV, MFM, TP, MFM_TP
1.5. `regime.py` — ADX/DMI market regime classification (uptrend/downtrend/notrend/transition)
2. `dvl_ledger.py` — DVL, DVL_rate, Velocity (norm), Price_distance, ARS, PDD per window [10,30,60,120]
3. `cwvap.py` — DVWAP_n, POC_n, va_high_n/va_low_n, CWVAP, CPOC, POC_spread, va_high/va_low (delivery-profile VA), CVAH/CVAL (Bollinger-style, kept for location classifier), price_location
4. `cwc.py` — Cross-Window Coherence, CWC_delta, CWC_slope
5. `mcs.py` — MCS, MCS_MFM, MCS_composite, MCS_composite_slope
6. `analysis.py` — price_slope_z, rdv_slope_z, coherence_raw, coherence, psz_delta_3d/5d, accum_div, distrib_div
7. `analysis_integrated.py` → delegates to `scoring/` package
7.5. `cei.py` — Cumulative Evidence Index (CEI): rolling net-directional-evidence score, EMA-smoothed

### Unified Scoring Model (v2.2 — LIVE)
Continuous factor scoring replaces binary gates. See `handoff.md` for full details.
- `scoring/__init__.py` — `compute_signal_strength()`, regime-aware direction detection, convergence multiplier
- `scoring/functions.py` — registry: `higher_is_better`, `lower_is_better`, `abs_higher_is_better`, `directional`, `counter_directional`, `count_ratio`, `categorical_map` — all numeric fns support `exponent` param
- YAML v2.1: `settings` + `factors` (15 active, weights sum to 100%: 30% Price Momentum, 30% Volume Momentum, 20% Alignment, 10% Confirmation, 10% Macro Context)
- PDD **disabled** — negatively correlated with returns.
- Delta variants (2d, 4d, 9d) process multi-horizon expansions for PSZ, RSZ, and MCS Δ.
- CDVL (Composite Delivery Velocity) added as a continuous macro volume baseline float.
- `delivery_phase` (categorical mapping of `gradient_shape`) added as an institutional volume context layer.
- `requires` system: child score capped by parent. `{factor: X}` single parent, `{any: [X,Y]}` max of parents
- Output: `signal_strength` (0-100), `scoring_details`, `integrated_state`, `demand_details`, `supply_details`, `demand_strength`, `supply_strength`
- UI: progress bars + contributions, auto-generated settings sliders, expandable sidebar for side-by-side Demand vs Supply comparison. "Delta" renamed mathematically to "Δ" to save space.
- **Direction detection**: regime-based (uptrend→Demand, downtrend→Supply, transition/notrend→strict CWVAP)
- **Regime alignment factor**: scores 1.0 (trending), 0.5 (transition), 0.15 (notrend) — no hard gates
- **v2.2 Intensity scoring**: Delta factors `exponent: 1.5`, level factors `exponent: 1.3`. Score = `linear ^ exponent`
- **Convergence multiplier**: counts delta families (PSZ/RSZ/MCS) firing > 0.3 threshold. 0→0.85×, 1→1.0×, 2→1.10×, 3→1.20×
- `rule_engine.py` — legacy, no longer called (can be deleted)

## Key File Paths
- Engine orchestrator: `src/divergence_engine/engine.py`
- State types: `src/divergence_engine/state_types.py`
- Regime classifier: `src/divergence_engine/regime.py`
- Scoring package: `src/divergence_engine/scoring/` (__init__.py, functions.py)
- Analysis (delegates to scoring): `src/divergence_engine/analysis_integrated.py`
- CEI module: `src/divergence_engine/cei.py`
- CEI design doc: `CEI_PLAN.md`
- Rule engine (legacy, unused): `src/divergence_engine/rule_engine.py`
- Rules config: `src/divergence_engine/config/default_rules.yaml`
- Chart data: `src/divergence_engine/chart.py`
- API: `src/api/main.py` (FastAPI)
- Screener: `scripts/run_screener.py`
- Signal quality: `scripts/signal_quality_report.py`
- Signal quality UI: `src/web/signal_quality.html`, `src/web/js/signal_quality.js`
- Backtester: `scripts/backtest_cei.py` — CEI signal backtester (long only)
- Backtest handoff: `backtest_handoff.md`
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

## Production Pipeline (EOD)
- `scripts/prod_run.sh` — runs in Docker: CA sync → NSE sync → NSE indices sync → Screener
- Screener uses CEI signals from latest bar → `SCR: Long` / `SCR: Short` watchlists

## Data Ingestion
- `src/agents/nse_agent.py` — NSE Bhavcopy download + smart_sync + holiday management
- `scripts/sync_nse_ca.py` — corporate action sync from NSE API
- `scripts/sync_ca.py` — yfinance-based split sync

## Dev Preferences
- **Always use latest versions** of frameworks/libs. Verify actual version availability via CDN/registry before using.

## CEI Status (end of day 2026-03-15)
- **Phase 1** ✓ — Feature engineering + CEI computation
- **Phase 2** ✓ — Chart panel visualization
- **Phase 2.5** ✓ — Position score tuning (CWVAP/CPOC awareness + CDVL rebalance)
- **Phase 3** ✓ — CEI markers + screener (COMPLETE)
- **Phase 3.5** ✓ — CEI signal fine-tuning + marker redesign (COMPLETE)
  - ✓ Signal trigger: **CEI zero-crossings** (was slope), 5-bar cooldown
  - ✓ Supply CWVAP gate: Supply only fires when `close < cwvap`
  - ✓ max_value recalibrated to P90 of Nifty 50 empirical distribution
  - ✓ Weight rebalance: PSZ 25%→15%, MCS 20%→30%
  - ✓ Cumulative divergence: rolling 20-bar sum replaces single-bar divergence
  - ✓ RSZ–PSZ per-window confirmation: RSZ dampened 0.2× when PSZ sign disagrees
  - ✓ Delivery-Profile VA boundaries (`va_high`/`va_low`): TPO-style 70% delivery volume area
  - ✓ VA Spring Energy: marker modifier (Design A) — bypasses EMA on breakout bars
  - ✓ Position score reduced 10%→5%
  - ✓ VA trendlines on OHLC chart (purple dashed lines, `cbVA` toggle)
  - ✓ `cei_raw` plotted on CEI panel (orange line), histogram switched to use `cei_raw` data
  - ✓ **Assister markers: `cei_raw` crosses `cei` (EMA)** — validated on Nifty 500
    (daily+weekly). Not viable as primary replacement (no hit rate improvement).
    Implemented as assister overlay (circle "A" markers, leads by 1-3 bars).
  - ✓ **CEI noise reduction (5-layer fix)**: 2d→9d weight shift, structure floors 0.3,
    EMA span 14, cumul_div 20%, `min_crossing_gap: 0.005`. Primary signals -64%,
    RELIANCE PF 2.03→3.63, win rate 33%→60%.
  - ○ Intensity calc in cei.py (parked)
- **Phase 4** — Future: Trend-riding state machine (entry vs continuation markers)

## CEI Backtester (2026-03-15)
- **File**: `scripts/backtest_cei.py` — long-only, walk-forward CEI signal backtester
- **Handoff**: `backtest_handoff.md` — full architecture, decisions, test results
- **Watchlist Support**: `--watchlist` flag for batch runs; tabular summaries + aggregate P&L.
- **Entry rules**: Demand anywhere + Assister only below CWVAP + optional `--exclude-va` filter.
- **Exit**: CWVAP trailing stop (always active) + CEI threshold exit (`|CEI| >= 0.08`, default).
- **Exit CLI**: `--cei-exit N` (default 0.08, 0=disabled), `--min-hold N` (default 0), `--va-primary` (opt-in).
- **Execution**: EOD limit order at signal-day close, fill check on next bar OHLC, `--chase` fills at open if limit misses.
- **Output**: Unified daily log + CSV exports in root `data/` folder.
- **NIFTY 50 result (2024-01-01 to 2026-03-13, --chase)**: **+443,188 P&L** (4× baseline), CEI threshold 0.08.
- **Test scripts**: `scripts/test_exit_filters.py`, `scripts/test_dual_ema_assister.py`.

## Key Findings
- [Supply Signal Dynamics](project_supply_dynamics.md) — root cause of Supply underperformance + current asymmetric exit rules

## Backlog
- **Run 5 signal quality report** — with noise-reduced CEI config (high priority)
- **Backtester: Multi-symbol batch run** — loop over NIFTY 500, aggregate statistics post noise reduction
- **Backtester: Parameter sensitivity** — sweep cei_exit_threshold (0.05-0.12), stop_cwvap_pct (-1% to -5%)
- **Backtester: Risk metrics** — Sharpe, Calmar, monthly returns
- **Intensity calculation in cei.py** — move from JS/screener into cei.py module
- **Trend-riding state machine** — Entry vs continuation markers. GLENMARK example.
- **Weekly Demand confirmation layer** — weekly CEI Demand hits 55%+ (vs 50% daily). Potential Phase 4 feature
- **Multi-thread signal quality report** — ThreadPoolExecutor
- **Tier-specific weights**: Different factor weights for Large/Mid/Small/Micro tiers
