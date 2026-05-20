# XGBoost Hyperparameter Fine-Tuning Guide

**Author:** Gemma 4
**Date:** 16-May-2026

This document outlines the key hyperparameters for tuning the XGBoost classifier to optimize performance, generalization, and profitability in your trading model.

## 1. Understanding the Goal

The primary goal of tuning XGBoost is to find the optimal balance between **Bias** (underfitting) and **Variance** (overfitting). We aim to prevent the model from memorizing the training data while ensuring it captures the underlying signal that leads to profitable trades.

## 2. Core Hyperparameters for Tuning

The following parameters should be systematically explored using techniques like `GridSearchCV` or `RandomizedSearchCV`.

### A. Controlling Model Complexity

| Parameter | Description | Tuning Strategy | Impact |
| :--- | :--- | :--- | :--- |
| `max_depth` | The maximum depth of a tree. | Search range: `3` to `10`. | Controls model complexity. Deeper trees capture complex interactions but risk overfitting. |
| `min_samples_leaf` | The minimum number of samples required to be at a leaf node. | Search range: `3` to `10`. | Controls the granularity of the model. Higher values lead to a simpler, more generalized model. |

### B. Controlling Learning Rate and Iterations

| Parameter | Description | Tuning Strategy | Impact |
| :--- | :--- | :--- | :--- |
| `n_estimators` | The number of boosting rounds (trees) to build. | Increase from the base value (e.g., 150) to `300` or `500`. | More estimators generally improve performance up to a point, but too many can introduce noise. |
| `learning_rate` | Shrinks the contribution of each tree. | Start small (e.g., `0.01` or `0.1`). | A smaller rate requires a larger `n_estimators` but leads to more stable convergence. |

### C. Controlling Regularization (Preventing Overfitting)

These parameters help ensure the model generalizes well to unseen data.

| Parameter | Description | Tuning Strategy | Impact |
| :--- | :--- | :--- | :--- |
| `subsample` | Fraction of the training data randomly sampled for each tree. | Search range: `0.6` to `1.0`. | Introduces randomness, which acts as a strong regularization method. |
| `colsample_bytree` | Fraction of features (columns) randomly sampled for each tree. | Search range: `0.6` to `1.0`. | Reduces reliance on any single feature and improves generalization. |

### D. Handling Class Imbalance

Since your dataset is imbalanced (Class 0 vs. Class 1), handling this is critical for fair learning.

| Parameter | Description | Tuning Strategy | Impact |
| :--- | :--- | :--- | :--- |
| `scale_pos_weight` | The weight given to positive (Class 1) samples relative to negative (Class 0) samples. | Calculate as $\frac{\text{Count}(\text{Class 0})}{\text{Count}(\text{Class 1})}$. | Directly penalizes the model more heavily for misclassifying the minority class, improving recall for the positive class. |
| `class_weight` | A general way to assign weights to classes. | Experiment with `balanced` or the manual weights you used in the script. | Provides an alternative, high-level control over class imbalance. |

## 3. Recommended Tuning Workflow

1.  **Establish a Baseline:** Train the XGBoost model with the default parameters and the class weights defined in your script. Evaluate its performance on the **test set**.
2.  **Define the Search Space:** Create a parameter grid defining the ranges for `max_depth`, `learning_rate`, `n_estimators`, `subsample`, and `colsample_bytree`.
3.  **Implement Search:** Use `RandomizedSearchCV` over the defined grid. Randomized search is often more efficient than Grid Search for high-dimensional hyperparameter spaces.
4.  **Validation Metric:** Do not solely optimize for accuracy. Optimize based on a metric that aligns with your business objective, such as:
    *   **F1-Score (Macro Average):** For a balanced view of precision and recall.
    *   **Profitability:** The **Average PnL** derived from the `report_trade_efficiency` function, as this is the ultimate goal.
5.  **Select Best Model:** Choose the model configuration that yields the best performance on the validation set's primary business metric.

**In summary: Start simple, then increase complexity (depth/estimators) while strictly controlling for regularization (`subsample`/`colsample_bytree`) and using `scale_pos_weight` to manage the class imbalance.**
