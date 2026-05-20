# ML Guard — Training Stats Analysis & XGBoost Code Review

**Author:** Claude Sonnet (Anthropic)
**Date:** 2026-05-16
**Scope:** XGBoost threshold sweep analysis + `train_ml_guard.py` code review

---

## Part 1 — Threshold Sweep Analysis

### Summary Table (at 0.90 Confidence Cutoff)

| Threshold | Trades (0.9) | Precision | Win Rate | Avg PnL | Notes |
|-----------|-------------|-----------|----------|---------|-------|
| 0.0       | 117         | 84.6%     | 84.6%    | 12.32%  | Most trades overall (6706 @ 0.5) |
| 1.0       | 64          | 85.9%     | 89.1%    | 13.63%  | Good balance of volume + quality |
| 2.0       | 55          | **92.7%** | **96.4%**| 15.49%  | ⭐ Best precision & win rate |
| 2.5       | 51          | 82.4%     | 92.2%    | 13.10%  | Dip — precision regresses vs 2.0 |
| 3.0       | 44          | 90.9%     | 93.2%    | 12.68%  | Recovers but avg PnL drops |
| 3.5       | 31          | 90.3%     | 93.5%    | 14.90%  | Strong, thin sample |
| 4.0       | 36          | 77.8%     | 80.6%    | 15.41%  | Precision falls sharply |
| 4.5       | 27          | 74.1%     | 85.2%    | **21.93%** | ⚠️ Highest PnL but n=27, unreliable |

---

### Key Observations

#### 1. Threshold 2.0 is the Sweet Spot for Model Quality
At the 0.90 cutoff, Threshold 2.0 achieves the highest precision (92.7%) and win rate (96.4%). The label definition "trade returned ≥ 2%" is tight enough to teach the model to discriminate meaningfully without being so rare that the class becomes unlearnable. This maps to 55 trades on the test set — statistically meaningful.

#### 2. The 2.5 Dip is Suspicious
Precision drops from 92.7% (Threshold 2.0) to 82.4% (Threshold 2.5) before recovering at 3.0. This non-monotonic behaviour suggests the label boundary at 2.5% coincides with a noisy cluster of trades — borderline setups that the model cannot reliably classify. Worth investigating whether there is a natural PnL distribution break around this level.

#### 3. Threshold 4.5 is a Mirage
The 21.93% avg PnL at 0.90 cutoff looks exceptional, but it is based on n=27 trades. With this sample size, a single outlier trade can swing avg PnL by several percentage points. Do not treat this as reliable signal. A bootstrapped confidence interval would be needed before drawing conclusions.

#### 4. Label Imbalance Worsens with Higher Thresholds
- Threshold 0.0 → Class 1 = 14,460 (51.2% of dataset)
- Threshold 2.0 → Class 1 = 12,010 (42.5%)
- Threshold 4.5 → Class 1 = 9,308 (32.9%)

As threshold rises, Class 1 shrinks, recall collapses (from 0.31 at Threshold 0 to 0.10 at 4.5), and the model increasingly over-predicts Class 0. The precision on positives stays moderate (0.65–0.67) across all thresholds because the model becomes more conservative, not more accurate — it learns to abstain rather than learn.

#### 5. The Confidence Cutoff Adds More Alpha Than the Threshold
Across every threshold, moving from 0.50 to 0.90 cutoff approximately doubles win rate and triples avg PnL. This is the dominant lever. The threshold choice primarily controls trade volume available at high cutoffs.

#### 6. Practical Recommendation
| Goal | Recommended Config |
|------|--------------------|
| Maximum precision per trade | Threshold 2.0, Cutoff 0.90 |
| Best precision + volume balance | Threshold 1.0, Cutoff 0.85 |
| Avoid | Threshold ≥ 4.0 — model quality degrades |

---

## Part 2 — Code Review: `train_ml_guard.py`

### Overall Assessment

The script is a solid, readable baseline trainer. It correctly applies stratified splitting, aligns PnL values with the split, and produces a useful trade efficiency report. However, for production use — especially for the XGBoost model — there are significant gaps in hyperparameter rigor, time-series correctness, probability calibration, and reproducibility.

---

### Critical Issues

#### C1. No Time-Series Aware Split — Data Leakage Risk
```python
# Current (WRONG for financial time-series data)
X_train, X_test, y_train, y_test, pnl_train, pnl_test = train_test_split(
    X, y, pnl_values, test_size=0.2, random_state=42, stratify=y
)
```
`train_test_split` with `random_state` shuffles rows randomly. For time-series data (trades), this leaks future information into training. A trade from 2025 can train the model, and a trade from 2024 ends up in the test set.

