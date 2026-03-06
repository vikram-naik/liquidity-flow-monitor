# Liquidity Flow Monitor — Project Memory

## Project Overview
NSE stock analysis tool with a rules-based Divergence Engine (current production) and an ML pipeline (paused, `feature/divergence-engin` branch). Detects institutional participation patterns to flag stocks with high probability of making a large price move (long/short) with massive delivery participation.

## Venv
Always use `venv/bin/python3` to run scripts. Never use the system Python.

## Database
- `liquidity_monitor.db` (SQLite) at project root — git-ignored, access via Python scripts using venv.
- **3,717,561 rows** in `nse_delivery_log`, **3,483 symbols**, dates **2019-01-01 to 2026-03-04**, **1,842 trading days**.
- Key tables: `nse_delivery_log`, `corporate_actions` (516 rows), `watchlists` (12), `watchlist_items` (1,203), `user_settings`, `symbol_anchors`.
- Active screener watchlists: `SCR: Long` (58 symbols), `SCR: Short` (20 symbols).
- `panel_builder.py` uses the FULL stock universe from `nse_delivery_log` (all symbols WHERE symbol NOT LIKE 'NIFTY%'), not the NIFTY 500 watchlist.

## Current Production Pipeline (rules-based engine)
7-module pipeline in `src/divergence_engine/`:
1. `base_calc.py` — ATR₂₀ (Wilder), RDV, MFM, TP, MFM_TP
2. `dvl_ledger.py` — DVL, DVL_rate, Velocity (norm), Price_distance, ARS, PDD per window [10,30,60,120]
3. `cwvap.py` — DVWAP_n, POC_n, CWVAP, CPOC, POC_spread, CVAH/CVAL, price_location
4. `cwc.py` — Cross-Window Coherence, CWC_delta, CWC_slope
5. `mcs.py` — MCS, MCS_MFM, MCS_composite, MCS_composite_slope
6. `analysis.py` — price_slope_z, rdv_slope_z, coherence_raw, coherence
7. `analysis_integrated.py` — 4-pillar Integrated State Matrix → 11 states

## ML Pipeline Status — PAUSED
XGBoost approach hit fundamental issues. See `handoff.md` for full details.

**Key findings:**
- **Direction mode leakage:** `cwvap_dist` sign = zone = label. Model achieves 100% accuracy by learning zone, not institutional patterns. Excluding zone features doesn't help — 15+ features encode signed distance-from-value.
- **Followthrough mode failure:** Model can't separate FOLLOW_THROUGH from TIMEOUT beyond base rate (11-12% precision = random). Mean predicted probability nearly identical for both classes.
- **What works:** Conviction gates (rdv≥0.6, rdv_cons≥2, cwc≤0.65) achieve 83% precision at 60% recall on 65 validated examples — better than any ML model trained.

## Conviction Gates (discovered from 65 validated examples)
Best discriminators between true and false setups:
- **CWC ≤ 0.65** — strongest gate. Low CWC = delivery windows disagreeing = setup building. High CWC = consensus = already priced in.
- **RDV ≥ 0.6** — lowered from 1.0 to include large-caps (RELIANCE, BAJFINANCE, LT) with deeper liquidity.
- **RDV consistency ≥ 2** — at least 2 of last 5 days with above-avg delivery.
- Combined: 83% precision, 60% recall, 80% false rejection rate.
- Validated on 7 stocks: GESHIP, NESCO, AAVAS, RELIANCE, BAJFINANCE, COALINDIA, LT.
- Examples stored in `data/price_action_examples.dat`.

## Next Step: Conviction Scorer
Build rules-based scorer (not ML) to rank gated setups by strength. Proposed scoring features: cwc (inverted), rdv, rdv_consistency, cwvap_dist depth, price_slope_z, pdd_30, delivery_pct.

## Label Generator Modes
- `--label-mode direction` — STRONG_UP/DOWN, timeouts dropped
- `--label-mode followthrough` — FOLLOW_THROUGH/TIMEOUT, timeouts kept
- New conviction gates: `--min-rdv-consistency`, `--max-cwc`
- `barrier_direction` column added for debugging

## Key File Paths
- Engine orchestrator: `src/divergence_engine/engine.py`
- Feature engineer: `src/feature_engineer.py`
- Panel builder: `scripts/panel_builder.py`
- Label generator: `scripts/label_generator.py`
- Train baseline: `scripts/train_baseline.py`
- Verify labels: `scripts/verify_labels.py`
- Inspect panel: `scripts/inspect_panel.py`
- Screener: `scripts/run_screener.py` (COHERENCE_THRESHOLD=0.70)
- Rules config: `src/divergence_engine/config/default_rules.yaml`
- API: `src/api/main.py` (FastAPI)
- DB helper: `src/database.py` (DB_PATH env var, default `liquidity_monitor.db`)
- Handoff doc: `handoff.md` (full pipeline status and findings)

## Data Ingestion
- `src/agents/nse_agent.py` — NSE Bhavcopy download + smart_sync
- `scripts/sync_nse_ca.py` — corporate action sync from NSE API
- `scripts/sync_ca.py` — yfinance-based split sync

## Corporate Action Adjustment
Backward-adjusted: OHLC / ratio_factor, Volume×ratio_factor for rows before ex_date.

## Dead Features
Zero importance across all training experiments: `dvl_rate_10/30/60/120`, `cwvap_lag_3d`.
