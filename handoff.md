# Handoff — Liquidity Flow Monitor

## Status
Steps 1–4 complete. Label generator enhanced with conviction gates (CWC, RDV consistency).
XGBoost ML approach **paused** — target leakage investigation revealed that zone-based
direction labeling makes the classification task trivially solvable by zone features, and
followthrough labeling (predicting setup quality) produced a model that couldn't separate
classes beyond base rate.

**Recommended next step:** build a **rules-based conviction scorer** instead of an ML model.
The conviction gates already capture the institutional fingerprint; a scoring function that
ranks setups by strength (rdv, cwc, cwvap depth, pdd) is the natural next step.

---

## What Was Built

### Step 1: `scripts/panel_builder.py`
Pulls all symbols from `nse_delivery_log` DB, runs the divergence engine per symbol,
invokes `feature_engineer.py`, and writes `data/panel.parquet`.

CLI modes: `--mode full` (rebuild from scratch) | `--mode delta` (append new rows only).
Optional `--limit N` for dev/test runs. `--dry-run` for quality gate check only.

### Step 2: `src/feature_engineer.py`
Computes ~26 lookback feature columns per symbol on top of the ~84 engine columns.
Invoked internally by `panel_builder.py` — not called directly.

### Step 3: `scripts/label_generator.py`
Zone-aware Triple Barrier labeling on `data/panel.parquet` → `data/labeled_panel.parquet`.

**Two label modes:**
- `direction` — STRONG_UP / STRONG_DOWN (timeouts dropped)
- `followthrough` — FOLLOW_THROUGH / TIMEOUT (predict setup quality, timeouts kept as negative class)

**Core logic:**
1. **Zone classification** — each row classified as `demand` (close below CWVAP) or
   `supply` (close above CWVAP) using ATR-based distance band.
2. **Directional barrier** — only the reversal direction is checked per zone:
   - Demand zone → upper barrier only
   - Supply zone → lower barrier only
3. **Conviction gates** (new):
   - `--min-rdv 0.6` — minimum relative delivery volume
   - `--min-rdv-consistency 2` — minimum days in last 5 with above-avg delivery
   - `--max-cwc 0.65` — maximum cross-window coherence (low = windows disagreeing = setup building)
4. **Cooldown dedup** — suppresses overlapping labels within N rows per symbol.
5. Adds `barrier_direction` column (`up` / `down`) for debugging and inference.

**Recommended parameters:**
```bash
# Direction mode with conviction gates
venv/bin/python3 scripts/label_generator.py --label-mode direction \
    --horizon 5 --cooldown 5 \
    --min-rdv 0.6 --min-rdv-consistency 2 --max-cwc 0.65

# Followthrough mode with conviction gates
venv/bin/python3 scripts/label_generator.py --label-mode followthrough \
    --horizon 5 --cooldown 5 \
    --min-rdv 0.6 --min-rdv-consistency 2 --max-cwc 0.65
```

**Reference counts (direction mode, full gates):**
- 8,619 labeled rows across 1,160 symbols (66.5% STRONG_UP, 33.5% STRONG_DOWN)

**Reference counts (followthrough mode, full gates):**
- 82,621 labeled rows across 1,162 symbols (9.9% FOLLOW_THROUGH, 90.1% TIMEOUT)

**Outputs:**
- `data/labeled_panel.parquet` — panel + `label` + `barrier_direction` columns
- `data/label_stats.json` — row counts, class distribution, all params used

**Integrity checker:** `scripts/verify_labels.py`
```bash
venv/bin/python3 scripts/verify_labels.py --cooldown 5 --level2 --symbol RELIANCE
```

**Usage reference:** `scripts/label_generator_usage.md`

### Step 4: `scripts/train_baseline.py`
XGBoost binary classifier on `data/labeled_panel.parquet` with hard chronological split.
Auto-detects label scheme (direction or followthrough). Supports `--exclude-features`
for selective feature removal during experiments.

**Split:**
- TRAIN: `date < 2024-01-01`
- VAL: `2024-01-01 ≤ date < last-60-trading-days`
- HOLDOUT: last 60 trading days — written to `data/holdout_dates.json`, never used for fit/eval

**Quality gate:** val log-loss must be `< 0.55` (configurable via `--logloss-threshold`).

**Outputs (on gate pass):**
- `models/xgb_divergence_v1.ubj` — trained model
- `data/feature_importance.csv` — features ranked by importance
- `data/holdout_dates.json` — holdout date list + row count

---

## XGBoost Investigation — Key Findings

