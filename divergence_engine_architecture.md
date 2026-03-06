# XGBoost Divergence Engine — Complete Architecture

> Liquidity Flow Monitor | feature/divergence-engin branch
> Reference document summarising all design decisions from architecture discussions.

---

## Part 1 — Offline Training & Validation

### The Full Data Pipeline (in order)

```
NSE Raw OHLCV + Delivery Data (Jan 2020 → present)
        │
        ▼
[ panel_builder.py ]          ← quality gate lives here
        │
        ▼
[ feature_engineer.py ]       ← lookback features computed here
        │
        ▼
[ label_generator.py ]        ← Triple Barrier labeling here
        │
        ▼
[ train_baseline.py ]         ← offline train + validate here
        │
        ▼
models/xgb_divergence_v1.ubj  ← mounted into container, never baked in
```

---

### Step 1 — Panel Builder (`panel_builder.py`)

Pool all NIFTY 500 stocks into one long-format dataset. Apply the quality gate
**before** any feature calculation to avoid poisoned rows.

**Quality Gate — discard a stock entirely if:**
- Daily price coverage < 80% of the full date spine
- Any single-day price move > 40% that is NOT in the corporate actions table
- Delivery volume coverage < 70% of trading days

Write all rejected symbols + reason to `data/rejected_symbols.csv` for audit.

Expected outcome: ~350 clean stocks surviving the filter from NIFTY 500.

---

### Step 2 — Feature Engineer (`feature_engineer.py`)

> **Core insight:** XGBoost sees a single row per stock per day. It has no native
> memory. The lookback trajectory of your indicators must be *flattened into that row*
> as engineered features. The model learns the setup, not the snapshot.

**For each stock × day row, compute:**

```
CWVAP features
  cwvap_today
  cwvap_lag_3d, cwvap_lag_5d, cwvap_lag_10d
  cwvap_slope_3d     = (cwvap_today - cwvap_3d_ago) / 3
  cwvap_slope_10d    = (cwvap_today - cwvap_10d_ago) / 10
  cwvap_acceleration = cwvap_slope_3d - cwvap_slope_10d   ← is slope steepening?

RDV features
  rdv_slope_5d
  rdv_consistency    = count of days in last 5 with above-average delivery ratio
  rdv_vs_20d_avg     = today's RDV relative to its own 20-day mean

Coherence features
  coherence_today
  coherence_trend_5d    = slope (should be FALLING for a real STRONG_UP)
  coherence_volatility  = std over 10 days (erratic = noise, smooth = institutional)

MFM features
  mfm_slope_3d
  mfm_vs_10d_avg     = today's MFM vs its own 10-day mean
  mfm_acceleration   = mfm_slope_3d - mfm_slope_10d

Cross-indicator alignment (the institutional fingerprint)
  all_aligned_5d     = (cwvap_slope_5d > 0) AND (rdv_slope_5d > 0)
                       AND (coherence_trend_5d < 0)  — all three agreeing
  setup_duration     = consecutive days the above alignment has held
                       ← most powerful single feature
```

Multi-timeframe merging: use `pd.merge_asof(direction='backward')` to forward-fill
weekly and monthly indicators onto the daily spine. This strictly prevents
look-ahead bias.

---

### Step 3 — Label Generator (`label_generator.py`)

**Design decision: Binary labels only — STRONG_UP and STRONG_DOWN.**
Weak moves do not change trend structure. Timeout rows (no barrier hit) are dropped.
This trains the model exclusively on the feature fingerprint that precedes a
genuine institutional move.

**Triple Barrier logic:**

```python
def triple_barrier_label(prices, start_idx, atr_value,
                          profit_mult=2.0, stop_mult=2.0,
                          horizon=10):
    entry  = prices.iloc[start_idx]
    upper  = entry + (profit_mult * atr_value)
    lower  = entry - (stop_mult * atr_value)
    window = prices.iloc[start_idx + 1 : start_idx + horizon + 1]

    for price in window:
        if price >= upper: return 'STRONG_UP'
        if price <= lower: return 'STRONG_DOWN'

    return None  # timeout → excluded from training
```

**Deduplication (critical):**
After labeling, consecutive rows for the same stock will echo the same institutional
move. Suppress the next 10 rows per stock after each label to get independent signals.

```python
def deduplicate_signals(df, cooldown_days=10):
    # Keep only the first detection of each institutional move per stock
    # Sort by (symbol, date), suppress rows within cooldown window
```

