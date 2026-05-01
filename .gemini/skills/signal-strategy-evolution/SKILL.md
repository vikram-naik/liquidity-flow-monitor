---
name: signal-strategy-evolution
description: Advanced workflow for identifying and fixing systemic weaknesses in signal paths using outlier analysis, ML-aided threshold discovery, and multi-factor scoring. Use when a path is consistently underperforming or giving back profits.
---

# Signal Strategy Evolution Skill

This skill provides a high-fidelity workflow for refining trading signals that have moved beyond simple prototyping and require architectural "hardening" to handle market noise and edge cases.

## 1. Outlier Metric Isolation
When a signal path produces "poor setups" (false positives) or "hard stops" despite matching basic rules:

1. **Identify Offenders:** Use `scripts/dump_trades.py --entry <tag> --period test` to find symbols/dates of significant losses.
2. **Dump Deep Metrics:** Create or use `scripts/study_single_row.py` to print every available column for that trade's signal bar (the bar *before* the entry date).
3. **Compare Groups:** Construct a dataset of signal-bar metrics for "Hard Stops" vs. "Good Trades" (PnL >= 4%).

## 2. ML-Aided Threshold Discovery
If standard indicators (CTS, PSZ) don't cleanly separate winners from losers, use a Decision Tree to find candidate splits:

```python
# Prototype split finder (scripts/find_filter.py)
from sklearn.tree import DecisionTreeClassifier, export_text
X = df[feature_cols]
y = df['is_good'] # 1 if PnL >= 4%, else 0
clf = DecisionTreeClassifier(max_depth=2, class_weight='balanced')
clf.fit(X, y)
print(export_text(clf, feature_names=feature_cols))
```

## 3. Geometric Feature Engineering
When indicator crossovers are too slow, engineer geometric guards from raw OHLC data:

- **Wick Rejection:** `(High - max(Open, Close)) / (High - Low)`. A ratio > 0.65 indicates a blow-off top or resistance rejection.
- **IBS (Internal Bar Strength):** `(Close - Low) / (High - Low)`. A ratio < 0.15 indicates a "Total Collapse" close near the low.
- **Inside Bar Climax:** `High <= Prev_High` AND `Low >= Prev_Low` on extreme volume (> 1.5x 20-day average).

## 4. Multi-Factor Hardening (Bayesian Scoring)
Transition from fragile binary "Gates" to holistic scoring in `src/trading/signals/savgol_cts/scoring.py`:

1. **Define a Base Score** (e.g., 10.0 or 20.0).
2. **Apply Progressive Boosts:** (e.g., `+4` for deep `prt <= -0.6`, `+2` for `psz_v` acceleration).
3. **Apply Severe Penalties:** 
   - **Shallow Penalty:** `-5` if the structural trough isn't deep enough.
   - **Flatness Penalty:** `-5` if velocity standard deviation is < 0.008 (dead-cat bounce guard).
   - **Thrust Penalty:** `-4` if `cts_accel < cts_accel_threshold`.

## 5. Universal Guard Augmentation
Update the global `cwvap_guard.py` to apply new safety rules (like Candle Rejection) selectively:

- **Scope Check:** Ensure rejections only fire when `Price > CWVAP`.
- **Targeted Rollout:** Use the `tag` argument in `apply_cwvap_guard` to enable new guards for specific paths (e.g., `fas-buy-cross`) before global rollout.

## 6. Full Lifecycle Sync
Every evolution MUST conclude with a documentation and tooling sync:
- **`config.py`:** Update dataclasses with new thresholds.
- **`SIGNAL_FLOW.md`:** Update the architectural diagram with new gates/scores.
- **`dump_trades.py`:** Add new `ENTRY_ALIASES` and report columns (e.g., `CWF`, `>VAH`) to maintain investigative visibility.
