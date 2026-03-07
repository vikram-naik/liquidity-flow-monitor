# Handoff — Liquidity Flow Monitor

## Current Status
The 11-state Integrated State Matrix has been **replaced** with a conviction-gated
2-marker system (Demand / Supply). The conviction scorer is **live in production** —
integrated into the engine, API, UI, and screener. XGBoost ML approach remains paused.

**Current focus:** Fine-tuning Demand/Supply marker thresholds and verification.

---

## What's Live Now

### Conviction-Gated Demand/Supply Markers
Replaced the 11-state matrix with 2 markers + conviction score:

- **Demand** — price below CWVAP + all 3 gates pass → green arrow below bar
- **Supply** — price above CWVAP + all 3 gates pass → red arrow above bar
- **Conviction score** (0-100) — weighted composite of gate inputs

**Conviction gates (all must pass):**
| Gate | Condition | Default |
|------|-----------|---------|
| CWC Gate | CWC ≤ threshold | 0.65 |
| RDV Gate | RDV ≥ threshold | 0.6 |
| RDV Consistency | ≥ N days in last 5 with RDV ≥ 1.0 | 2 |

**Conviction score weights:**
| Component | Weight | Calculation |
|-----------|--------|-------------|
| CWC | 25% | max(0, 1 - cwc) |
| RDV | 20% | min(rdv / 2.0, 1.0) |
| RDV Consistency | 15% | rdv_consistency / 5 |
| CWVAP Depth | 15% | min(|cwvap_dist| / 5.0, 1.0) |
| PDD | 15% | min(|pdd_30| / 20.0, 1.0) |
| Delivery % | 10% | min(delivery_pct / 80.0, 1.0) |

**Gate thresholds and score weights are user-tunable** via the UI settings panel.
Rules in `default_rules.yaml` use `$threshold` references so they automatically
respect user overrides stored in the database.

### Verification System
`compute_verification()` in `analysis_integrated.py`:
- Scans all Demand/Supply signals in the ledger
- Checks if price hit the target (entry ± ATR_mult × ATR) within horizon
- Returns per-signal results (hit/miss/pending) + summary stats (hit rate %)
- API endpoint: `GET /de/api/verification/{symbol}`
- UI: verification panel in sidebar with hit rate badge + signal log table

### Screener
`scripts/run_screener.py` — scans NIFTY 500, populates `SCR: Demand` and `SCR: Supply`
watchlists. Filters by `--min-conviction` (default 50). Replaces old coherence-based filter.

### UI Changes
- 2-item marker legend (Demand green, Supply red) replaces 11-item legend
- Sidebar shows: state + conviction score, CWC, RDV, RDV Consistency, CWVAP Dist, Delivery %, Coherence
- Settings: Conviction Gates group + Verification group (5 sliders)
- Verification panel below engine state in sidebar
- Marker tooltips show "Demand (72)" with conviction score

### Deleted Code
- `src/analysis/markers/` — MarkerRegistry (no longer imported)
- `src/analysis/` — empty after markers removal
- Old state types: `Trajectory`, `ValueZone` enums removed from `state_types.py`
- Old predicates: `_TrajectoryIs`, `_ZoneIn` removed from `rule_engine.py`
- Old analysis: `_calc_angle()`, trajectory/zone classification removed from `analysis_integrated.py`

---

## Files Changed (Demand/Supply Migration)