**Expected label distribution after deduplication:**
```
Raw panel rows:       ~500K
After noise drop:     ~150K  (barrier hit within 10 days)
After deduplication:  ~50K   (independent signals)
  STRONG_UP:          ~32K   (post-2020 bullish skew)
  STRONG_DOWN:        ~18K
```

---

### Step 4 — Offline Training & Validation (`train_baseline.py`)

**Hard chronological split — never shuffle time-series data:**

```
|── TRAIN ─────────────────|── VALIDATE ───────────|── HOLDOUT ──|
  Jan 2020 → Dec 2023         Jan 2024 → Oct 2024     Last 60 days
                                                       (never touched)
```

The validate set answers the key question before going live:
*"On data this model never saw, what would it have done?"*

**XGBoost configuration:**

```python
model = xgb.XGBClassifier(
    objective='binary:logistic',      # clean binary — not multi:softprob
    n_estimators=500,
    learning_rate=0.05,
    max_depth=6,
    scale_pos_weight=(n_strong_down / n_strong_up),  # correct for bullish skew
    early_stopping_rounds=30,         # stops when val loss plateaus — prevents overfit
    eval_metric='logloss',
)

model.fit(
    X_train, y_train,
    eval_set=[(X_val, y_val)],        # XGBoost watches val loss during training
    verbose=50,
)
```

**Validation metrics — what to check before deploying:**

| Metric | What it tells you | Target |
|--------|------------------|--------|
| Log Loss | Overall probability calibration | < 0.55 (random binary = 0.693) |
| Precision STRONG_UP | Of all UP calls, how many were right | > 0.60 |
| Recall STRONG_UP | Of all real UP moves, how many were caught | > 0.50 |
| Precision STRONG_DOWN | Of all DOWN calls, how many were right | > 0.60 |
| Calibration plot | Does 70% confidence win ~70% of the time? | Diagonal line |

**Quality gate — only save model if it clears the bar:**

```python
if log_loss(y_val, y_proba) < 0.55:
    model.save_model("models/xgb_divergence_v1.ubj")
    # This file is mounted into the container, never baked into the image
else:
    # Do not deploy — retune features or barriers first
```

---

## Part 2 — Production Inference + Continuous Retraining Cycle

### Architecture Overview

```
                    ┌─────────────────────────────────────┐
                    │         Docker Container             │
                    │                                      │
  NSE EOD Data ──►  │  eod_pipeline.py                    │
                    │       │                              │
                    │       ▼                              │
                    │  feature_engineer.py (today's row)  │
                    │       │                              │
                    │       ▼                              │
                    │  model.predict_proba()               │  ◄── /app/models/ (mounted volume)
                    │       │                              │
                    │       ▼                              │
                    │  predictions table (append-only)    │  ◄── /app/data/   (mounted volume)
                    └─────────────────────────────────────┘
                                    │
                          every weekend / month-end
                                    │
                                    ▼
                    ┌─────────────────────────────────────┐
                    │      retrain_scheduler.py            │
                    │                                      │
                    │  1. Settle labels on closed windows  │
                    │  2. Compute performance metrics      │
                    │  3. Retrain if decay detected        │
                    │  4. Write new model version          │
                    └─────────────────────────────────────┘
```

**Docker volume mount (never bake model into image):**

```yaml
services:
  divergence-engine:
    image: liquidity-flow-monitor:latest
    volumes:
      - ./data:/app/data        # panel data, predictions DB
      - ./models:/app/models    # model artifacts — swap without rebuild
    environment:
      - MODEL_PATH=/app/models/xgb_divergence_v1.ubj
      - CONFIDENCE_THRESHOLD=0.65
```

---

### Daily Inference Pipeline (`eod_pipeline.py`)

1. Pull NSE EOD data for all tracked stocks
2. Calculate today's features using `feature_engineer.py`
3. Load model from `MODEL_PATH` env var
4. Run `model.predict_proba(today_features)`
5. Write to predictions table — **append only, never overwrite**

**Predictions table schema:**

```sql
CREATE TABLE predictions (
    symbol              TEXT,
    prediction_date     DATE,
    model_version       TEXT,          -- track which model made this call
    p_strong_up         FLOAT,         -- raw probability output
    confidence_flag     BOOLEAN,       -- p_strong_up >= threshold
    direction           TEXT,          -- 'STRONG_UP' / 'STRONG_DOWN' / 'NO_CALL'
    horizon_end_date    DATE,          -- prediction_date + 10 trading days
    outcome             TEXT,          -- NULL until settled by retrain_scheduler
    outcome_settled     BOOLEAN,       -- FALSE until horizon window closes
    PRIMARY KEY (symbol, prediction_date)   -- enforces immutability at DB level
);
```