### Target Leakage in Direction Mode
The label generator assigns direction based on zone: `cwvap_dist < 0` → demand → STRONG_UP,
`cwvap_dist > 0` → supply → STRONG_DOWN. This means `cwvap_dist` (and any zone-proxy feature)
perfectly encodes the label. The model achieves 100% accuracy by learning a single threshold
on `cwvap_dist` — it never learns institutional setup patterns.

**Attempts to fix:**
1. Excluded `cwvap_dist` + `cpoc_dist` → accuracy stayed 94%, `cwvap_slope_5d` took over (33% importance)
2. Excluded ALL CWVAP-derived features → accuracy stayed 94%, `pdd_30` + `price_distance_30` stepped in
3. The leakage is structural: ~15 features encode signed distance-from-value-anchor, and in direction
   mode all of them correlate with zone membership → label

### Followthrough Mode — Model Can't Separate Classes
Reframed the task: predict FOLLOW_THROUGH vs TIMEOUT (did the zone setup actually hit the barrier?).
This eliminates zone leakage since both classes exist in both zones.

**Results:**
- 82K rows, 90/10 class split (TIMEOUT dominant)
- Logloss reached 0.42 (1500 rounds, never hit early stopping)
- But FOLLOW_THROUGH precision stayed at 11-12% across all thresholds = base rate
- Mean predicted probability: FOLLOW_THROUGH=0.204 vs TIMEOUT=0.197 (virtually identical)
- **Conclusion:** with current features, XGBoost cannot distinguish setups that will follow
  through from those that won't, beyond what the conviction gates already filter

### What Actually Works: Conviction Gates
Feature analysis on 65 manually validated examples (7 stocks, horizon=5) showed that
simple threshold combinations on engine features outperform the ML model:

| Filter | Precision | Recall |
|---|---|---|
| No filter (base rate) | 62% | 100% |
| `rdv≥0.6 AND rdv_cons≥2 AND cwc≤0.65` | **83%** | **60%** |

The gates reject 80% of false setups while keeping 60% of true ones.

---

## Conviction Gates — The Core Discovery

### Feature Analysis (65 validated examples across 7 stocks)

**Strongest separators between true setups and false setups:**

| Feature | Effect size | True median | False median | Meaning |
|---|---|---|---|---|
| `price_slope_z` | 1.16 | -0.16 | +0.13 | True setups are mean-reverting |
| `mfm_slope_3d` | 0.66 | -0.15 | +0.03 | Money flow declining (about to turn) |
| `price_distance_30` | 0.54 | 0.49 | 2.18 | True setups are closer to value |
| `cwc` | 0.50 | 0.40 | 0.54 | Low CWC = windows disagreeing = building |
| `mcs_composite` | 0.47 | -0.01 | +0.09 | Near-zero MCS = money flow not yet aligned |
| `delivery_pct` | 0.46 | 57.7% | 52.4% | Higher delivery % in true setups |
| `cwvap_dist` | 0.45 | -0.47 | +1.43 | True setups are closer to CWVAP |

**Key insight:** true setups are *building* (low CWC, negative price_slope_z, declining MFM)
but haven't completed. False setups already have consensus (high CWC) — the move is priced in.

### Gate Thresholds (calibrated from 65 examples)
- `--min-rdv 0.6` — lowered from 1.0 to include large-cap setups (RELIANCE, BAJFINANCE, LT)
  where delivery ratios are naturally lower due to deeper liquidity
- `--min-rdv-consistency 2` — at least 2 of last 5 days with above-average delivery
- `--max-cwc 0.65` — reject setups where all delivery windows already agree (priced in)

### Validated Examples
65 manually curated price action examples stored in `data/price_action_examples.dat`:
- 7 stocks: GESHIP, NESCO, AAVAS, RELIANCE, BAJFINANCE, COALINDIA, LT
- 40 true setups (barrier hit within 5 days), 25 false setups
- Verified against actual panel prices with 2×ATR barriers
- Used to discover and calibrate conviction gates

---

## Full Pipeline — Run Sequence

```bash
# 1. Sync NSE data (if stale)
venv/bin/python3 scripts/sync_nse_ca.py

# 2. Build full panel (~1160 symbols, slow)
venv/bin/python3 scripts/panel_builder.py --mode full

# 3. Generate labels (direction mode with conviction gates)
venv/bin/python3 scripts/label_generator.py --label-mode direction \
    --horizon 5 --cooldown 5 \
    --min-rdv 0.6 --min-rdv-consistency 2 --max-cwc 0.65

# 4. Verify labels
venv/bin/python3 scripts/verify_labels.py --cooldown 5

# 5. Train (if pursuing ML path)
venv/bin/python3 scripts/train_baseline.py --verbose
```

