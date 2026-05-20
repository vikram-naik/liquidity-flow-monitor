# ML Guard: Iterative Training & Promotion Workflow

This document establishes the institutionalized process for periodically retraining the XGBoost ML Guard, evaluating its performance across both the Machine Learning domain and the Quant (Backtesting) domain, and promoting new models to production.

## 1. Rationale and Cadence
Financial markets evolve. The structural conditions that define a "Good" vs "Bad" setup shift over time. To prevent model decay, the XGBoost model should be iteratively retrained on fresh historical data.
**Recommended Cadence:** Monthly or after a significant market regime change.

## 2. Artifact Naming Convention
To ensure reproducibility and easy rollbacks, all data and model artifacts MUST be versioned with a timestamp (`YYYYMMDD`) and include the source watchlist and PnL threshold where applicable.
*   **Dataset:** `output/ml/dataset_dense_<watchlist>_YYYYMMDD.csv`
*   **Model Binary:** `src/trading/signals/savgol_cts/models/model_xgb_<watchlist>_<threshold>_YYYYMMDD.joblib`

*Note: The model binary is explicitly checked into the `src/` tree to ensure Git tracks the exact weights used for backtesting at any given commit.*

## 3. The Iterative Process

### Step 1: Extract Dense Trade Features
Instead of using standard simulation which suffers from "signal shadowing" (ignoring crosses while a trade is open), the system uses a **Dense Extraction** model. 

It evaluates every single bar for *any* structural inflection (PRT, FAS, CTS, or Accel cross). If a cross occurs, it spawns a Virtual Trade. Overlapping trades are permitted. All setups are forced to exit using the standardized `Universal-Cross` exit logic to ensure a mathematically consistent target variable. 
The raw performance metrics (`pnl_pct`, `mfe_pct`, `mae_pct`) are saved for each setup.

Run the dense extraction script:
```bash
./venv/bin/python scripts/extract_dense_universal_features.py --watchlist "NIFTY 50"
```
*Output:* `output/ml/dataset_dense_nifty_50_<DATE>.csv`

### Step 2: Train the Universal XGBoost Model
The training script loads the dense dataset, dynamically labels the trades as "Good" (1) if the `pnl_pct` meets the specified `--threshold` (e.g., 4.0%), splits it (80/20 stratified), applies a cost-sensitive class weight (penalizing false positives), and trains a deep XGBoost classifier (`max_depth=7`) capable of synthesizing the various trigger types into a single confidence score.

Run the training script:
```bash
./venv/bin/python scripts/train_ml_guard.py --threshold 4.0
```
*Output:* `src/trading/signals/savgol_cts/models/model_xgb_nifty_50_4p0_<DATE>.joblib`

### Step 2.1: Analyze the Trade Efficiency Report
During training, the script generates a **Trade Efficiency Report** on the hold-out test set. This report is the primary tool for evaluating the impact of the `--threshold` without running a full backtest.

| Cutoff | Trades | Precision | WinRate | Avg PnL |
| :--- | :--- | :--- | :--- | :--- |
| **0.00** | 3291 | 34.2% | 55.8% | 2.28% |
| **0.80** | 156 | 87.8% | 94.2% | 14.84% |
| **0.90** | 54 | 96.3% | 100.0% | 17.53% |

*   **Cutoff**: The minimum ML confidence score required to take a trade.
*   **Precision**: % of trades that reached the training `--threshold`.
*   **WinRate**: % of trades that had a positive PnL.
*   **Avg PnL**: The expected average return per trade at this confidence level.

Use this report to decide on the `min_ml_score` (Gate 2) in your signal config. If the `Avg PnL` at 0.80 is high enough for your strategy, you can set `min_ml_score = 80.0`.

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
