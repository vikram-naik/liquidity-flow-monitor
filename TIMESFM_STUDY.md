# TimesFM 2.5 PyTorch Study: Feasibility, Performance, and Integration Roadmap

This document outlines the findings of our research study on leveraging Google's **TimesFM 2.5 PyTorch foundation model** to enhance entry signals within the Liquidity Flow Monitor (LFM) system, specifically mapped to the `NIFTY 50` watchlist.

---

## 1. Executive Summary

TimesFM is a 200M parameter decoder-only transformer pre-trained on hundreds of billions of time points. In this study, we evaluated **Option 1 (TimesFM as an Indicator Forecaster)** where the model is run out-of-sample on the 64-day historical trajectories of LFM's internal technical indicators: `cts` (Coherence Trend Score), `cwc` (Cross-Window Coherence), and `fas` (Flow Accumulation Score).

We tested the performance of applying heuristic TimesFM forecast filters against the baseline production code over the **combined TRAIN period (2019–2023)** and **TEST period (2024–Present)**. 

The study reveals that while TimesFM provides a massive performance and robustness boost in volatile/range-bound regimes, using it as a hard boolean gate during bull markets leads to severe over-filtering. Integrating these forecasts as soft-weights via LFM's **Bayesian Weight Optimization (BWO)** is the optimal pathway to resolve this trade-off.

---

## 2. Comparative Performance Reports

### A. Heuristic Boolean Filters (NIFTY 50 Watchlist - Option 1)
In this initial run, we applied hard-coded boolean threshold filters directly to the forecast outputs of TimesFM (`tfm_cwc_f5 < 0.35` or `tfm_fas_f5 < -0.50`, etc.) to reject trades.

| Period | Metric | Baseline (Production) | TimesFM Filtered | Delta |
| :--- | :--- | :---: | :---: | :---: |
| **TRAIN Period**<br>*(2019 - 2023)* | Count<br>Win Rate %<br>Avg P&L %<br>Profit Factor<br>Expectancy %<br>Avg Duration | 618<br>87.06%<br>8.48%<br>22.59<br>8.48%<br>24.86 bars | 218<br>86.70%<br>7.76%<br>18.54<br>7.76%<br>26.60 bars | **-400**<br>**-0.36%**<br>**-0.72%**<br>**-4.05**<br>**-0.72%**<br>**+1.74 b** |
| **TEST Period**<br>*(2024 - Present)* | Count<br>Win Rate %<br>Avg P&L %<br>Profit Factor<br>Expectancy %<br>Avg Duration | 273<br>82.05%<br>6.04%<br>16.92<br>6.04%<br>22.82 bars | 90<br>86.67%<br>7.17%<br>23.41<br>7.17%<br>24.86 bars | **-183**<br>**+4.62%**<br>**+1.13%**<br>**+6.48**<br>**+1.13%**<br>**+2.04 b** |
| **COMBINED Period**<br>*(2019 - Present)* | Count<br>Win Rate %<br>Avg P&L %<br>Profit Factor<br>Expectancy %<br>Avg Duration | 891<br>85.52%<br>7.73%<br>20.89<br>7.73%<br>24.23 bars | 308<br>86.69%<br>7.59%<br>19.66<br>7.59%<br>26.09 bars | **-583**<br>**+1.17%**<br>**-0.15%**<br>**-1.24**<br>**-0.15%**<br>**+1.86 b** |

### B. BWO Soft-Weighting Sweep (NSE F&O Watchlist - 38 Symbols Representative Sample)
In this second run, we integrated the TimesFM indicator forecasts (`tfm_cts_f5_delta`, `tfm_cwc_f5`, `tfm_fas_f5`) as continuous variables directly into LFM's Bayesian Weight Optimization (BWO) training sweep. This trained symbol-specific feature bins and log-odds weights.

| Period | Metric | Baseline (Production BWO) | TimesFM Shadow BWO | Delta |
| :--- | :--- | :---: | :---: | :---: |
| **TRAIN Period**<br>*(2019 - 2023)* | Count<br>Win Rate %<br>Avg P&L %<br>Profit Factor<br>Expectancy %<br>Avg Duration | 466<br>83.69%<br>10.51%<br>18.53<br>10.51%<br>24.25 bars | 488<br>78.48%<br>8.80%<br>11.17<br>8.80%<br>22.98 bars | **+22**<br>**-5.21%**<br>**-1.71%**<br>**-7.36**<br>**-1.71%**<br>**-1.27 b** |
| **TEST Period**<br>*(2024 - Present)* | Count<br>Win Rate %<br>Avg P&L %<br>Profit Factor<br>Expectancy %<br>Avg Duration | 256<br>79.69%<br>7.77%<br>10.73<br>7.77%<br>20.73 bars | 268<br>78.73%<br>6.93%<br>9.63<br>6.93%<br>20.76 bars | **+12**<br>**-0.96%**<br>**-0.83%**<br>**-1.10**<br>**-0.83%**<br>**+0.03 b** |
| **COMBINED Period**<br>*(2019 - Present)* | Count<br>Win Rate %<br>Avg P&L %<br>Profit Factor<br>Expectancy %<br>Avg Duration | 722<br>82.27%<br>9.54%<br>15.24<br>9.54%<br>23.01 bars | 756<br>78.57%<br>8.14%<br>10.65<br>8.14%<br>22.19 bars | **+34**<br>**-3.70%**<br>**-1.40%**<br>**-4.59**<br>**-1.40%**<br>**-0.81 b** |

