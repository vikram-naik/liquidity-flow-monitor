---
name: signal-ml-guard-pipeline
description: End-to-end lifecycle for implementing and maintaining "Model-in-the-Loop" ML Guards for trading signals. Use when asked to improve signal win rates, filter falling knives using machine learning, or institutionalize an iterative retraining workflow for XGBoost/RandomForest guards.
---

# Signal ML Guard Pipeline

This skill provides the standardized workflow for implementing, training, and integrating machine learning models as mandatory "Guards" for trading signals. This architecture ensures that mechanical signals are only executed if a model confirms the setup's statistical probability of success.

## 1. Data Extraction (Dense vs. Siloed)

Models are trained on technical indicator states at the moment a trigger fires, paired with the actual PnL outcome of the system's exits.

### Standard Siloed Extraction
Used for refining a specific mechanical path.
1. **Siloed Backtesting**: Run a single entry path with all other paths disabled.
2. **Sequential Logic**: Respects the `in_trade` lock (one trade at a time per symbol).
3. **Command**:
   ```bash
   ./venv/bin/python scripts/extract_features_from_trades.py
   ```

### Dense Universal Extraction (Recommended)
Used for training a "Universal Master Model" that handles multiple trigger types.
1. **Overlapping Virtual Trades**: Abandon the `in_trade` blocking logic. Capture *every single bar* where a trigger (PRT, FAS, CTS, Accel) occurs.
2. **Contextual Triggers**: Add binary features (`trigger_prt`, `trigger_fas`, etc.) so the model knows which inflection point was breached.
3. **Outcome Normalization**: Resolve all generated virtual setups using the **same standardized exit logic** (e.g., Pure CTS Trailing) to ensure a consistent target variable (`y`).
4. **Command**:
   ```bash
   ./venv/bin/python scripts/extract_dense_universal_features.py --watchlist "NIFTY 50"
   ```

## 2. Cost-Sensitive Training

Trading guards must be **conservative**. It is better to miss a good trade than to enter a bad one.

1. **Labeling**: 
   - **Label 1 (Good)**: PnL $\ge$ 4.0% (Modern standard) or 2.5%.
   - **Label 0 (Bad)**: PnL below target or hard stop hit.
2. **Imbalance Handling**: Use severe class weighting (e.g., `30:1` or `20:1`) to penalize false positives (Type I error).
3. **Model Selection**: Prefer `XGBoost` (`max_depth=7` for dense datasets) as it handles non-linear relationships across 40+ indicators.
4. **Command**:
   ```bash
   ./venv/bin/python scripts/train_ml_guard.py --dataset output/ml/dataset_dense_YYYYMMDD.csv
   ```

## 3. Universal Model Architecture (ML-Heavy)

The system is transitioning from 8+ complex mechanical paths to a single **Universal Funnel** (Path 0).

1. **Path 0 Logic**: Triggers on ANY structural inflection (PRT Cross, FAS Cross, CTS Cross, or Accel Cross).
2. **ML Gatekeeper**: The XGBoost model acts as the sole decision boundary. If inflection occurs, it feeds the current state to `MLGuard`.
3. **Integration**:
   - Call `MLGuard.get_instance().score_setup(row)`.
   - Enforce a high `min_ml_score` (e.g., `85.0%`).
   - The confidence score is returned as the signal intensity (90-99) for UI visualization.

## 4. Iterative Retraining Workflow

Model drift is inevitable. Retrain monthly or after regime changes.

### Workflow Summary:
1. **Extract**: Run `scripts/extract_dense_universal_features.py` to capture fresh variance.
2. **Train**: Run `scripts/train_ml_guard.py`. Select the model with the highest **Precision for Class 1**.
3. **Quant Validation**: Run `scripts/analyze_score_thresholds.py` to find the optimal confidence floor.
4. **Backtest**: Run `scripts/walk_forward.py --watchlist "NIFTY 50"` to verify portfolio-level Profit Factor and Win Rate.
5. **Promote**: Update `ACTIVE_MODEL_VERSION` in `ml_guard.py` and commit the binary.

## Common Landmines

- **Temporal Misalignment**: Features must match the state seen on the **Signal Bar** (Bar `i`).
- **Edge Effects**: Exclude the last 60 calendar days from training to avoid Savitzky-Golay "right-edge" whipping.
- **UI Truthiness**: Ensure `check_entry` returns a non-zero intensity (e.g., `int(ml_score)`) so JS truthiness checks in the web UI don't skip the marker.
- **Docker Image Bloat**: `xgboost` wheels for Linux bundle ~400MB of unused Nvidia GPU libraries (`nvidia-nccl-cu12`). In the `Dockerfile`, purge these and package tests after `pip install` to keep the image under 900MB:
  ```dockerfile
  RUN pip install --no-cache-dir -r requirements.txt && \
      rm -rf /usr/local/lib/python3.12/site-packages/nvidia* && \
      find /usr/local/lib/python3.12/site-packages -name "tests" -type d -exec rm -rf {} +
  ```
