# ML Guard: Iterative Training & Promotion Workflow

This document establishes the institutionalized process for periodically retraining the XGBoost ML Guard and promoting new models.

## 1. Rationale and Cadence
Financial markets evolve. The structural conditions that define a "Good" vs "Bad" setup shift over time. 
- **Cadence**: Models should be retrained **Monthly** or after significant regime shifts.
- **Trigger**: Win rate drop in `walk_forward` reports or significant increase in Hard Stop hits for `PRT_ZERO_CROSS`.

## 2. Step 1: Data Extraction
Generate a fresh trade-based dataset. This runs each entry path in a silo to capture its pure mechanical behavior.
```bash
./venv/bin/python scripts/extract_features_from_trades.py
```
- **Output**: `output/ml/dataset_trade_YYYYMMDD.csv`
- **Exclusion**: Always excludes the last 60 calendar days to avoid edge effects.

## 3. Step 2: Training & Model Selection
Train the suite of models (DT, RF, XGB) on the new dataset.
```bash
./venv/bin/python scripts/train_ml_guard.py --dataset output/ml/dataset_trade_YYYYMMDD.csv
```
- **Metrics**: Look for high **Recall on Class 0 (Bad Setups)**.
- **Model Promotion**: The script will automatically save the winner (XGBoost by default) to `src/trading/signals/savgol_cts/models/model_xgb_YYYYMMDD.joblib`.

## 4. Step 3: Out-of-Sample Validation
Validate the new model's performance on the 60-day holdout period.
```bash
./venv/bin/python scripts/study_ml_guard_prt.py
```
- **Verification**: Check if the setups with >80% probability indeed show high Win Rates and Avg PnL in the recent data.

## 5. Step 4: System Backtest (Quant Domain)
Run the full walk-forward backtest to ensure the new model plays well with all other signals and system exits.
```bash
./venv/bin/python scripts/walk_forward.py --watchlist "NIFTY 50"
```

## 6. Step 5: Promotion to Production
If Quant results are positive:
1. Update `ACTIVE_MODEL_VERSION` in `src/trading/signals/savgol_cts/ml_guard.py`.
2. Commit the `.joblib` binary to Git.
3. Commit the documentation update.