**Fix:** Use a chronological split. Sort by `date` first, then take the last 20% as test.
```python
df = df.sort_values('date').reset_index(drop=True)
split_idx = int(len(df) * 0.8)
train_df = df.iloc[:split_idx]
test_df  = df.iloc[split_idx:]
```
This is the single most important fix in the script.

---

#### C2. XGBoost `eval_metric` Passed but No Validation Set in `.fit()`
```python
# eval_metric='logloss' is set in constructor but never used
model.fit(X_train, y_train, sample_weight=sample_weights_train)
```
`eval_metric` and `early_stopping_rounds` only work when an `eval_set` is passed. Right now, `logloss` is silently ignored — `n_estimators=150` is a fixed cap with no stopping mechanism, which risks overfitting.

**Fix:**
```python
model.fit(
    X_train, y_train,
    sample_weight=sample_weights_train,
    eval_set=[(X_val, y_val)],
    verbose=False
)
```
Reserve 15–20% of training data as a validation set for early stopping.

---

#### C3. `scale_pos_weight` Not Used for XGBoost
The script applies `sample_weight` but XGBoost has a native, more efficient mechanism: `scale_pos_weight`. For a class ratio of 60:40 (Bad:Good), this should be:
```python
scale_pos_weight = sum(y_train == 0) / sum(y_train == 1)

XGBClassifier(
    scale_pos_weight=scale_pos_weight,
    # Remove sample_weight from .fit() call when using this
    ...
)
```
Using both `scale_pos_weight` and `sample_weight` simultaneously double-penalises the majority class.

---

### Important Issues

#### I1. Key XGBoost Hyperparameters Are Missing
The current config (`n_estimators=150, max_depth=7`) leaves significant performance on the table. These parameters should be tuned or at minimum set to sensible defaults:

```python
XGBClassifier(
    n_estimators=500,          # Higher ceiling; let early stopping decide
    max_depth=5,               # 7 is prone to overfit on financial data
    learning_rate=0.05,        # Default 0.3 is too aggressive
    subsample=0.8,             # Row subsampling — reduces overfit
    colsample_bytree=0.8,      # Feature subsampling per tree
    min_child_weight=5,        # Equivalent to min_samples_leaf for XGB
    reg_alpha=0.1,             # L1 regularisation
    reg_lambda=1.0,            # L2 regularisation (default, but be explicit)
    early_stopping_rounds=30,  # Stop when val logloss stops improving
    eval_metric='logloss',
    random_state=42,
    n_jobs=-1
)
```

---

#### I2. No Hyperparameter Search
The model is trained once with hand-tuned values. Given the threshold sensitivity observed in the stats, the optimal XGBoost config likely varies per threshold. Recommend adding an Optuna sweep or at minimum a `GridSearchCV` around the most impactful axes:

```python
import optuna
from sklearn.model_selection import cross_val_score

def objective(trial):
    params = {
        'max_depth': trial.suggest_int('max_depth', 3, 7),
        'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.2, log=True),
        'subsample': trial.suggest_float('subsample', 0.6, 1.0),
        'colsample_bytree': trial.suggest_float('colsample_bytree', 0.6, 1.0),
        'min_child_weight': trial.suggest_int('min_child_weight', 3, 15),
        'reg_alpha': trial.suggest_float('reg_alpha', 1e-4, 10.0, log=True),
    }
    model = XGBClassifier(**params, n_estimators=300, early_stopping_rounds=20)
    # Use TimeSeriesSplit here, not standard CV
    ...
```

---

#### I3. Misleading Comment on Class Weights
```python
# In this dataset, Bad Trades (0) are actually the majority.
# We still want to heavily penalize missing a Bad setup (False Positives)...
weights_dict = {0: 2, 1: 1}
```
The comment says "penalize missing a Bad setup (False Positives)" but the weight assignment (`class 0 = 2`) penalises misclassifying Bad trades as False Negatives from the perspective of class 0. The language conflates FP/FN across the two class perspectives. More importantly, the goal of the ML Guard appears to be maximising **precision on class 1** (Good Trades), which means the model should be conservative about predicting 1. The 2:1 weighting pushes in this direction but the comment should reflect that clearly.

---

