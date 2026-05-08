# ML Guard: Code and Output Artifacts

This document catalogs the scripts, core modules, and output artifacts created to implement the "Model-in-the-Loop" ML Guard for the Liquidity Flow Monitor.

## 1. Data Pipeline & Labeling

*   **`scripts/extract_features_from_trades.py`**
    *   *Type*: Study Script
    *   *Description*: Iterates through all entry paths in historical backtests (siloed). For every mechanically generated entry, it captures the exact state of all normalized technical indicators (the feature vector) and the actual system PnL. Labels the setup as "Good" (1) if PnL $\ge$ 2.5%, else "Bad" (0). This ensures the model trains on the exact systemic reality it will face in live inference.
*   **`output/ml/dataset_trade_YYYYMMDD.csv`**
    *   *Type*: Output Artifact
    *   *Description*: The compiled dataset containing thousands of actual historical trades, primed for machine learning.
*   **`src/divergence_engine/oracle.py` & `scripts/study_oracle_labelling.py`**
    *   *Type*: Legacy / Visualization
    *   *Description*: Previously used to label ground-truth structural troughs. Now maintained primarily for UI visualization and ad-hoc chart studies.

## 2. Feature Extraction & Model Training

*   **`scripts/train_ml_guard.py`**
    *   *Type*: Training Script
    *   *Description*: Loads the CSV dataset, splits it (80/20 stratified), and trains an `XGBClassifier`. Applies cost-sensitive learning weights (e.g., `{0: 2, 1: 1}`) to penalize missing a bad setup while handling the real-world class balance. Exports the final model to the source tree.
*   **`src/trading/signals/savgol_cts/models/model_xgb_YYYYMMDD.joblib`**
    *   *Type*: Output Artifact (Version-Controlled)
    *   *Description*: The serialized XGBoost model, including the trained model and the exact list of normalized feature columns it expects during live inference. Checked into git to ensure backtest reproducibility.

## 3. Inference Validation

*   **`scripts/analyze_score_thresholds.py`**
    *   *Type*: Study Script
    *   *Description*: Validates the model in the Quant Domain. Runs the full historical backtest and generates a PnL distribution report grouped by the ML confidence score, allowing the developer to identify the optimal entry threshold (e.g., 80%).

## 4. Production Integration (Core Modules)

*   **`src/trading/signals/savgol_cts/ml_guard.py`**
    *   *Type*: Core Module
    *   *Description*: A robust Singleton class that loads the active `model_xgb_YYYYMMDD.joblib` into memory once. Provides the `score_setup(row)` method to execute sub-millisecond live inference on active market data using the XGBoost model.
*   **`src/trading/signals/savgol_cts/entries/prt_zero_cross.py`**
    *   *Type*: Signal Entry Path
    *   *Description*: The `PRT_ZERO_CROSS` entry logic. It checks for a mechanical momentum cross, queries the `MLGuard`, and mandates a minimum confidence score (currently 80.0%). Overrides the standard UI intensity score with the ML confidence probability.
*   **`src/trading/signals/savgol_cts/exits/prt_zero_cross.py`**
    *   *Type*: Signal Exit Path
    *   *Description*: The dedicated exit logic for the ML-Guarded entry. Implements a pure CTS Trailing Stop alongside system-level hard stops (8%) and CWVAP momentum guards.
*   **`src/trading/signals/savgol_cts/config.py` & `signal.py`**
    *   *Type*: Configuration & Orchestration
    *   *Description*: Updated to route and manage the `PRT_ZERO_CROSS` entry/exit paths within the overarching `SavgolCTS` strategy.

## 5. UI & Tooling Enhancements

*   **`src/divergence_engine/chart.py` & `src/web/js/divergence_engine.js`**
    *   *Type*: Frontend Integration
    *   *Description*: Parses and renders markers directly on the candlestick chart, allowing visual inspection of Oracle points and technical states.
*   **`scripts/dump_trades.py`**
    *   *Type*: CLI Tool
    *   *Description*: Allows developers to dump, sort, and analyze trades specifically approved by the machine learning model.
*   **`scripts/validate_data_integrity.py`**
    *   *Type*: Maintenance Tool
    *   *Description*: Heavily upgraded to be "dividend-aware" during reconciliation, ensuring the historical data fed to the ML model is pristine and free of unadjusted corporate action whip-saws.