---

## Debugging / Tuning Tools

### `scripts/inspect_panel.py`
Inspect all feature values for a symbol + date. Essential for tuning label generator params.

```bash
# All features for a single date (vertical)
venv/bin/python3 scripts/inspect_panel.py --symbol RELIANCE --date 2025-03-04

# Feature group with surrounding context
venv/bin/python3 scripts/inspect_panel.py --symbol RELIANCE --date 2025-03-04 --group cwvap --context 5

# Custom columns
venv/bin/python3 scripts/inspect_panel.py --symbol RELIANCE --date 2025-03-04 \
    --cols close,cwvap,cwvap_dist,atr_20,rdv,mcs_composite --context 3
```

Available groups: `price`, `atr`, `cwvap`, `cpoc`, `dvl`, `rdv`, `mcs`, `mfm`,
`coherence`, `velocity`, `pdd`, `setup`, `dvwap`, `poc`, `cwc`

**Usage reference:** `scripts/inspect_panel_usage.md`

---

## File Map

| File | Purpose |
|------|---------|
| `scripts/panel_builder.py` | Step 1 — builds `data/panel.parquet` |
| `src/feature_engineer.py` | Step 2 — computes 84+ feature columns |
| `scripts/label_generator.py` | Step 3 — zone-aware labeling with conviction gates |
| `scripts/label_generator_usage.md` | Step 3 — usage reference |
| `scripts/verify_labels.py` | Step 3b — label integrity checks (supports both modes) |
| `scripts/inspect_panel.py` | Debugging — inspect feature values by symbol + date |
| `scripts/inspect_panel_usage.md` | Debugging — inspect_panel usage reference |
| `scripts/train_baseline.py` | Step 4 — XGBoost training (auto-detects label scheme) |
| `data/panel.parquet` | Raw feature panel (109 columns, ~2.1M rows) |
| `data/labeled_panel.parquet` | Labeled dataset (current: direction mode with gates) |
| `data/label_stats.json` | Label distribution stats + params used |
| `data/price_action_examples.dat` | 65 manually validated setups for gate calibration |
| `data/holdout_dates.json` | Holdout dates (do not use until final eval) |
| `models/xgb_divergence_v1.ubj` | Trained model (current: direction mode, zone-leaky) |
| `data/feature_importance.csv` | Feature rankings |
| `requirements-pipeline.txt` | Deps: tqdm, pyarrow, xgboost, scikit-learn |
| `divergence_engine_architecture.md` | Original architecture reference (partially superseded) |

---

## Next Step: Conviction Scorer

The XGBoost approach is paused. The recommended path forward is a **rules-based conviction
scorer** that replaces the ML model:

**Production pipeline:**
```
All stocks today
  → Run divergence engine (daily features)
  → Apply conviction gates (rdv≥0.6, rdv_cons≥2, cwc≤0.65, within CWVAP zone band)
  → Determine direction from zone (demand=UP, supply=DOWN)
  → Rank by conviction score (composite of rdv, cwc, cwvap depth, pdd, delivery_pct)
  → Top N signals → output
```

**Script to build:** `scripts/conviction_scorer.py` or integrate into `scripts/run_screener.py`

**Scoring function (proposed):** weighted composite of features that separate true from false setups:
- `cwc` (inverted — lower is better)
- `rdv` and `rdv_consistency`
- `abs(cwvap_dist)` (deeper in zone = stronger)
- `abs(price_slope_z)` (mean-reversion strength)
- `delivery_pct`
- `abs(pdd_30)` (price-delivery divergence)

This is interpretable, doesn't suffer from leakage, and directly captures the institutional
fingerprint discovered from the 65 validated examples.

---

## Dev Notes
- `panel_builder.py --mode full --limit 50` validates the pipeline end-to-end before the full run.
- Label generator params are saved into `data/label_stats.json` for reproducibility.
- CWVAP distance filter uses **raw numerical values only** — no engine string classifications.
- `--min-rdv` was lowered from 1.0 to 0.6 to include large-cap setups where delivery ratios
  are naturally lower (RELIANCE, BAJFINANCE, LT have deeper liquidity pools).
- CWC (Cross-Window Coherence) is the strongest single discriminator between true and false
  setups. Low CWC means delivery windows disagree — counterintuitively signals a *building*
  setup where one timeframe is accumulating before others catch up.
- `train_baseline.py` auto-detects label scheme from data. Use `--exclude-features` for
  selective feature removal during experiments.
- Dead features (zero importance across all experiments): `dvl_rate_10/30/60/120`, `cwvap_lag_3d`.
