# ML-Guarded Signal Integration: Universal Master Model

This document details the architecture and implementation of the ML-Guarded entry signal integrated into the `SavgolCTS` signal package.

## 1. Overview
The `Universal Cross` entry path implements a "Model-in-the-Loop" architecture. Instead of relying on hardcoded technical thresholds across 8 different signal paths, it uses a pre-trained XGBoost classifier as a mandatory "Guard" to evaluate the structural validity of *any* momentum or structural inflection.

## 2. Core Modules

### `src/trading/signals/savgol_cts/ml_guard.py`
This module defines the `MLGuard` class, which operates as a **Singleton** to ensure efficient model resource management.

- **Initialization**: Loads the serialized model artifact (`src/trading/signals/savgol_cts/models/model_xgb_YYYYMMDD.joblib`) once upon first access.
- **Inference**: The `score_setup(row)` method:
    1.  Maps the input `row` (current market state, including boolean trigger columns) to the exact `feature_cols` expected by the model.
    2.  Handles missing values by imputing `0` (neutral state), matching training-time preprocessing.
    3.  Returns the probability score for the "Good Setup" (Label 1) class.

### `src/trading/signals/savgol_cts/entries/universal_cross.py`
This module implements the entry logic, orchestrating the mechanical triggers and the ML Guard.

- **Gate 1 (Mechanical Inflection)**: Verifies a positive structural inflection in ANY of the following:
  - `prt_slope` zero cross.
  - `fas` crossing its dynamic buy threshold.
  - `cts` crossing its dynamic buy threshold.
  - `cts_accel` crossing its dynamic buy threshold.
- **Gate 2 (ML Guard)**: Invokes `MLGuard.get_instance().score_setup(row_dict)` passing the indicator state AND the trigger context (e.g., `trigger_prt = 1`).
- **Threshold Enforcement**: Rejects any trade where the ML confidence (`prob_pct`) is below the configurable `min_ml_score` threshold (default `85.0%`).
- **Intensity Mapping**: The ML confidence score is passed back as the signal intensity, ensuring the confidence percentage is displayed directly on the UI.

## 3. Workflow Integration

### Data Pipeline (Dense Extraction)
1. **Dense Extraction Script**: `extract_dense_universal_features.py` iterates over history without signal shadowing. It spawns overlapping virtual trades for every structural inflection.
2. **Standardized Exits**: Every virtual trade is resolved using the Pure CTS Trailing logic to ensure the ML target variable represents the exact same systemic reality.
3. **Cost-Sensitive Training**: An XGBoost classifier (`max_depth=7`) is trained with heavy class weighting (e.g., `{0: 2, 1: 1}`) to prioritize detecting failure-prone "Bad" setups.

### Live Inference
When the `SavgolCTSSignal` orchestrator evaluates a bar:
1.  The `Universal Cross` path checks if any structural inflection fired.
2.  If yes, the `MLGuard` scores the setup based on the holistic indicator state.
3.  If `ML Score < threshold` (e.g., 85.0%), the entry is rejected.
4.  If passed, the trade is explicitly tagged as `UNIVERSAL_CROSS` to ensure it routes to the standardized CTS Trailing exit logic alongside CWVAP guards and an 8% hard stop.