#### I4. No Probability Calibration
XGBoost's raw `predict_proba` outputs are not calibrated probabilities — they are monotonic scores. The confidence cutoffs (0.5, 0.6, 0.7...) in `report_trade_efficiency` are compared against these raw scores. The actual meaning of "0.90 confidence" is not 90% probability of a Good Trade; it is an uncalibrated score.

**Fix:** Apply isotonic regression calibration post-training:
```python
from sklearn.calibration import CalibratedClassifierCV

calibrated = CalibratedClassifierCV(best_model, method='isotonic', cv='prefit')
calibrated.fit(X_val, y_val)
# Use calibrated.predict_proba() in report_trade_efficiency
```
This makes the confidence cutoffs interpretable and more portable across threshold regimes.

---

#### I5. No Feature Importance Output
The script trains the model and saves it but never logs which features drove predictions. For a financial ML guard, feature importance is essential for trust and debugging.

```python
import matplotlib.pyplot as plt

importances = pd.Series(
    best_model.feature_importances_, index=feature_cols
).sort_values(ascending=False)

print("\n--- Top 20 Feature Importances ---")
print(importances.head(20).to_string())

# Optional: SHAP values for local explainability
import shap
explainer = shap.TreeExplainer(best_model)
shap_values = explainer.shap_values(X_test[:500])
shap.summary_plot(shap_values, X_test[:500], feature_names=feature_cols)
```

---

### Minor Issues

#### M1. `fillna(0)` is Aggressive
```python
X = df[feature_cols].fillna(0)
```
Filling all NaNs with 0 is a reasonable starting point but can introduce significant distortion for ratio-based or normalised features where 0 is a meaningful value. At minimum, log the NaN fill counts:
```python
nan_counts = df[feature_cols].isna().sum()
nan_counts = nan_counts[nan_counts > 0]
if not nan_counts.empty:
    print(f"[Warning] Filling {nan_counts.sum()} NaN values with 0 across {len(nan_counts)} columns")
```
Consider per-column medians or a `SimpleImputer` with `strategy='median'` for financial features.

---

#### M2. `DecisionTree` and `RandomForest` Are Trained but Unused
The loop trains all three models but only XGBoost is saved and evaluated for trade efficiency. If Decision Tree and Random Forest are not part of the production pipeline, remove them to reduce training time and code noise. If they serve as diagnostic baselines, add their trade efficiency reports too.

---

#### M3. No Reproducibility Guard for Environment
The script uses `random_state=42` correctly, but XGBoost's GPU/CPU implementations can produce slightly different results across environments. Add a header log block:
```python
import xgboost as xgb
print(f"XGBoost version: {xgb.__version__}")
print(f"NumPy version: {np.__version__}")
print(f"Sklearn version: {sklearn.__version__}")
```

---

#### M4. Model Filename Does Not Encode Cutoff Recommendation
The saved model is named `model_xgb_{wl_tag}_{thresh_str}_{date_str}.joblib` but does not encode which confidence cutoff was selected. When loading the model downstream, an agent or trader needs to know which cutoff to apply. Consider saving the optimal cutoff alongside the model in a metadata JSON or inside the `model_data` dict:
```python
model_data = {
    'model': best_model,
    'feature_cols': feature_cols,
    'threshold': args.threshold,
    'recommended_cutoff': 0.85,   # Or derive programmatically from efficiency report
    'trained_on': date_str,
    'watchlist': wl_tag
}
```

---

### Summary Checklist for Next Iteration

| Priority | Fix |
|----------|-----|
| 🔴 Critical | Replace `train_test_split` with chronological split |
| 🔴 Critical | Pass `eval_set` to `.fit()` and enable `early_stopping_rounds` |
| 🔴 Critical | Use `scale_pos_weight` OR `sample_weight`, not both |
| 🟠 Important | Add `learning_rate`, `subsample`, `colsample_bytree`, `min_child_weight`, `reg_alpha` |
| 🟠 Important | Add probability calibration via `CalibratedClassifierCV` |
| 🟠 Important | Log feature importances / add SHAP summary |
| 🟠 Important | Add Optuna sweep for hyperparameter search |
| 🟡 Minor | Replace blanket `fillna(0)` with median imputation |
| 🟡 Minor | Remove unused Decision Tree / Random Forest or add their efficiency reports |
| 🟡 Minor | Store recommended cutoff in saved model metadata |
| 🟡 Minor | Add version logging for reproducibility |

---

*Review prepared by Claude Sonnet (Anthropic) — 2026-05-16. Intended for multi-agent review circulation.*