The `PRIMARY KEY` constraint is the technical enforcement of point-in-time storage.
Even if the model's view changes the next day, yesterday's prediction row is locked.

---

### Continuous Retraining Cycle (`retrain_scheduler.py`)

Runs every weekend. Two distinct jobs:

**Job 1 — Settle outcomes on closed prediction windows:**

```python
# Find all predictions where horizon_end_date < today and outcome is still NULL
# Look up actual price action for those rows
# Attach TRUE label: did price hit 2x ATR in the direction predicted?
# Update outcome column — this is the ground truth feedback loop
```

**Job 2 — Detect decay and conditionally retrain:**

```python
# Only retrain if:
#   a) At least 500 newly settled predictions are available
#   b) Rolling accuracy over last 60 settled predictions < decay_threshold
#   c) Log Loss over last 60 predictions is rising (trending worse)

if should_retrain:
    # Fine-tune on recent data — do NOT retrain from scratch
    # Higher learning_rate for recent data to adapt to regime shifts
    # Save as new versioned model: xgb_divergence_v{N+1}.ubj
    # Update MODEL_PATH env var → restart container (no image rebuild)
```

---

### Model Decay Monitoring — The Performance Dashboard

Track these metrics over time in a `model_performance` table.
Plot them weekly to see drift visually.

**Rolling accuracy (most intuitive metric):**

```
Week 1:   87% of STRONG_UP calls within confidence threshold were correct
Week 4:   84%
Week 8:   79%   ← watch this
Week 12:  71%   ← retrain triggered
```

**Full metric set to track:**

| Metric | Frequency | Decay Signal |
|--------|-----------|--------------|
| Rolling precision (last 60) | Weekly | Drop > 10pp from baseline |
| Rolling log loss (last 60) | Weekly | Rising trend over 4 weeks |
| Calibration error | Monthly | Probabilities drifting off diagonal |
| STRONG_UP recall | Monthly | Missing real moves |
| STRONG_DOWN recall | Monthly | Missing real moves |
| % of days with NO_CALL | Weekly | Sudden spike = model lost confidence |

**`model_performance` table schema:**

```sql
CREATE TABLE model_performance (
    week_ending          DATE,
    model_version        TEXT,
    n_settled            INTEGER,      -- how many predictions were settled this week
    rolling_precision    FLOAT,        -- last 60 settled predictions
    rolling_log_loss     FLOAT,
    pct_strong_up_correct FLOAT,
    pct_strong_down_correct FLOAT,
    pct_no_call          FLOAT,
    decay_flag           BOOLEAN,      -- TRUE if retrain was triggered
    retrain_version      TEXT          -- new model version if retrain fired
);
```

---

## Summary — Files to Build (in order)

| Order | File | Purpose |
|-------|------|---------|
| 1 | `scripts/panel_builder.py` | Quality gate + cross-sectional panel |
| 2 | `src/feature_engineer.py` | Lookback features — the setup trajectory |
| 3 | `scripts/label_generator.py` | Triple Barrier binary labels + deduplication |
| 4 | `scripts/train_baseline.py` | Offline train + validate + quality gate |
| 5 | `src/eod_pipeline.py` | Daily inference + append-only prediction write |
| 6 | `scripts/retrain_scheduler.py` | Weekend settle + decay detection + retrain |
| 7 | `src/performance_monitor.py` | Weekly metrics + decay dashboard |

---

## Key Design Decisions (locked)

- **Binary labels only** — STRONG_UP / STRONG_DOWN. Timeout rows dropped.
- **Label lives on Day 0** — the start of the setup, not the day of the price explosion.
- **Lookback features flatten history** — setup_duration and all_aligned_5d are the most important features.
- **Hard chronological split** — train/val/holdout by date. Never shuffle.
- **Model mounted via volume** — never baked into the Docker image.
- **Predictions are immutable** — PRIMARY KEY on (symbol, prediction_date).
- **Model version tracked per prediction** — every row knows which model made the call.
- **Retrain is fine-tuning, not from scratch** — adapts to regime shifts without losing foundational learning.
- **Minimum 500 settled labels before retraining** — prevents overfitting to recent noise.