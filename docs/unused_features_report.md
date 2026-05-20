# LFM Codebase Audit: Computed but Unused Features

This document identifies technical features and data columns that are calculated by the `DivergenceEngine` pipeline but are never utilized directly (in the UI or automated trade execution) or indirectly (as inputs to other used indicators).

## 1. High-Performance Candidates for Removal (0 Consumers)

These features are computationally expensive but their results are never read by any module, signal, or UI component.

- **`poc_n` [10, 30, 60, 120]** **`[COMPUTATIONALLY HEAVY]`**: midpoint of price bin with highest cumulative delivery.
    - **Logic**: `CompositeVWAP._compute_poc`. Uses TPO-style price binning and accumulation.
    - **Status**: Computed and stored in `df`, then dropped by `_DROP_COLS`. No consumers.
- **`va_high_n`, `va_low_n` [10, 30, 60, 120]** **`[COMPUTATIONALLY HEAVY]`**: Per-window Value Area boundaries.
    - **Logic**: `CompositeVWAP._compute_poc`. Uses iterative expansion loop from POC.
    - **Status**: Intermediates for `va_high`/`va_low`, but the per-window boundaries themselves are never used elsewhere.
- **`cwvap_slope` & `cwvap_slope_norm`** **`[MODERATELY HEAVY]`**: Linear regression slope of the composite VWAP.
    - **Logic**: `CompositeVWAP._compute_cwvap`. Uses `scipy.stats.linregress` inside a rolling window.
    - **Status**: Never used. Signals use `cts_slope` (savgol-filtered) instead.

## 2. Unused Adaptive Thresholds

The system calculates rolling percentile thresholds for many indicators, but most were superseded by the **Universal Cross** architecture which uses hardcoded triggers or lets the ML model handle normalization.

- **`psz_buy_threshold` / `psz_sell_threshold`** **`[MODERATELY HEAVY]`**: 10th/70th percentile of `price_slope_z`.
- **`rsz_buy_threshold` / `rsz_sell_threshold`** **`[MODERATELY HEAVY]`**: 10th/70th percentile of `rdv_slope_z`.
- **`prt_slope_buy_threshold` / `prt_slope_sell_threshold`** **`[MODERATELY HEAVY]`**: 10th/90th percentile of `prt_slope`.
- **`cts_slope_threshold`** **`[MODERATELY HEAVY]`**: Rolling percentile of `|cts_slope|`.
- **`psz_v_extreme_threshold`** **`[MODERATELY HEAVY]`**: 90th percentile of `|psz_v|`.

*Note: Rolling quantiles/percentiles require sorting the window at each bar, making them significantly more expensive than rolling means/sums.*


*Note: While some are listed in `chart.py`, they are not mapped to any display component in `divergence_engine.js`.*

## 3. Passive ML Features (No Mechanical/UI usage)

These columns are calculated and included in the ML training dataset (`dataset_dense_*.csv`), but they have NO mechanical usage in signal logic and NO visualization in the UI.

- **`accum_div` & `distrib_div`**: Wyckoff-style accumulation/distribution divergence.
- **`pdd_10` & `pdd_60`**: Price-Delivery Divergence for short and medium-long windows. (Only 30 and 120 are used).
- **`va_profile_width`**: Normalized width of the delivery-profile value area.
- **`cts_slope_trough`**: Boolean flag for Savgol-slope inflections.

## 4. Signal Metadata Intermediates

- **`soft_filters_passed`**: Integer count of secondary filters.
- **`rdv_pass`, `mcs_pass`, `cwc_pass`, `grad_pass`**: Boolean gate results.
    - **Status**: These are calculated in `DivergenceEngine.run()` but the Universal Cross path purely uses the ML Score. These legacy booleans are computed but ignored by the master path.

## 5. Recently Removed Features

These features were identified during the audit and have already been completely excised from the codebase.

- **`entry_signal_prob`**: A composite probability score (PSZ × PRT × CTS).
    - **Logic**: Previously in `DivergenceEngine.run()`.
    - **Status**: Removed from engine calculations, API payloads, and frontend UI.

---
**Coverage**: 100% of `src/divergence_engine` and `src/trading/signals`.
**Reviewer**: gemini-pro
