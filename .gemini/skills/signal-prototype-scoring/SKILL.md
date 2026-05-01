---
name: signal-prototype-scoring
description: Workflow for creating ad-hoc study scripts to prototype new signal logic and implementing multi-factor Bayesian-style scoring to optimize performance. Use this when asked to analyze a new signal idea, refine an entry setup, or improve win rates through holistic feature scoring.
---

# Signal Prototype and Scoring Workflow

This skill defines the process for prototyping new signal logic in isolated study scripts and evolving them into robust multi-factor scoring systems.

## Phase 1: Rapid Prototyping (Study Script)

When tasked with analyzing a new setup, create a standalone script in `scripts/study_<name>.py` to avoid polluting the core codebase.

### Step 1: Base Study Structure
Copy the structure from `scripts/nifty50_study.py`.
- **Universe:** Load "NIFTY 50" symbols using `get_watchlist_symbols()`.
- **Warmup:** ALWAYS load the entire history using `DivergenceEngine(ticker=symbol)` then filter the resulting `ledger` by the start date (e.g., `2024-01-01`). This ensures indicators like Savgol and EMA are warmed up.
- **Cycle Logic:** Iterate through the `records` (list of dicts). Identify an `entry_idx` (signal at `i`, entry at `i+1` open) and then search forward for an `exit_idx` based on hypothesized logic.

### Step 2: Data Extraction
Record key metrics for every setup found:
- `symbol`, `signal_date`, `entry_date`, `exit_date`
- `entry_price`, `exit_price`, `pnl_pct`
- `mfe_pct` (High/Entry - 1) and `mae_pct` (Low/Entry - 1)
- Values of candidate features at the `signal_date` (e.g., `psz_v`, `prt`, `cts_accel`).

## Phase 2: Winning vs. Losing Analysis

Once the study produces a CSV (in `output/`), analyze the distribution of features to find optimal filters.

### Step 1: Distribution Delta
Compare the median and percentiles of features for winners vs. losers.
```python
winners = df[df["pnl_pct"] > 0]
losers = df[df["pnl_pct"] <= 0]
print(winners[feature].describe())
print(losers[feature].describe())
```

### Step 2: Identify "Violence" and "Flatness"
- **Rubber Band Snapback:** Look for deep negative values of momentum (`psz_v` or `cts_accel`) leading into the floor. High-velocity "V-crashes" usually produce better bounces than slow "drifts."
- **Noise Penalty (The HINDUNILVR Guard):** Identify failed trades where a feature was flat or minuscule near zero (e.g., `std(psz_v over 3d) < 0.008`). These often represent "fake floors" or dead-cat bounces.

## Phase 3: Multi-Factor Scoring

If binary gates are too aggressive (e.g., they kill 10%+ winners), implement a holistic scoring model.

### Step 1: Base Score & Threshold
- **Base Score:** Start with `10.0`.
- **Pass Threshold:** Require `>= 9.0`.

### Step 2: Reward and Penalty Factors
Assign points based on feature buckets:
- **Structural Depth (PRT):** 
  - `prt <= -0.6`: +4.0
  - `prt > -0.2`: -5.0 (Severe penalty for shallow pullbacks)
- **Momentum Violence:**
  - `min(psz_v over 3d) <= -0.04`: +4.0
  - `min(psz_v over 3d) > -0.01`: -4.0 (Penalty for weak momentum)
- **Flatness Penalty:** 
  - `max(abs(psz_v)) < 0.025` AND `std(psz_v) < 0.008`: -5.0

### Step 3: Iterate
Adjust point values until the model filters out specific outliers (e.g., `HINDUNILVR 2024-10-31`) while preserving big winners. Use the study script to print the "Score" for every trade to verify.
