---
name: signal-path-implementation
description: Repo-specific checklist and procedure for implementing a new signal entry/exit path into the core SavgolCTS package. Use this when a signal prototype from a study script is ready for permanent migration into the trading system.
---

# Signal Path Implementation Checklist

This skill provides the procedure for migrating a validated signal trigger into the core Universal Master Path.

## 1. Implement Trigger Logic
Update `src/trading/signals/savgol_cts/entries/universal_cross.py`.
- **Trigger Extraction**: Define the new mechanical inflection (e.g., a specific crossover).
- **Trigger Variable**: Add a `trigger_<name>` binary variable (0 or 1).
- **Wiring**: Include the new trigger in the `any()` check before calling the ML Guard.
- **Row Update**: Add the new trigger to the `row_dict` so the ML Guard can see it as a feature.

## 2. Dataset Enrichment (Dense Extraction)
Update `scripts/extract_dense_universal_features.py` to capture the new trigger.
- **Triggers Dict**: Include the new trigger in the `VirtualTrade` object's metadata.
- **Feature CSV**: Run the script to generate a fresh training dataset with the new trigger column.

## 3. Retrain ML Guard
Run `scripts/train_ml_guard.py` to produce a new model that "understands" how to filter the new trigger.
- **Verification**: Ensure the classification report shows improved precision/recall for the "Good Trade" label.
- **Deployment**: Update `ACTIVE_MODEL_VERSION` in `src/trading/signals/savgol_cts/ml_guard.py`.

## 4. Documentation
Update `SIGNAL_FLOW.md` to reflect the new trigger in the Universal Master Path diagram.

## ⚠️ Mandatory: EOD-Lag Reality Check
Before migrating any study to production, you MUST verify that the study used **EOD-Lag Execution** (Signal at `i`, Entry at `i+1`).
- **Production Code**: Ensure `UniversalCross` correctly handles the 1-bar lag between signal identification and execution.
