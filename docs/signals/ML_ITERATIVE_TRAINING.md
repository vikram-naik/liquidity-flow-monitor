# ML Guard: Iterative Training & Promotion Workflow

This document establishes the institutionalized process for periodically retraining the XGBoost ML Guard, evaluating its performance across both the Machine Learning domain and the Quant (Backtesting) domain, and promoting new models to production.

## 1. Rationale and Cadence
Financial markets evolve. The structural conditions that define a "Good" vs "Bad" setup shift over time. To prevent model decay, the XGBoost model should be iteratively retrained on fresh historical data.
**Recommended Cadence:** Monthly or after a significant market regime change.

## 2. Artifact Naming Convention
To ensure reproducibility and easy rollbacks, all data and model artifacts MUST be versioned with a timestamp (`YYYYMMDD`).
*   **Dataset:** `output/ml/dataset_dense_YYYYMMDD.csv`
*   **Model Binary:** `src/trading/signals/savgol_cts/models/model_xgb_YYYYMMDD.joblib`

*Note: The model binary is explicitly checked into the `src/` tree to ensure Git tracks the exact weights used for backtesting at any given commit.*

## 3. The Iterative Process

### Step 1: Extract Dense Trade Features
Instead of using standard simulation which suffers from "signal shadowing" (ignoring crosses while a trade is open), the system uses a **Dense Extraction** model. 

It evaluates every single bar for *any* structural inflection (PRT, FAS, CTS, or Accel cross). If a cross occurs, it spawns a Virtual Trade. Overlapping trades are permitted. All setups are forced to exit using the standardized `Universal-Cross` exit logic to ensure a mathematically consistent target variable. 
A trade is labeled "Good" (1) if the PnL reaches **4.0%**.

Run the dense extraction script:
```bash
./venv/bin/python scripts/extract_dense_universal_features.py --watchlist "NIFTY 50" --threshold 4.0
```
*Output:* `output/ml/dataset_dense_<DATE>.csv`

### Step 2: Train the Universal XGBoost Model
The training script loads the dense dataset, splits it (80/20 stratified), applies a cost-sensitive class weight (penalizing false positives), and trains a deep XGBoost classifier (`max_depth=7`) capable of synthesizing the various trigger types into a single confidence score.

Run the training script:
```bash
./venv/bin/python scripts/train_ml_guard.py
```
*Output:* `src/trading/signals/savgol_cts/models/model_xgb_<DATE>.joblib`

### Step 3: Quant Domain Validation
A model that performs well in `scikit-learn` might fail in a path-dependent backtest. You MUST validate the new model's efficacy against the actual trading simulator.

1.  **Update the Active Model:** Edit `src/trading/signals/savgol_cts/ml_guard.py` to point to the newly generated `model_xgb_<DATE>.joblib`.
2.  **Full System Walk-Forward:** Run the exhaustive system backtest. With the Universal Cross enabled, the system should achieve a massive profit factor and a Win Rate > 90%.
    ```bash
    ./venv/bin/python scripts/walk_forward.py --watchlist "NIFTY 50"
    ```

### Step 4: Promotion & Rollback
Once the Quant Validation is successful:
1.  Commit the new `model_xgb_<DATE>.joblib` binary to the repository.
2.  Commit the updated pointer in `ml_guard.py`.
3.  Document the new baseline metrics in the PR/Commit message.

**Rollback:** If a newly promoted model begins underperforming, simply revert the model pointer in `ml_guard.py` to the previous `YYYYMMDD.joblib` artifact and flush the redis cache.