| File | Change |
|------|--------|
| `src/divergence_engine/state_types.py` | 3-value StateName, new MarketContext |
| `src/divergence_engine/config/default_rules.yaml` | 2 rules + gate/score/verification thresholds |
| `src/divergence_engine/rule_engine.py` | Generalized _Range-only predicates |
| `src/divergence_engine/analysis_integrated.py` | rdv_consistency, conviction score, verification |
| `src/divergence_engine/engine.py` | Updated EngineResult.latest fields |
| `src/divergence_engine/chart.py` | Updated UI_COLUMNS |
| `src/api/main.py` | Removed MarkerRegistry, added verification endpoint |
| `src/web/js/divergence_engine.js` | 2-marker system, new settings, verification panel |
| `src/web/divergence_engine.html` | Simplified legend, verification section |
| `src/web/css/divergence_engine.css` | 2 marker classes, verification styles |
| `scripts/run_screener.py` | Demand/Supply + conviction threshold |
| `src/web/help_guide.html` | Rewritten for new system |

---

## XGBoost Investigation — Key Findings (PAUSED)

### Target Leakage in Direction Mode
`cwvap_dist` sign = zone = label. Model achieves 100% by learning zone, not patterns.
~15 features encode signed distance-from-value, all correlate with zone → label.

### Followthrough Mode — Model Can't Separate Classes
FOLLOW_THROUGH precision stayed at 11-12% = base rate across all thresholds.
Mean predicted probability virtually identical for both classes.

### What Works: Conviction Gates
Simple gates (rdv≥0.6, rdv_cons≥2, cwc≤0.65) achieve **83% precision at 60% recall**
on 65 validated examples — outperforming any ML model trained.

---

## Conviction Gates — The Core Discovery

### Feature Analysis (65 validated examples across 7 stocks)

| Feature | Effect size | True median | False median | Meaning |
|---|---|---|---|---|
| `price_slope_z` | 1.16 | -0.16 | +0.13 | True setups are mean-reverting |
| `mfm_slope_3d` | 0.66 | -0.15 | +0.03 | Money flow declining (about to turn) |
| `price_distance_30` | 0.54 | 0.49 | 2.18 | True setups are closer to value |
| `cwc` | 0.50 | 0.40 | 0.54 | Low CWC = windows disagreeing = building |
| `mcs_composite` | 0.47 | -0.01 | +0.09 | Near-zero MCS = not yet aligned |
| `delivery_pct` | 0.46 | 57.7% | 52.4% | Higher delivery % in true setups |
| `cwvap_dist` | 0.45 | -0.47 | +1.43 | True setups are closer to CWVAP |

**Key insight:** true setups are *building* (low CWC, negative price_slope_z, declining MFM)
but haven't completed. False setups already have consensus (high CWC) — the move is priced in.

Validated on: GESHIP, NESCO, AAVAS, RELIANCE, BAJFINANCE, COALINDIA, LT.
Examples stored in `data/price_action_examples.dat`.

---

## ML Pipeline Scripts (reference, paused)

| File | Purpose |
|------|---------|
| `scripts/panel_builder.py` | Builds `data/panel.parquet` (~2.1M rows, 109 cols) |
| `src/feature_engineer.py` | Computes ~26 lookback feature columns |
| `scripts/label_generator.py` | Zone-aware Triple Barrier labeling |
| `scripts/verify_labels.py` | Label integrity checks |
| `scripts/inspect_panel.py` | Feature value inspector |
| `scripts/train_baseline.py` | XGBoost training |

---

## Next Steps: Fine-Tuning Demand/Supply Markers

1. **Gate threshold tuning** — run verification across broader universe, optimize gates for hit rate
2. **Score weight calibration** — test whether current weights maximize separation
3. **Expand validation set** — beyond 7 stocks, test on full NIFTY 500 verification stats
4. **Signal clustering** — investigate whether consecutive signals should be collapsed
5. **Additional gate candidates** — consider price_slope_z, mfm_slope_3d as supplementary gates

---

## Dev Notes
- All gate thresholds in `default_rules.yaml` use `$variable` references resolved from `thresholds` section
- `config_manager.py` is threshold-key agnostic — no changes needed for new keys
- Verification uses `atr_20` from `base_calc.py` — ensure ATR column is always present
- Dead features (zero ML importance): `dvl_rate_10/30/60/120`, `cwvap_lag_3d`
