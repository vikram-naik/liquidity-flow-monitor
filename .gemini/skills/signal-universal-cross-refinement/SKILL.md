---
name: signal-universal-cross-refinement
description: Procedural guide for debugging and refining the Universal Cross (ML Master Path) entry and exit logic. Use when investigating missing/incorrect signals, adjusting scoring weights (awards/penalties), or optimizing the single-funnel funnel.
---

# Signal Universal Cross Refinement

This skill provides the procedural standard for working with the LFM's consolidated "Universal Cross" funnel.

## 1. Scoring & Award Standards

The Universal Cross path relies on an XGBoost ML Guard supplemented by manual adjustments (awards and penalties).

- **Universal Award Unit**: All manual score adjustments MUST use the `award` unit, defined as `ml_guard_threshold * 0.3`.
  - Typical `ml_guard_threshold`: `1.55` (Predicted PnL %).
  - Typical `award`: `~0.465`.
- **Consistency**: Do not use hard-coded floats for new adjustments; always derive them from the `award` variable to ensure they scale with the database-configured threshold.

## 2. Adaptive Flatness Calibration

The `is_flattish_line_adaptive` utility is used to reward consolidations or penalize "lazy" momentum.

- **Lookback Requirement**: Always use a **30-bar** (approx. 1.5 months) environment lookback window for the `lookback_window_data` parameter. 
- **Failure Mode**: Using a small window (e.g., 5 bars) causes the adaptive tolerance to collapse to near-zero, making the check fail even for visually flat lines.
- **Sensitivity**: Use `0.05` (5%) for strict flatness or `0.10` for broader consolidations.

## 3. Debugging Workflow (Entry)

When a signal is missing or rejected unexpectedly:
1. **Run Telemetry**: 
   ```bash
   ./venv/bin/python scripts/debug_universal_scoring.py --symbol <SYMBOL> --date <YYYY-MM-DD>
   ```
2. **Analyze Output**:
   - Check `UC - Flattish Line Check` results (dynamic tolerance vs spread).
   - Review the `adjustments` dictionary to see which awards/penalties were applied.
   - Compare `Confidence` (Final Score) against `ml_guard_threshold`.
3. **Verify Trends**: Ensure `idx` is sufficient (>= 2 or 3) for trend checks like `cts_accel` or `psz_v`.

## 4. Exit Guard Standards

The Universal Cross exit logic uses specialized "Loss Prevention" guards that override the standard CTS trailing logic.

- **Gap Down Loss Guard**: Triggers if `high < prev_low` AND `pnl_pct < 0`. This is a hard exit to prevent staying in "falling knives."
- **Negative PnL Timeout**: Triggers if `bars_held >= 15` AND `pnl_pct < 0`. Prevents capital lockup in anemic setups.
- **CWVAP Lost**: This guard is **DISABLED** by default for Universal Cross to allow holding through structural noise.

## 5. Empirical Impact Analysis (Penalty Simulation)

Before implementing a logic change (award/penalty) in the core `universal_cross.py`, conduct an empirical study to validate its impact.

1. **Verify Grounding Example**: Confirm the failure case (e.g., a loser that should be rejected) using `debug_universal_scoring.py` or a surgical check script.
2. **Scaffold Study**: Create an ad-hoc script (e.g., `scripts/study_fas_positive.py`) based on the `signal-study-lifecycle` pattern.
3. **Simulate Penalty**:
   - Calculate the `award` unit (`threshold * 0.3`).
   - For every signal, check if the proposed condition (e.g., `fas > 0`) is met.
   - Calculate a `sim_score` = `original_score - award`.
   - Mark as `passes_penalized` if `sim_score >= threshold`.
4. **Delta Reporting**: Report the impact using the "Before vs After" grid, focusing on the delta in Win Rate and Profit Factor.
5. **Outlier Deep-Dive**: Print a table of rejected trades (losers avoided) and missed trades (winners lost) to ensure the logic is surgically targetting the intended failure mode.

## 6. Verification Checklist
- [ ] Round all adjustments to 4 decimal places (`round(adj, 4)`).
- [ ] Use `pytest.approx()` when asserting scores in unit tests.
- [ ] Ensure all trigger columns are explicitly added to `row_dict` before scoring.
- [ ] **Timestamp Landmine**: When filtering by date in pandas, ensure both the column and the search value are either both `str` or both `pd.Timestamp`.
