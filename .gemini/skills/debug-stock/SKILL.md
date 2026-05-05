---
name: debug-stock
description: Specialized workflow for debugging a stock by extracting and analyzing its SavgolCTS indicators over a specific date range. Use this skill when investigating why an entry/exit signal fired or failed, or when checking the momentum and institutional metrics of a stock.
---

# Debug Stock Skill

This skill provides a standardized workflow for investigating the behavior of the SavgolCTS mean-reversion signals on a specific stock. It utilizes a custom script to dump the exact indicators used by the signal engines.

## 1. Extract the Data

Whenever asked to debug a stock, analyze its indicators, or understand why an entry/exit triggered on a specific date, **always start by running the feature extraction script** covering a window a few days before and after the date of interest:

```bash
./venv/bin/python scripts/debug_stock_features.py --symbol <SYMBOL> --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD>
```

*Note: The script safely loads the entire history to ensure all indicators (like EMA and Savitzky-Golay filters) are fully warmed up, strictly adhering to the project's data loading mandates.*

## 1.1 Surgical Scoring Analysis

For paths with complex multi-factor scoring (like `PRT_SLOPE_ZERO_CROSS` or `CTS_ACCEL_CROSS`), use the specialized scoring debug scripts to see the exact gate validations and point additions/deductions:

```bash
./venv/bin/python scripts/debug_<path_alias>_scoring.py --symbol <SYMBOL> --date <YYYY-MM-DD>
```

*Available scripts: `debug_prt_scoring.py`, `debug_cts_accel_scoring.py`, `debug_fas_scoring.py`.*

## 2. Core Indicators to Analyze

The output grid provides the exact features used by the `savgol_cts` entry and exit gates. Use this guide to interpret the data:

### Institutional Trend (CTS Group)
- **CTS**: Measures institutional accumulation/distribution (-1.0 to +1.0).
  - Deep floor exhaustion is typically `<= -0.98`.
  - Positive values indicate institutional buying.
- **CTS_Slope**: The rate of change of CTS. Negative slope = increasing distribution. A cross above zero indicates the trend of distribution is breaking.
- **CTS_Accel & Accel_Thr**: Measures the violence of the turn. `CTS_Accel > Accel_Thr` is a mandatory gate in many "engine start" setups (like Accel Cross or Slope Bottom).
- **CTS_BT / CTS_ST**: Dynamic boundaries. If `CTS <= CTS_BT`, price is in the institutional buy zone. An exit often triggers when `CTS` crosses down through `CTS_ST`.

### Price Momentum (PSZ Group)
- **PSZ (`price_slope_z`)**: Z-score of price momentum. Values `<= -0.30` show deep exhaustion.
- **PSZ_V**: Velocity of PSZ. **Crucial metric:** A rising `PSZ_V` (even if negative) shows deceleration of selling (the "rubber band snapping back"). Many entries require `PSZ_V > 0.0` or `PSZ_V > 0.01`.

### Thrust & Confluence
- **FAS**: Fast momentum engine. Values `<= -1.15` indicate extreme panic. Crosses above 0.0 or above its own thresholds signal strong directional thrust.
- **PRT_Slope**: Price Range Trend. Crosses above zero are used in the PRT Slope Zero Cross path.
- **CWVAP_Dist%**: Distance from the daily close to the Custom VWAP. Deep negative values (-3% to -8%) show extreme dislocation. Positive values mean price has reclaimed structural resistance.
- **CWC_Slope**: Cash Coherence Slope. Positive values indicate broad market alignment.

### Price Range Features (RP Group)
- **RP_10, RP_22, RP_63, RP_252 (`range_pos_n`)**: Normalized position within the high-low range over the last `n` trading days (0.0 = at the lowest low, 1.0 = at the highest high).
  - Used heavily in mean-reversion signals like Range Reversion. Values close to 0.0 (e.g., `< 0.15` or `< 0.25`) indicate price is compressed near the lows over that lookback window, showing oversold conditions. Values close to 1.0 indicate distribution/top risk and are often penalized.

## 3. Execution & Hypothesis

When debugging:
1. **Locate the target date** in the script output.
2. **Check the mandatory gates** for the relevant signal path (e.g., if debugging `Slope Bottom`, verify `CTS_Slope` was rising and `< -0.1`, and that `CTS_Accel > Accel_Thr`).
3. **Trace the exit state**: If investigating an exit, check if momentum faded (`PSZ` dropped below 0) or if the structural trend collapsed (`CTS` crossed down through `CTS_ST`).
4. Detail exactly which indicator failed the gate logic based on the script's output.