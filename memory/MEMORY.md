# Liquidity Flow Monitor — Project Memory

## Project Overview
NSE stock analysis tool with a rules-based Divergence Engine. Detects institutional participation patterns using conviction-gated Demand/Supply markers to flag stocks with high probability of making a large price move with massive delivery participation.

## Venv
Always use `venv/bin/python3` to run scripts. Never use the system Python.

## Database
- `liquidity_monitor.db` (SQLite) at project root — git-ignored, access via Python scripts using venv.
- **3,717,561 rows** in `nse_delivery_log`, **3,483 symbols**, dates **2019-01-01 to 2026-03-04**, **1,842 trading days**.
- Key tables: `nse_delivery_log`, `corporate_actions` (516 rows), `watchlists` (12), `watchlist_items` (1,203), `user_settings`, `symbol_anchors`.
- Screener watchlists: `SCR: Demand`, `SCR: Supply` (conviction-based).

## Current Production Pipeline
7-module pipeline in `src/divergence_engine/`:
1. `base_calc.py` — ATR₂₀ (Wilder), RDV, MFM, TP, MFM_TP
2. `dvl_ledger.py` — DVL, DVL_rate, Velocity (norm), Price_distance, ARS, PDD per window [10,30,60,120]
3. `cwvap.py` — DVWAP_n, POC_n, CWVAP, CPOC, POC_spread, CVAH/CVAL, price_location
4. `cwc.py` — Cross-Window Coherence, CWC_delta, CWC_slope
5. `mcs.py` — MCS, MCS_MFM, MCS_composite, MCS_composite_slope
6. `analysis.py` — price_slope_z, rdv_slope_z, coherence_raw, coherence
7. `analysis_integrated.py` — Conviction-gated Demand/Supply markers + conviction score

### Marker System (replaced 11-state matrix)
Two markers: **Demand** (price below CWVAP) and **Supply** (price above CWVAP).
Three conviction gates must all pass:
- **CWC ≤ 0.65** — low coherence = delivery windows disagreeing = setup building
- **RDV ≥ 0.6** — elevated relative delivery volume
- **RDV consistency ≥ 2** — at least 2 of last 5 days with RDV ≥ 1.0

Conviction score (0-100) weights: CWC 25%, RDV 20%, RDV consistency 15%, CWVAP depth 15%, PDD 15%, Delivery % 10%.

### Verification System
`compute_verification()` in `analysis_integrated.py` tracks signal accuracy:
- Demand hit: high reaches entry + ATR_mult × ATR within horizon
- Supply hit: low reaches entry - ATR_mult × ATR within horizon
- Configurable: `verification_horizon` (default 5), `verification_atr_mult` (default 2.0)
- API endpoint: `GET /de/api/verification/{symbol}`

### State Types
- `StateName`: DEMAND, SUPPLY, NO_SIGNAL (was 11 states)
- `MarketContext`: cwvap_dist, cwc, rdv, rdv_consistency, delivery_pct, pdd_30, coherence
- Rules in `default_rules.yaml` use `$threshold` references for user-tunable gates

## Conviction Gates (calibrated from 65 validated examples)
- Combined: 83% precision, 60% recall, 80% false rejection rate.
- Validated on 7 stocks: GESHIP, NESCO, AAVAS, RELIANCE, BAJFINANCE, COALINDIA, LT.
- Examples stored in `data/price_action_examples.dat`.

## ML Pipeline Status — PAUSED
XGBoost approach hit target leakage + inability to separate classes. See `handoff.md`.

## Key File Paths
- Engine orchestrator: `src/divergence_engine/engine.py`
- State types: `src/divergence_engine/state_types.py`
- Rule engine: `src/divergence_engine/rule_engine.py`
- Analysis (markers): `src/divergence_engine/analysis_integrated.py`
- Rules config: `src/divergence_engine/config/default_rules.yaml`
- Chart data: `src/divergence_engine/chart.py`
- API: `src/api/main.py` (FastAPI)
- Screener: `scripts/run_screener.py` (conviction threshold, Demand/Supply)
- DB helper: `src/database.py` (DB_PATH env var, default `liquidity_monitor.db`)
- Handoff doc: `handoff.md`
- ML scripts: `scripts/panel_builder.py`, `scripts/label_generator.py`, `scripts/train_baseline.py`

## Deleted Modules
- `src/analysis/markers/` — old MarkerRegistry, no longer imported anywhere
- `src/analysis/` — empty after markers removal

## Data Ingestion
- `src/agents/nse_agent.py` — NSE Bhavcopy download + smart_sync
- `scripts/sync_nse_ca.py` — corporate action sync from NSE API
- `scripts/sync_ca.py` — yfinance-based split sync

## Corporate Action Adjustment
Backward-adjusted: OHLC / ratio_factor, Volume×ratio_factor for rows before ex_date.

## UI Settings (configurable via gear icon)
- Conviction Gates: cwc_gate, rdv_gate, rdv_consistency_gate
- Verification: verification_horizon, verification_atr_mult
- Score weights: w_cwc, w_rdv, w_rdv_consistency, w_cwvap_depth, w_delivery_pct, w_pdd

## Next Steps: Fine-tuning Demand/Supply Markers
- Tune gate thresholds using verification hit rates across broader universe
- Evaluate conviction score weight calibration
- Consider adding time-of-day or seasonality filters
- Expand validated examples beyond 7 stocks