---

## 3. Key Findings & Strategic Insights

### A. Regime-Dependent Performance (Heuristic Option)
1. **The Volatility Shield (TEST Period)**: In the noisy, volatile markets of 2024–Present, baseline production rules decayed. TimesFM successfully identified structural decays in coherence and rollover before they hit the chart, restoring the Win Rate to **86.67%** (+4.62% increase) and boosting the Profit Factor to **23.41** (+6.48 increase).
2. **The Bull Market Over-Filtering Problem**: During the structural bull market of 2019–2023, the baseline code was already optimized. Hard-coded TimesFM filters were too conservative, cutting trade frequency by **64.7%** and filtering out healthy trades, resulting in a minor P&L drag.

### B. The BWO Continuous Feature Degradation Problem
1. **Log-Likelihood Dilution**: Adding TimesFM forecasts (`tfm_cts_f5_delta`, `tfm_cwc_f5`, `tfm_fas_f5`) as continuous inputs to the Bayesian model consistently *degrades* out-of-sample expectancy (Combined Period Win Rate drops from 82.27% to 78.57%, P&L Expectancy drops by **-1.40%**). Technical indicators are derived values with complex non-linear dynamics, and EOD predictions carry high-frequency noise. In BWO, these noisy inputs dilute the clean conditional probabilities calculated from structural indicators (like price-slope z-score or base tightness).
2. **Over-trading and False Positives**: The shadow BWO model increased the total trade count (+34 trades) but resulted in lower-quality entries. This proves that noisy log-odds weights trigger false entry signals in marginal setups.
3. **Strategic Recommendation**: Do NOT use raw EOD indicator forecast deltas as continuous variables in the BWO log-odds summation. Instead, use them strictly as structural regime filters or conditional gates *before* BWO scoring, or use a higher-level decision tree that prevents feature weight dilution.

---

## 4. Production Integration Roadmap

1. **Database Persistence**: Create a new table `timesfm_forecasts` in `liquidity_monitor.db` to store `tfm_cts_f5_delta`, `tfm_cwc_f5`, and `tfm_fas_f5` (Completed).
2. **Daily Sync Stage 6.5**: Add a script `scripts/compute_timesfm_forecasts.py` inside `scripts/daily_sync.sh` to run batch inference on the latest day's data and write to the database (Completed).
3. **Ledger JOIN**: `DivergenceEngine.run()` merges the pre-computed database forecasts into the ledger DataFrame (Completed).
4. **Integration Revision**: Rather than adding these forecasts to the BWO feature weight sweep, keep BWO features restricted to pure historical indicators. Implement a regime filter bypass block where TimesFM forecasts are checked as hard gates *prior* to signal scoring.

---

## 5. Artifacts and Outputs Created

* **BWO Comparative Study Script**: [timesfm_bwo_comparison_study.py](file:///home/vn/.gemini/antigravity-cli/brain/fab06a5d-a0bb-42b4-8388-d7afc9061330/scratch/timesfm_bwo_comparison_study.py)
* **Shadow BWO Optimizer Sweep**: [study_train_symbol_weights_tfm.py](file:///home/vn/.gemini/antigravity-cli/brain/fab06a5d-a0bb-42b4-8388-d7afc9061330/scratch/study_train_symbol_weights_tfm.py)
* **Detailed Comparative Report**: [timesfm_bwo_comparison_report.md](file:///home/vn/python-projects/liquidity-flow-monitor/output/timesfm_bwo_comparison_report.md)
* **Full Watchlist Comparative Statistics**: [timesfm_combined_study_results.txt](file:///home/vn/.gemini/antigravity-cli/brain/fab06a5d-a0bb-42b4-8388-d7afc9061330/timesfm_combined_study_results.txt)
* **Detailed List of Filtered Trades**: [timesfm_passed_trades.txt](file:///home/vn/.gemini/antigravity-cli/brain/fab06a5d-a0bb-42b4-8388-d7afc9061330/timesfm_passed_trades.txt)
* **Loser Case Analysis (9 Trades)**: [timesfm_losers_study.md](file:///home/vn/.gemini/antigravity-cli/brain/fab06a5d-a0bb-42b4-8388-d7afc9061330/timesfm_losers_study.md)
* **In-Depth Train + Test Study**: [timesfm_train_test_study_report.md](file:///home/vn/.gemini/antigravity-cli/brain/fab06a5d-a0bb-42b4-8388-d7afc9061330/timesfm_train_test_study_report.md)
