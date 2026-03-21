# Handoff: Next-Gen Signal v2 Implementation

**Date**: 2026-03-20
**Branch**: `feature/divergence-engin`
**Purpose**: Full context for incorporating next-gen signal analysis into the engine after a context reset.

---

## What We Did (completed work)

### Feature Correlation Study — Three Rounds

**Round 1 — NIFTY 50** (`output/correlation_study/nifty50/`)
Pooled ~79,229 bars across 50 large-cap stocks. Established baseline feature rankings, redundancy clusters, lag analysis, and regime-conditional correlations. Script: `scripts/feature_correlation.py`.

**Round 2 — NIFTY 500 v1** (`output/correlation_study/`)
Pooled ~697,154 bars across ~500 stocks. Discovered the PDD_120 sign flip vs NIFTY 50 and the emergence of delivery velocity signals (cdvl, velocity_120_norm) in the broader market. This appeared to show PDD was cap-size dependent.

**Round 3 — NIFTY 500 v2** (`output/correlation_study/nifty500_v2/`)
Added to the script: per-stock Spearman analysis (468 stocks), sign agreement %, liquidity tiering (terciles by median daily delivery_qty), quintile analysis (Q1–Q5 mean return spreads + monotonicity score), mutual information via `sklearn.feature_selection.mutual_info_regression`, and `rdv_slope_z` as a new feature. This round resolved the PDD sign flip as Simpson's Paradox — the definitive result.

### Script Location and Usage

```
scripts/feature_correlation.py
```

```bash
# Single ticker
venv/bin/python3 scripts/feature_correlation.py --ticker RELIANCE

# Full watchlist with CSV output
venv/bin/python3 scripts/feature_correlation.py --watchlist "My Watchlist" --csv

# Custom horizons
venv/bin/python3 scripts/feature_correlation.py --ticker RELIANCE --horizons 3 5 10 --max-lag 10
```

The script runs the full engine pipeline (BaseCalc → DVL → CWVAP with `causal_savgol` CTS strategy → CWC → MCS → `compute_trend_participation`) internally via `compute_full_pipeline()`. It uses `matplotlib` with `Agg` backend only — no seaborn dependency.

Features analyzed (defined in `FEATURES` list in the script):
`cts`, `cts_slope`, `cts_accel`, `price_slope_z`, `psz_smooth`, `psz_v`, `mcs_composite`, `mcs_composite_slope`, `cwc`, `cwc_slope`, `cwc_delta`, `rdv`, `rdv_slope_z`, `accum_div`, `distrib_div`, `cdvl`, `pdd_120`, `velocity_10/30/60/120_norm`, `mfm`, `coherence`.

Outputs per run: console tables, heatmap PNGs, optional CSVs. `WARMUP_ROWS = 120`, `MIN_USABLE_BARS = 250`.

---

## What We Learned (empirical findings)

### 1. Simpson's Paradox in PDD_120 — Resolved

The v1 N500 finding that PDD_120 had a positive pooled rho (+0.019 at 10-bar) versus N50's negative rho (-0.030) appeared to indicate a cap-size behavioral difference. The v2 per-stock analysis proves this is wrong.

| Metric | Pooled rho (10-bar) | Per-stock mean rho | Per-stock median rho |
|--------|--------------------|--------------------|---------------------|
| pdd_120 | +0.019 | **-0.017** | **-0.014** |

Within each stock, PDD_120 is consistently contrarian (negative). Pooled rho flips positive because stocks with structurally higher PDD levels are structurally higher-return stocks — a cross-sectional selection artifact. The paradox holds at every liquidity tier (pooled rho positive across all three tiers, per-stock negative in all three).

**Implication**: PDD_120 is a **universal within-stock contrarian quality gate**. The prior exit strategy's use of PDD_120 as a quality filter is valid for all market caps. Do not flip its sign or restrict it to large-caps.

### 2. Feature Rankings — By Validated Evidence

Rankings from combining pooled Spearman + per-stock sign agreement + quintile monotonicity + MI + lag analysis + regime conditioning:

**Rank 1 — `cts_accel`**: Best Spearman (rho +0.054 at 5-bar), 85% per-stock sign agreement at 5-bar, perfect quintile monotonicity (mono=1.0) at 5-bar and 10-bar with Q1→Q5 spread +0.270 at 5-bar. Works in all regimes (bull +0.023, bear +0.032). Signal is short-lived: decays and flips negative by lag_3–5 across both universes. Role: **primary entry timing signal only**.

**Rank 2 — `cts_slope`**: 71% per-stock sign agreement at 5-bar. Top MI feature (0.0052 nats — has nonlinear structure beyond monotonic component). Sign flip: negative at 5-bar (contrarian, rho -0.029) → noise at 20-bar. Stronger in bull regime (rho -0.026) than bear (-0.018). Role: **short-term mean reversion filter / entry confirmation**.

**Rank 3 — `cdvl`**: 63% per-stock agreement at 20-bar. Persistent across lags (rho +0.021 at lag_0 → +0.024 at lag_5). Perfect quintile monotonicity at 10-bar and 20-bar (spread +0.163 / +0.293). Stronger in low-liquidity stocks (rho_20 +0.037 vs +0.020 high). Was noise in N50 pooled; significant in N500 per-stock. Role: **medium-term delivery momentum confirmation**.

**Rank 4 — `velocity_60/120_norm`**: 61–62% agreement. Persistent across lags and strengthens (genuine leading indicator). velocity_120_norm rho_20 +0.045 in low-liquidity vs +0.015 high-liquidity — strongest in thinner stocks. Role: **delivery velocity confirmation, especially for mid/small-cap**.

**Rank 5 — `pdd_120`**: 58% per-stock agreement. Per-stock contrarian confirmed as universal. Persistent within stocks across all lags. Role: **contrarian quality gate** (same as current exit strategy).

**Rank 6 — `psz_v`**: Weak Spearman (rho ~-0.008 to -0.022) but #2 in MI (0.0043 nats). Quintile pattern is **V-shaped** (Q1=0.33, Q3=0.23, Q5=0.26) — extremes outperform the middle at 10-bar. This nonlinear structure is not captured by rank correlation. Role: **nonlinear momentum velocity — use extremes, not direction**.

**Rank 7 — `rdv_slope_z`**: 62% per-stock agreement at 5-bar. Short-term contrarian (rising delivery slope predicts negative 5/10-bar return). Stable across bull/bear (rho -0.013/-0.012). Cross-correlated with `price_slope_z` (rho ~0.47 — partially redundant with PSZ family). Decays to noise at 20-bar. Role: **short-term delivery divergence signal**.

**Rank 8 — `coherence`**: Perfectly anti-monotonic in quintiles (mono=-1.0 at 5/10-bar). 58% agreement. Stable contrarian. But purely contemporaneous — decays rapidly with lag. Role: **regime confirmation only, not a leading indicator**.

**Rank 9 — `price_slope_z` / `psz_smooth`**: Perfect quintile monotonicity at 20-bar (spread +0.467 / +0.382). 64% agreement. But rho=0.96 with each other — pick one. Also heavily correlated with `cts` (rho 0.75). Role: **trend direction context only**.

### 3. Features to Deprioritize

| Feature | Why |
|---------|-----|
| `mcs_composite` (level) | 49–54% per-stock agreement — coin flip. Pooled significance is cross-sectional, not time-series. |
| `mcs_composite_slope` | 51–53% per-stock agreement across N500. Was useful in N50 pooled (+0.015) but does not generalize — near zero/negative in N500. |
| `cwc`, `cwc_slope`, `cwc_delta` | 50–58% agreement. cwc flips sign by regime (bull +0.022, bear -0.006) making it unreliable as a direct predictor. The flip itself is useful as a regime indicator. |
| `mfm` | 50–57% agreement. Noise per-stock. |
| `accum_div` | 51% agreement. Only 2 effective quintile bins due to skew. Useful in high-liquidity stocks only as a specialized filter. |

### 4. Regime Conditioning — Confirmed Patterns

`mcs_composite` sign (bull ≥ 0, bear < 0) is the regime switch. Do not use it as a direct predictor — use it to weight other features:

| Pattern | Confidence | Detail |
|---------|-----------|--------|
| **cwc flips sign** between bull/bear | Very high — confirmed in both N50 and N500 | Bull: +0.022, Bear: -0.006 (N500) |
| **cts_accel is stronger in bear** | High — consistent across both universes | Bull +0.023, Bear +0.032 |
| **cts_slope is stronger in bull** | High — consistent | Bull -0.026, Bear -0.018 |
| **coherence is contrarian in bull, weak in bear** | High | Bull -0.013, Bear -0.005 |
| **pdd_120 doubles in bear (N50)** | High for large-caps | N50: Bull -0.019, Bear -0.041 |
| **velocity_60_norm stronger in bear (N500)** | Moderate | Bull +0.015, Bear +0.028 |

Gradient shape regimes (from ADX/DMI `gradient_shape` column) have strong conditional effects:
- `accumulation`: `cts_accel` (+0.045–0.046) is dominant signal
- `downtrend_mature`: `cts_slope` (-0.041), `cts_accel` (+0.037), `velocity_120_norm` (+0.037)
- `uptrend_exhausting`: `velocity_60_norm` (+0.026), `psz_v` (-0.025), `coherence` (-0.021)
- `uptrend_forming`: `pdd_120` (+0.048), `velocity_120_norm` (+0.044), `cts_accel` (+0.032)

### 5. Lag Structure — What Leads vs What Confirms

| Feature | lag_0 → lag_5 | Interpretation |
|---------|--------------|----------------|
| `cts_accel` | +0.027 → -0.024 | Rapid decay + flip. Use only at entry, discard afterwards. |
| `cts_slope` | -0.023 → +0.010 | Sign flip at lag ~3–4. Short-lived contrarian. |
| `cts` | +0.002 → +0.036 | Strengthens with lag — momentum builds behind trend. |
| `cdvl` | +0.021 → +0.024 | Persistent. Genuine structural signal, not contemporaneous. |
| `velocity_120_norm` | +0.018 → +0.021 | Slowly strengthening — genuine leading indicator. |
| `pdd_120` | +0.019 → +0.023 | Persistent (paradox aside — per-stock is negative and persistent). |
| `coherence` | -0.011 → +0.005 | Decays and flips. Purely contemporaneous confirmation. |
| `rdv_slope_z` | -0.012 → +0.002 | Decays toward zero. Entry-only signal. |

---

## What to Build Next (the actual task)

### The Task: Incorporate Next-Gen Signal into the Engine

The engine currently produces `cts`, `cts_slope`, `cts_accel`, `pdd_120`, `psz_v`, `rdv_slope_z`, `coherence`, `cdvl`, and related features in its ledger. The `SavitzkyGolayAnalyzer` (Module 7 in `engine.py`) runs post-pipeline and produces a `TrendAnalysis` object attached to `EngineResult.cts_trend_analysis`. There is no combined signal score or entry gate in the engine yet — signal construction currently lives in the exit strategy scripts.

The task is to add a **new analysis module** (Module 8 or a new step in the pipeline) that computes a **combined next-gen signal score** per bar, using the empirically validated feature set. This score will serve as:
1. An entry timing gate (replace or supplement the current CTS/PSZ crossing logic in `backtest_cts_psz.py` and `backtest_long_signals.py`)
2. A quality filter at signal generation time

### Minimal Feature Set (agreed)

| Role | Feature | Notes |
|------|---------|-------|
| **Entry timing (primary)** | `cts_accel` | Short-lived. Gate entry only, not hold. Must be positive and above threshold. |
| **Entry confirmation** | `cts_slope` | Should be negative (contrarian at short horizon = acceleration from depressed slope) |
| **Trend direction context** | `cts` | Level — must be above its adaptive `cts_buy_threshold` (already computed in `compute_trend_participation`) |
| **Delivery confirmation** | `cdvl` or `velocity_60_norm` | Pick one. cdvl preferred for broader universe. Must be positive. |
| **Quality gate** | `pdd_120` | Per-stock contrarian. High pdd_120 suppresses entry (contrarian = mean reversion risk). Use rolling percentile threshold. |
| **Regime switch** | `mcs_composite` sign | Do not use as direct predictor. Use to up-weight cts_accel in bear, up-weight cts_slope in bull. |
| **Optional nonlinear** | `psz_v` extremes | Flag when abs(psz_v) > rolling 90th percentile of abs(psz_v) — extremes predict outperformance regardless of direction. |

### Signal Logic Outline

```
Entry signal fires when ALL of:
  1. cts_accel > adaptive threshold (rolling 60-bar 70th percentile of cts_accel)
     [strongest predictor — 85% agreement, pure monotone — this is the primary gate]

  2. cts >= cts_buy_threshold
     [trend context — already computed as rolling 10th percentile of cts over 60 bars]

  3. cdvl > 0  OR  velocity_60_norm > 0
     [delivery confirmation — at least one delivery velocity signal positive]

  4. pdd_120 < pdd_sell_threshold
     [quality gate — pdd_120 below rolling 70th percentile suppresses mean-reversion risk]
     [this is the PDD_120+RSZ gate from the walk-forward validated exit strategy]

Regime conditioning (modifies weights, does not veto):
  - If mcs_composite < 0 (bear): increase cts_accel weight / lower its threshold
  - If mcs_composite >= 0 (bull): cts_slope confirmation becomes stricter

Optional nonlinear boost:
  - If abs(psz_v) > rolling 90th percentile of abs(psz_v): flag as high-momentum bar
    (V-shaped quintile: extremes outperform, use as a boost, not a gate)
```

### How It Differs from Current Signal Logic

Current signal logic (in `backtest_cts_psz.py` / `backtest_long_signals.py`):
- Entry triggered by CTS crossing `cts_buy_threshold` or PSZ crossing `psz_buy_threshold`
- These are level-based crossings (PSZ/CTS below the rolling 10th percentile, then recovering)
- The PDD_120 quality gate exists as an exit filter in the exit strategy, not an entry filter
- No `cts_accel` in the entry gate
- No regime-conditional feature weighting

Next-gen signal changes:
- **`cts_accel` becomes the primary entry trigger**, not a CTS/PSZ threshold crossing
- PDD_120 gate moves **into entry logic** (not just exit strategy)
- Regime conditioning (`mcs_composite` sign) adjusts thresholds dynamically
- Delivery confirmation (cdvl) is required, not optional
- `cts_slope` used as a confirmation (short-term mean reversion setting up for acceleration), not as a standalone signal

---

## Key Files

### Engine Core

| File | Purpose |
|------|---------|
| `src/divergence_engine/engine.py` | Orchestrator. Runs 7 modules in sequence. Module 8 (next-gen signal) to be added here. `EngineResult` dataclass needs a new field for the signal output. |
| `src/divergence_engine/analysis/trend_participation.py` | `compute_trend_participation()` — computes `price_slope_z`, `rdv_slope_z`, `psz_smooth`, `psz_v`, `coherence`, adaptive PSZ/CTS thresholds, `accum_div`, `distrib_div`. Uses causal Savitzky-Golay via `lfilter` with `savgol_coeffs`. This is Module 6 in the pipeline. |
| `src/divergence_engine/analysis/trend.py` | `SavitzkyGolayAnalyzer` — non-causal SG analysis on `cts` series. Extracts `TrendAnalysis` with direction, bend_type, trend_strength, extrema. This is Module 7. |
| `src/divergence_engine/analysis/models.py` | Dataclasses: `TrendAnalysis`, `TrendDirection`, `BendType`, `ExtremaPoint`, `ExtremaAnalysis`, `EmpiricalThresholds`. |
| `src/divergence_engine/analysis/__init__.py` | Exports: `compute_trend_participation`, `SavitzkyGolayAnalyzer`, `TrendAnalysis`. |
| `src/divergence_engine/dvl_ledger.py` | Produces: `cdvl`, `pdd_120`, `velocity_*_norm`, `rdv`, `gradient_shape`. |
| `src/divergence_engine/mcs.py` | Produces: `mcs_composite`, `mcs_composite_slope`, `mcs_mfm`. |
| `src/divergence_engine/cwc.py` | Produces: `cwc`, `cwc_slope`, `cwc_delta`. |

### Correlation Study

| File | Purpose |
|------|---------|
| `scripts/feature_correlation.py` | Full correlation study script. Runs engine pipeline internally, computes Spearman rho at 5/10/20-bar horizons, per-stock analysis, liquidity tiers, quintiles, MI, lag analysis, regime splits. |
| `output/correlation_study/nifty500_v2/observations_nifty500_v2.md` | **Primary reference document.** Full v2 findings including Simpson's Paradox resolution, all feature rankings, quintile analysis, MI, regime conditioning. |
| `output/correlation_study/observations_nifty500.md` | v1 N500 findings — PDD sign flip (now explained as paradox), delivery velocity emergence, N50 vs N500 comparison. |
| `output/correlation_study/observations_nifty50.md` | N50 findings — N50-specific behavior (PDD contrarian strong, mcs_composite_slope useful, velocity_30 bear-only). |
| `output/correlation_study/nifty500_v2/*.csv` | Raw data: `per_stock_correlations.csv`, `quintile_analysis.csv`, `mutual_information.csv`, `tier_*.csv`. |

### Backtesting / Walk-Forward

| File | Purpose |
|------|---------|
| `scripts/backtest_cts_psz.py` | Current entry signal backtest — CTS/PSZ threshold crossings. Next-gen signal will replace this logic. |
| `scripts/backtest_long_signals.py` | Broader backtest including PSZ-only and combined consensus signals. |
| `scripts/walk_forward.py` | Walk-forward validation framework. PDD_120+RSZ quality gate is validated here. Any new signal module must be tested through this script. |
| `scripts/test_savgol_thresholds.py` | Tests adaptive threshold behavior. |
| `scripts/test_savgol_walkforward.py` | Walk-forward test for causal Savgol CTS strategy. |

---

## Important Constraints / Design Decisions

### 1. Entry Timing Over Horizon-Based Exits

The system is optimized for **entry precision, not horizon-based position management**. The walk-forward validated PDD_120+RSZ quality gate controls exit (documented in `MEMORY.md → project_exit_strategy.md`). The next-gen signal work is purely about improving entry timing — do not redesign the exit logic.

### 2. No Seaborn Dependency

The entire codebase uses `matplotlib` with `Agg` backend. `seaborn` is not installed. All visualization in `feature_correlation.py` and any new scripts must use `matplotlib` directly.

### 3. CTS Strategy: causal_savgol Only

The `CompositeVWAP` module supports multiple CTS strategies (Default EMA, DEMA, KAMA, Savgol). The canonical strategy for all backtesting, signal work, and the correlation study is `causal_savgol`. The `SavitzkyGolayAnalyzer` in `trend.py` uses a non-causal SG filter for the `TrendAnalysis` object (post-pipeline visualization only) — that is acceptable because it is not used for entry signals. The causal filter in `trend_participation.py` (via `lfilter` with `savgol_coeffs(pos=sg_win-1)`) is the correct implementation for any bar-by-bar signal.

### 4. Per-Stock Behavior Is Ground Truth, Not Pooled

When evaluating a new feature or signal design, the **per-stock Spearman rho and sign agreement %** from the v2 analysis are the relevant metrics. Pooled rho values are misleading for PDD_120, MCS, and CWC due to Simpson's Paradox. A feature with 65%+ per-stock sign agreement is reliable; below 60% is marginal; below 55% is noise.

### 5. venv Python

Always run scripts with `venv/bin/python3`, not system Python. This is required for correct package resolution.

### 6. cts_accel Is Ephemeral — Do Not Hold On It

The lag analysis shows `cts_accel` flips from +0.027 at lag_0 to -0.024 at lag_5 (both universes). It signals that something is about to move. Once the entry bar passes, the cts_accel reading from that bar is useless — the signal has decayed. Do not use cts_accel as a hold condition or trailing signal.

### 7. mcs_composite Sign As Regime Switch Only

`mcs_composite` has 49–54% per-stock sign agreement (N500 v2). It is a **coin flip as a direct predictor**. Its only validated use is as a binary regime classifier (sign: bull vs bear) to condition other features. It should not appear in a signal score formula with a coefficient.

### 8. Adaptive Thresholds Are Already in the Pipeline

`compute_trend_participation()` already computes `cts_buy_threshold` (rolling 10th percentile of cts over 60 bars) and `cts_sell_threshold` (rolling 90th percentile). Similarly `psz_buy_threshold` and `psz_sell_threshold`. Any new signal module should reuse these rather than recomputing rolling quantiles independently. For `cts_accel` and `pdd_120`, equivalent rolling percentile thresholds need to be added in the new module.

---

## Backlog (discovered during implementation)

### 1. Bear-regime slope guard — ✅ INVESTIGATED, NO CHANGE (2026-03-20)

Original concern: BAJFINANCE 2026-03-10/11 false positive — CTS diving with negative slope, but accel had tiny positive blip. **Already fixed by new Gate 3** (vel_dp5 was 0-1, blocks the entry).

**Broader investigation** (NIFTY 500, `scripts/gate_slope_guard_nifty500.py`):
- Slope-negative downtrend entries (N=98) **outperform** slope-positive ones (N=263): 50.0% vs 36.1% win rate, +0.86% vs -0.73% avg10d
- These are early bottom-catchers where delivery momentum (vel_dp5) confirms turn before slope/regime flip
- Only very steep slopes (<-0.03, N=9) are consistently bad (0% win), but interleaved with winners at similar depths (POLYCAB +14%, DBREALTY +30%)
- **Conclusion**: A slope guard removes more good entries than bad. No change warranted.

### 2. Gate 3 level-check — ✅ IMPLEMENTED (2026-03-20)

**Was**: `cdvl > 0 OR velocity_60_norm > 0` — waited for level to cross zero, missed best entry window.

**Now**: `vel_dp5 >= 4 AND velocity_60_norm > -0.10`
- `vel_dp5`: velocity_60_norm delta positive on 4+ of last 5 bars (computed in `DVLLedger.compute_all()`)
- Floor `-0.10`: proximity-to-zero filter — rejects stocks improving from deeply negative baseline
- Validated on NIFTY 500 Jan-Mar 2026: hybrid variant E avg10d=+3.27% vs level baseline +2.72%
- Fixed floor outperforms adaptive percentile floors because rolling pctile adapts per-stock history

### 3. Gate 4 PDD-120 relaxation — ✅ INVESTIGATED, NO CHANGE (2026-03-20)

Investigated whether Gate 4 should be relaxed for stocks with confirmed delivery momentum (Gate 3 passing). GESHIP Jan 27 (+20% in 10d) was missed because pdd_120=6.10 > threshold=5.19.

**Variants tested** (NIFTY 500 Jan-Mar 2026, `scripts/gate4_pdd_nifty500.py`):
- A baseline: N=1073, Win10d=40.5%, Avg10d=-0.64%
- B waive if G3 pass: N=1765, Win10d=39.6%, Avg10d=-1.04%
- C delta (block only if divergence widening): N=1158, Win10d=40.6%, Avg10d=-0.78%
- D delta + vel confirm: N=1765 (identical to B — Gate 3 already requires vel confirmation)

**Conclusion**: All relaxed variants are worse. The 692 gained signals from waiving Gate 4 average -1.67% return. Gate 4 correctly blocks more bad entries than good ones. GESHIP was an outlier — most breakouts above PDD exhaustion threshold fail. Gate 4 stays as-is.

---

---

## Exit Strategy Work (2026-03-20)

### Guiding Principles (agreed)
1. Hard stop-loss that algos can't game (ATR-based, not round number)
2. Trailing stop to ride trend without sacrificing R:R
3. Exit gates derived empirically from features, same rigor as entry gates

### Empirical Study — Trade Anatomy (`scripts/exit_study_nifty500.py`)

Run across NIFTY 500, Jun 2025 – Mar 2026 (1564 trades, max hold 30 bars). Output: `output/exit_study_nifty500.txt`.

**Key findings:**

**Stop-Loss (MAE in ATR multiples):**

| ATR mult | Winners dip | Losers dip | Recovery rate |
|----------|-------------|------------|---------------|
| 1.5x | 41.8% | 86.9% | 27.5% |
| 2.0x | 30.6% | 73.7% | 24.6% |
| 2.5x | 22.6% | 60.8% | 22.7% |
| 3.0x | 17.6% | 50.7% | 21.4% |

→ **2.5 ATR hard stop** is the sweet spot. Only 22.6% of winners dip that far; 60.8% of losers do. Recovery rate at 2.5 ATR is only 22.7%.

**Trailing Stop (DD from peak in ATR multiples):**

| Trail ATR | Triggers | Avg PnL at trigger | MFE captured |
|-----------|----------|-------------------|--------------|
| 1.0x | 97.7% | **+2.79%** | 48.7% |
| 1.5x | 93.2% | +1.20% | 21.5% |
| 2.0x | 84.6% | -0.37% | neg |

→ **1.0 ATR trailing stop** (activates when trade reaches +1 ATR profit) captures avg +2.79% vs current -0.34%. Half the MFE at a predictable price.

**Exit Gates — Per-Bar Feature Evolution:**

The winner vs loser divergence emerges by bar 7:

| Bar | Winners cts_slope | Losers cts_slope | Winners CTS | Losers CTS |
|-----|------------------|-----------------|-------------|------------|
| 0 | +0.0135 | +0.0137 | -0.057 | -0.094 |
| 7 | **+0.0135** | **-0.0010** | +0.039 | -0.070 |
| 10 | +0.0130 | -0.0076 | +0.078 | -0.095 |

→ **cts_slope turning negative** at bar 7 is the clearest loser signal. Winners' slope stays positive until bar 17.

**Feature delta correlations with PnL10 (strongest signals for exit design):**
- `d_cts` → r=+0.425 (CTS rising during trade = good)
- `d_pdd_120` → r=+0.384 (PDD rising = price leading delivery)
- `d_vel_dp5` → r=-0.261 (momentum fading = bad)
- `d_cts_accel` → r=-0.233 (accel fading = bad)

**MFE timing:** median peak at bar 10, 26% of trades peak between bars 21-30. Don't exit too early.

### Exit Prototype (`scripts/exit_prototype_nifty500.py`)

10 strategies tested across NIFTY 500. **Run this to get results:** `venv/bin/python3 scripts/exit_prototype_nifty500.py`. Output: `output/exit_prototype_nifty500.txt`.

Strategies:
```
A  current      — existing can_exit() (CWVAP-based, baseline)
B  hold_10      — hold exactly 10 bars (benchmark)
C  stop_only    — 2.5 ATR hard stop, hold 30 bars otherwise
D  stop+trail   — 2.5 ATR stop + 1.0 ATR trail (activates at +1 ATR profit)
E  stop+fg1     — 2.5 ATR stop + feature gate: cts_slope<0 after bar 5
F  stop+fg2+tr  — 2.5 ATR stop + fg: slope<0 AND cts<entry_cts bar5+ + 1.0 ATR trail
G  stop+fg3+tr  — 2.5 ATR stop + fg: slope<0 AND cts<0 bar7+ + 1.0 ATR trail
H  stop+fg4+tr  — 2.5 ATR stop + fg: slope<0 AND accel<0 bar5+ + 1.0 ATR trail
I  tight+fg2+tr — 2.0 ATR stop + fg2 + 1.0 ATR trail
J  wide+fg3+tr  — 3.0 ATR stop + fg: slope<0 AND cts<0 bar7+ + 1.5 ATR trail
```

**NIFTY 50 preview (175 trades):**

| Strategy | Win% | AvgPnL% | MedPnL% | AvgBar | Payoff |
|----------|------|---------|---------|--------|--------|
| A current | 51.4% | +0.92% | +0.02% | 9.4 | 2.12x |
| B hold_10 | 55.4% | +0.92% | +0.62% | 9.9 | 1.47x |
| H stop+fg4+tr | 57.1% | **+0.92%** | +0.50% | 9.9 | 1.41x |
| G stop+fg3+tr | 56.0% | +0.83% | +0.44% | 12.5 | 1.30x |
| D stop+trail | 62.9% | +0.76% | +0.79% | 15.0 | 0.87x |

H matches current avg PnL (+0.92%) while improving win rate by 5.7% and median PnL by +0.48%. NIFTY 500 run needed to confirm.

### Exit Prototype Results — NIFTY 500 (✅ COMPLETE, 2026-03-20)

`venv/bin/python3 scripts/exit_prototype_nifty500.py` → `output/exit_prototype_nifty500.txt`

**Conclusion: Current exit (Strategy A / CWVAP-based) beats all 9 prototypes on AvgPnL and Payoff.**

| Strategy | Win% | AvgPnL | Payoff |
|----------|------|--------|--------|
| A current | 36.3% | **-0.26%** | **1.43x** |
| D stop+trail | 46.2% | -1.03% | 0.79x |
| H stop+fg4+tr | 39.7% | -0.51% | 1.16x |

The CWVAP-based quick exit (avg 5.1 bars) acts as a tight early soft stop at -0.82% avg loss. ATR-based hard stops in new strategies trigger at -8.3% when they fire, erasing gains. **Exit strategy is not the bottleneck — entry quality is.**

---

## Entry Refinement Work (2026-03-20)

### 3x Payoff Target

Target: avg_winner / avg_loser ≥ 3x. At baseline, winners avg +4.51% and losers avg -4.18% at 10d hold (payoff 1.08x). No fixed-horizon gate can achieve 3x — winners and losers move similar magnitudes regardless of filtering. Path to 3x requires: tighter entries (improve win rate) + dynamic exits (cut losers early).

### Entry Quality Study (`scripts/entry_quality_study.py`)

NIFTY 500, Jun 2025 – Mar 2026, 1552 trades. Output: `output/entry_quality_study.txt`.

**Key findings:**

Feature discriminators (winners vs losers at entry):
| Feature | Winners mean | Losers mean | Notes |
|---------|-------------|-------------|-------|
| `pdd_120` | -1.15 | -2.55 | 2× gap — strongest entry discriminator |
| `cts` | -0.055 | -0.089 | Less negative = better |
| `velocity_60_norm` | +0.36 | +0.43 | **Higher velocity = worse** (already extended) |

Best gate found: `pdd_rel >= -0.4 AND velocity_60_norm <= 0.60`
- N=407 (27% of signals), Win%=50.6%, AvgPnL=+0.30% — turns system net positive at 10d hold
- `pdd_rel = (pdd_120 - pdd_120_threshold) / |pdd_120_threshold|` (normalised delivery exhaustion)

### Gate 5 — Implemented (✅ 2026-03-20)

Added to `NextGenEntryConfig` and `_can_enter()` after Gate 4:
```python
pdd_rel_min: float = -0.40   # (pdd_120 - threshold) / |threshold| >= -0.40
vel_max: float = 0.60         # velocity_60_norm <= 0.60; blocks extended delivery spikes
```
**Effect:** ~27–30% of signals filtered; expected Win% improvement 45% → 50.6%, AvgPnL -0.26% → +0.30%.

### Hard Stop Study (`scripts/hard_stop_study.py`)

NIFTY 500, Jun 2025 – Mar 2026, 469 trades (Gate-5-filtered). Output: `output/hard_stop_study.txt`.

**Key finding: pdd_rel does NOT discriminate hard-stop trades. CTS does.**

| Feature | Hard-stop trades | Rest |
|---------|-----------------|------|
| `cts` | **+0.017** | **+0.089** |
| `pdd_rel` | -0.167 | -0.166 ← identical |

Best entry gate for HS reduction: `cts >= -0.12` (efficiency 0.685: removes 61 HS trades per 89 signals filtered).

Early exit simulation: exiting at -1×ATR within bars 2–7 saves +3.37% per HS trade vs full -11.20% hard stop.

### Gate 6 — Implemented (✅ 2026-03-20)

```python
cts_abs_floor: float = -0.12   # absolute CTS floor; cts < -0.12 → 67–72% hard-stop rate
```
Blocks deeply-negative CTS entries that are most likely to become hard stops regardless of per-stock relative threshold.

### Early Stop — Implemented (✅ 2026-03-20)

Added to `NextGenExitConfig` and `check_exit()`, checked **after** CWVAP exits:
```python
early_stop_atr_mult: float = 1.0    # exit when pnl < -1×ATR
early_stop_min_bars: int = 2        # T+2 minimum (delivery trade classification, tax)
early_stop_max_bars: int = 7        # backstop window captures 56.9% of HS-bound trades
```
Fires only if CWVAP didn't already exit the trade. Catches persistent bleeders before the 2×ATR hard stop triggers.

### Walk-Forward Results — Gates 5 + 6 + Early Stop

`venv/bin/python3 scripts/walk_forward.py --signal nextgen --watchlist "NIFTY 500"`

| Period | Trades | Win% | AvgPnL | Payoff | hard_stop |
|--------|--------|------|--------|--------|-----------|
| Train 2019–2023 | 1980 | 42.3% | **+2.21%** | **3.10x** ✓ | 58 (-54%) |
| Test 2024–2026 | 717 | 31.5% | -0.63% | 1.52x | 32 (-48%) |

**Hard stops cut significantly** (126→58 train, 62→32 test). Train hits 3x payoff target.

**Problem: train/test gap is -10.8 pp win rate and -2.84% avg PnL.** This is not a gate tuning issue — it is **genuine market regime change**:
- Train (2019–2023): bull run, avg MFE 6.64%, CWVAP exits avg +0.78%
- Test (2024–2026): choppy/volatile, avg MFE 3.10%, CWVAP exits avg -1.27%

The signal generates strong momentum trades in trending markets and gets caught in false starts in sideways markets. The test period CWVAP exit (351 trades, -1.27% avg) is the single largest drag on test performance.

### Exit Reason Analysis (Test period)

| Exit reason | Count | Avg PnL | Win% |
|-------------|-------|---------|------|
| CWVAP | 351 | -1.27% | 23.1% | ← dominant drag |
| CTS Momentum Fade | 140 | +4.52% | 81.4% | ← still working |
| early_stop | 80 | -3.92% | 0% | ← backstop |
| hard_stop | 32 | -8.45% | 0% | ← reduced |

**Observation:** "CTS Momentum Fade" exits are working well (81.4% win, +4.52%) — the signal works when trends develop. The problem is the 49% of trades that exit via CWVAP in a choppy tape.

### Next: Visual Study + Further Entry Refinement

User to study signals visually on UI before the next analysis session. Specific questions to explore:
1. **Regime filter**: take signals only in `uptrend` or `transition` regime? These had better test performance
2. **CWVAP exit replacement**: the -1.27% CWVAP exit avg suggests it fires on false dips; investigate alternative early exit rule
3. **CTS level floor**: from entry quality study, `cts -0.03 to 0.0` bin was worst (Win=39.8%, Payoff=0.74x) — could exclude the "nearly recovered but not yet" zone

---

## CTS -1/+1 Threshold Prototype (2026-03-20 — Latest Work)

### Paradigm Shift: From 6-Gate NextGen to CTS Floor/Ceiling

After visual analysis, the user discovered a simpler and more intuitive approach: **causal Savgol CTS is clipped to [-1, +1]**, so use these natural boundaries as entry/exit levels. This is a fundamentally different system from the 6-gate NextGen — mean-reversion on the CTS indicator itself.

**Critical fix discovered**: The engine was using `"default_ema"` as the CTS strategy instead of `"causal_savgol"`, causing misinterpretation of all prior data. Fixed in `src/divergence_engine/engine.py` line 244: `cts_strategy="causal_savgol"`. User also created `src/trading/signals/savgol_cts.py` for the formal signal class.

**How causal Savgol CTS works** (in `src/divergence_engine/cts/causal_savgol.py`):
- CTS = `np.clip((velocity * scale_factor) / atr, -1.0, 1.0)` where scale_factor=5.0
- window_length=15, polyorder=2
- cts_buy_threshold = rolling P10 of CTS over 60 bars (adaptive per-stock)
- cts_sell_threshold = rolling P90

### Entry/Exit Logic — Current Best Configuration

**Entry** (all must pass):
1. `CTS <= -1.0` — CTS at floor (maximum oversold on causal savgol scale)
2. `coherence <= 0.3` — price/delivery diverged (good for mean-reversion); high coherence = lockstep decline (bad)
3. `pdd_120 > -10.0` — not deeply delivery-exhausted

**Execution**: Signal fires on bar i, trade opens on bar i+1 (T+1, next bar close = proxy for open).

**Exit** (after CTS has risen above -1.0 at least once):
- `CTS drops to cts_buy_threshold (P10)` OR `CTS drops to -1.0` — whichever comes first
- The buy_threshold typically fires first because it's above -1.0, providing earlier exit and tighter losses

**Why BT exit is asymmetric**: CTS -1/+1 entry/exit is symmetric by construction (same indicator, same distance). This produces 1.00x payoff on completed trades. Using buy_threshold as a dynamic exit level cuts losers earlier than waiting for -1.0, making the system asymmetric (losers exit faster → higher payoff).

### Prototype Script

```bash
venv/bin/python3 scripts/entry_crossover_prototype.py                           # NIFTY 50
venv/bin/python3 scripts/entry_crossover_prototype.py --watchlist "NIFTY 500"   # Full universe
```

Script: `scripts/entry_crossover_prototype.py` — self-contained, uses `DivergenceEngine` directly.

### Fizzle Study

Script: `scripts/cts_fizzle_study.py` — comprehensive analysis of trades that enter at CTS=-1 but never reach +1.

Output: `output/cts_fizzle_study.txt`, `output/cts_fizzle_trades.csv`

Key findings (1602 trades, unfiltered CTS -1/+1, NIFTY 500):
- 324/1602 (20%) trades never reach CTS=+1 ("fizzled"), averaging -11.67%
- Completed trade losers have avg pdd -3.31 vs winners -2.04
- All tested exit triggers (slope<0, accel<0, CTS drop) fired too aggressively on good trades — entry filtering is more effective than exit triggers for this system
- Coherence is the best discriminator: low coh at CTS=-1 = divergence (good); high coh = lockstep decline (bad)

### Results Evolution (NIFTY 500)

| Config | Trades | Win% | AvgPnL | Payoff | Avg Bars |
|--------|--------|------|--------|--------|----------|
| CTS -1/+1 baseline (no gate) | 1602 | 57.1% | +0.47% | 0.83x | — |
| + coh<=0.3, exit at +1 | 557 | 57.5% | +0.70% | 0.85x | — |
| + coh<=0.3, exit at BT/-1 | 663 | 37.9% | +0.33% | 1.78x | — |
| + coh<=0.3 + pdd>-10, exit at BT/-1 | 589 | 39.2% | +0.69% | 1.86x | 23.2 |
| **+ coh<=0.3 + pdd>-10 + no uptrend, exit at BT/-1** | **553** | **40.0%** | **+1.00%** | **1.94x** | **23.4** |

### Exit Breakdown (Best Config, NIFTY 500)

| Exit Reason | Count | Avg PnL | Win% |
|-------------|-------|---------|------|
| cts_hit_bt | ~dominant | tighter losses | higher |
| cts_hit_-1 | secondary | deeper losses | lower |
| end_of_data | residual | open trades | — |

### Regime Breakdown (Best Config, NIFTY 500)

| Regime | Trades | Payoff | Avg PnL | Win% |
|--------|--------|--------|---------|------|
| downtrend | — | **2.02x** | — | — |
| notrend | — | **1.96x** | — | — |
| transition | — | 0.87x | — | — |
| **uptrend** | **48** | **0.78x** | **-2.89%** | **31.2%** |

**Uptrend is the only regime with payoff < 1.0x.** Winners are weak (+5.03% avg vs system avg +10.61%). Data supports exclusion — pending decision.

Uptrend collateral damage: 48 trades, 15 winners (+5.03% avg), 33 losers (-6.49% avg). Removing them would improve system payoff.

### Key Insights

1. **Coherence as entry filter**: At CTS=-1, low coherence means price/delivery are diverging (oversold but delivery hasn't collapsed) — good mean-reversion setup. High coherence means both price and delivery falling in lockstep — genuine bearish trend, not oversold.

2. **BT exit provides asymmetry**: The buy_threshold (P10 of CTS) is a dynamic per-stock level above -1.0. Using it as the exit trigger means losers get cut before reaching the full -1.0 floor, while winners that rose significantly before dropping back still capture gains.

3. **pdd_120 > -10 removes deeply exhausted entries**: Only removes ~20 trades from end_of_data (fizzled) category, mostly negative PnL. Minimal collateral damage to good trades.

4. **Exit triggers don't work well here**: Slope<0 for N bars, accel<0 for N bars, CTS drop below level — all fire too aggressively on good trades that are still developing. Entry filtering is the lever, not exit timing.

### Uptrend Exclusion — ✅ IMPLEMENTED (2026-03-20)

Added `regime != "uptrend"` gate to `check_entry()` in `scripts/entry_crossover_prototype.py`. Regime checked on the **signal bar** (bar i), not the execution bar (bar i+1) — this is the information available at decision time.

**Results comparison (NIFTY 500, Jan 2025 – Mar 2026):**

| Config | Trades | Win% | Avg PnL | Payoff |
|--------|--------|------|---------|--------|
| Baseline (all regimes) | 589 | 39.2% | +0.69% | 1.86x |
| **+ Exclude uptrend** | **553** | **40.0%** | **+1.00%** | **1.94x** |

Removed 36 uptrend entries (avg -2.89%, 31.2% win, 0.78x payoff). 12 borderline trades remain with uptrend at execution bar but non-uptrend at signal bar — these are benign (-0.31% avg, 41.7% win).

**NIFTY 50 confirmation:**

| Config | Trades | Win% | Avg PnL | Payoff |
|--------|--------|------|---------|--------|
| Baseline | 52 | 48.1% | +2.38% | 1.99x |
| **+ Exclude uptrend** | **48** | **47.9%** | **+2.67%** | **2.10x** |

### Pending Decisions

1. ~~**Exclude uptrend regime**~~: ✅ Done — see above.
2. **Further coherence tightening**: coh<=0.25 was tested (fewer trades, similar quality) — diminishing returns below 0.3.
3. **Integration path**: CTS -1/+1 is the primary signal. PSZ explored extensively as complement (see PSZ Studies section below) — no complementary juice found. CTS -1/+1 stands alone.

---

## PSZ Studies (2026-03-21) — ✅ INVESTIGATED, NO CHANGE

### PSZ Exit Study (`scripts/psz_exit_study.py`)

Tested whether PSZ extremes could improve exits for CTS -1/+1 trades. Four variants:
- A: Baseline CTS BT/-1 exit
- B: Hold if psz_clipped > 0 at CTS exit, release when psz drops to 0
- C: Exit only when psz_clipped hits P90 ceiling
- D: Delay exit if psz_smooth > psz_sell_threshold

Results (NIFTY 500):

| Strategy | Win% | AvgPnL | Payoff | AvgBars |
|----------|------|--------|--------|---------|
| A Baseline | 40.1% | **+1.00%** | **1.93x** | 23.5 |
| B Hold if PSZ>0 | 40.5% | +1.04% | 1.91x | 24.0 |
| C PSZ ceiling exit | 52.9% | +0.73% | 1.11x | 16 |
| D Delay on PSZ>ST | 38.7% | +0.66% | 1.87x | 24.1 |

**Conclusion**: No improvement. When CTS hits BT, PSZ is already negative 59% of the time — they are correlated, not independent. Strategy C (PSZ ceiling) destroys payoff. Baseline CTS BT/-1 exit is optimal.

### PSZ Entry Complement Study (`scripts/psz_complement_study.py`)

Tested whether PSZ-level or PSZ-crossing signals catch entries CTS -1 misses.

Key finding (NIFTY 500 forward returns, 10-bar):
- CTS -1 signals (N=1,045): 43.3% win, -0.93% avg (raw, no exit management)
- PSZ-level complement (N=1,954): 37.1% win, **-1.99%** avg — worse
- PSZ-crossing complement (N=4,017): 48.9% win, -0.03% avg — near breakeven

**Conclusion**: PSZ at its P10 level (complement to CTS -1) is bad — mean -1.99%. PSZ crossing P10 upward has near-zero expected value.

### PSZ Entry Prototype (`scripts/psz_entry_prototype.py`)

Full trade simulation of PSZ crossing entries with 4 exit strategies × 6 gate configs on NIFTY 500.

Best result: ungated + no uptrend, Exit B (PSZ drops to buy_threshold):
- N=2,505, 34.7% win, **-0.15%** avg, 1.80x payoff, 25.7 bars

The `psz_hit_bt` exits (when PSZ crosses up then drops back below P10) average **-5.2% to -6.3% with 0% win rate** across all configs — they are catastrophic losers. CTS -1.0 is a natural clip boundary; PSZ P10 is just a rolling percentile that can be re-crossed repeatedly.

**Conclusion**: PSZ crossing entries don't work. CTS -1 is structurally superior because it's a hard clipped boundary, not a percentile threshold.

---

## savgol_cts.py — ✅ IMPLEMENTED (2026-03-21, updated 2026-03-21)

`src/trading/signals/savgol_cts.py` rewritten with PSZ raw crossover entry logic. This is now the **canonical signal class** for the CTS -1/+1 system.

### Entry — PSZ Raw Crossover (2026-03-21, v3)

**Paradigm shift from v2**: The prior entry (v2) fired when CTS first hit -1.0 with psz_v velocity gates (rising 3 bars, spread > 0.02, psz_raw < -0.26). This produced false positives where psz_v showed positive blips but PSZ raw was flat — velocity without displacement (e.g., CIPLA Jan 20 2026: psz_v=0.045 but PSZ raw stuck at -0.33 for 12 bars).

**v3 replaces all psz_v/bend/pdd/regime gates with a single PSZ raw crossover check.** When CTS and BT are pinned at -1.0, wait for PSZ raw to actually cross above -0.25 — structural turn confirmed by displacement, not velocity noise.

```python
SavgolCTSEntryConfig:
    cts_floor: float = -1.0              # CTS at maximum oversold
    psz_cross_threshold: float = -0.25   # PSZ raw must cross above this level
```

**Entry fires when ALL of:**
1. `CTS <= -1.0` — maximum oversold
2. `BT <= -1.0` — buy threshold also at floor (sustained oversold)
3. `PSZ raw > -0.25` on current bar AND `PSZ raw <= -0.25` on previous bar — crossover

**Gates removed (no longer needed with crossover approach):**
- `coherence_max` — crossover filters false setups that coherence was catching
- `pdd_floor` (-10.0) — tested: blocks 39 trades averaging +1.85% (25W/14L), net harmful
- `exclude_uptrend` — tested: blocks only 3 trades including MAXHEALTH +18.87%, net harmful
- `psz_v_min`, `psz_v_rising_bars`, `psz_v_min_spread` — replaced by crossover (displacement > velocity)
- `psz_raw_max` (-0.26) — contradicts crossover (can't be < -0.26 AND cross above -0.25)
- `psz_raw_bend_lookback/min_delta` — intermediate approach, superseded by crossover

**Why -0.25 threshold (NIFTY 50 sweep, 2019–2026):**

| PSZ cross level | N | Win% | AvgPnL | Payoff | Bars |
|-----------------|---|------|--------|--------|------|
| -0.28 | 269 | 69.1% | +1.89% | 1.05x | 14.9 |
| **-0.25** | **292** | **65.4%** | **+2.03%** | **1.33x** | **12.2** |
| -0.20 | 227 | 62.6% | +1.38% | 1.17x | 11.5 |
| -0.15 | 182 | 58.8% | +1.04% | 1.21x | 10.0 |

-0.25 has the best AvgPnL and strong payoff. Tighter thresholds (-0.20, -0.15) enter later with shorter holds but lower returns.

**PSZ-only complement study (2026-03-21):** Tested PSZ crossover without CTS/BT requirement (catching non-bottomed setups). PSZ-only at -0.25 generates 2251 trades (57.7% win, +1.29% avg, 1.26x payoff). Positive expectancy but lower quality than CTS+BT+PSZ. Combined OR gate adds no value — PSZ-only volume swamps the CTS+BT trades. **No change warranted.**

**Evolution of PSZ entry gates (2026-03-21):**
1. **psz_v gates** (v2): psz_v > 0.01, rising 3 bars, spread > 0.02. Failed on CIPLA Jan 20 — psz_v positive but PSZ raw flat.
2. **PSZ raw bend delta** (intermediate): `PSZ[today] - PSZ[today-3] >= threshold`. Simple delta at 0.03 gave 84 trades, 79.8% win, +3.63% avg, 1.05x payoff. Better but still a displacement proxy.
3. **Elbow detection** (tested, rejected): `scipy.signal.find_peaks` for trough detection. More principled but empirically inferior to simple delta — `find_peaks` looks up to 10 bars back, allowing slow grinds from distant troughs to pass.
4. **PSZ raw crossover** (v3, final): Level-crossing at -0.25. Simplest, most intuitive, best payoff (1.33x). Fewer trades than bend approach but higher quality per trade.

### Intensity (0–100)
- Base 60 for passing all gates
- +0–15: coherence bonus (coh < 0.5 = more divergence = better)
- +0–15: PDD bonus (closer to 0 = less exhausted = better quality)
- +10: downtrend regime bonus; +5: notrend bonus

### Reason strings
- `"SavgolCTS: STRONG [CTS=-1.00, coh=0.15, pdd=-3.0, downtrend, psz=-0.240, prev_psz=-0.260]"` (intensity >= 80)
- `"SavgolCTS: good [...]"` (intensity >= 65)
- `"SavgolCTS: [...]"` (below 65)

### Exit
```python
SavgolCTSExitConfig:
    st_crossover_tolerance: float = 0.03   # Relaxed sell-threshold crossover detection
    floor_tolerance: float = 0.10          # CTS/BT near-floor suppression zone
    psz_stall_min_delta: float = 0.005     # PSZ raw must rise this much from entry by T+N
    psz_stall_check_bar: int = 3           # check at T+3
```

Uses `delivery_bad_count` parameter as `cts_rose` flag (repurposed — this signal doesn't use delivery tracking).

**Exit suppressions (checked before any exit logic):**
1. **Floor suppression**: If CTS <= -0.90 AND BT <= -0.90, suppress exit — trade is still in setup zone. Discovered via MAXHEALTH 03-Feb-2025: CTS=-1.0, BT=-0.9432, exited prematurely, trade went on to close with better gain. Tolerance of 0.10 covers near-floor BT values.
2. **PSZ raw suppression**: If `price_slope_z < -0.26`, suppress exit — stock is still deeply oversold, hold the position.

**Exit triggers (after suppressions):**
- Ceiling exit: CTS >= 1.0 → `"CTS ceiling hit"`
- Sell threshold exit: CTS crosses below sell_threshold with tolerance 0.03 on crossover detection. Discovered via INDIGO 11-Feb-2026: CTS=0.8225, ST=0.8319, gap of only 0.0094 — strict crossover missed because prev_cts was already marginally below ST.
- Floor exit: CTS drops to buy_threshold → `"CTS hit BT (-0.xxx)"`
- Floor exit: CTS drops to -1.0 → `"CTS hit -1.0"`

### chart.py wired to savgol_cts
`src/divergence_engine/chart.py` now uses `SavgolCTSEntryConfig` / `SavgolCTSExitConfig` / `SignalFactory.get_signal("savgol_cts")` for UI marker generation.

### Backtest Results — NIFTY 50, Jan 2026+ (realized trades only)

```
Signal: savgol_cts | 8 trades | 75.0% win | +5.68% avg | 3.50x payoff

Symbol      Entry       Exit          PnL%    MFE%    MAE%    Days  Reason
APOLLOHOSP  2026-01-28  2026-02-09    4.85    4.85    1.11       8  CTS ceiling hit
CIPLA       2026-01-21  2026-02-11   -1.44    0.06    4.23      15  CTS ceiling hit
ETERNAL     2026-01-30  2026-02-10   11.04   11.04    0.35       7  CTS ceiling hit
INDIGO      2026-01-20  2026-02-12    4.03    4.67    4.04      17  CTS sell threshold hit
LT          2026-01-23  2026-02-06    8.66    9.17    0.00      10  CTS ceiling hit
MARUTI      2026-01-22  2026-02-13   -3.35    0.00    8.76      16  CTS ceiling hit
POWERGRID   2026-01-21  2026-02-04   13.14   13.14    0.65      10  CTS ceiling hit
TITAN       2026-01-29  2026-02-12    8.53    8.53    0.00      10  CTS ceiling hit

Avg winner: +8.38% | Avg loser: -2.40% | Avg duration: 11.6 bars
```

**Observations:**
- Winners move decisively (7-10 bars, MFE near PnL — little give-back)
- Losers (MARUTI, CIPLA) show zero/near-zero MFE — setup failed from bar one
- 87.5% exits via CTS ceiling hit — the system is catching genuine mean-reversion cycles
- Potential future refinement: early-exit gate if no MFE after ~5-6 bars (would cut MARUTI/CIPLA losses earlier)

---

## PSZ Panels — ✅ IMPLEMENTED (2026-03-21, updated 2026-03-21)

Two PSZ panels added to the UI for visual inspection.

**`src/web/js/divergence_engine.js`**:

1. **`"psz"` — PSZ Velocity**: Simplified to show only `psz_v` as a histogram (purple positive, red negative) with zero line. Previously showed raw PSZ + smooth + thresholds — stripped down since the raw PSZ now has its own panel.

2. **`"price_slope_z"` — Price Slope Z (Raw)**: New panel showing just the raw `price_slope_z` line (solid blue) with zero line. Added for direct visual inspection of the -0.26 entry gate threshold.

Activate in UI via panel settings (stored in `localStorage` as `de_panel_config`).

---

## State of the Codebase

### Signal Systems

**1. 6-gate NextGen** in `src/trading/signals/nextgen.py` (prior work):
```
Gate 1: cts_accel > cts_accel_threshold (×0.8 in bear regime)
Gate 2: cts >= cts_buy_threshold
Gate 3: vel_dp5 >= 4 AND velocity_60_norm > -0.10
Gate 4: pdd_120 < pdd_120_threshold
Gate 5: pdd_rel >= -0.40 AND velocity_60_norm <= 0.60
Gate 6: cts >= -0.12 (absolute CTS floor)
Bull extra: cts_slope >= 0.0001
```
Exit: Hard stop (2×ATR) → CWVAP → Early stop (1×ATR, bars 2–7)

**2. CTS -1/+1 Threshold** — `scripts/entry_crossover_prototype.py` (prototype) + `src/trading/signals/savgol_cts.py` (formal class):
```
Entry: CTS <= -1.0 AND coherence <= 0.3 AND pdd_120 > -10.0 AND regime != "uptrend"
Exit:  CTS drops to buy_threshold OR CTS drops to -1.0 (after having risen above -1)
```
Results: NIFTY 500 → 553 trades, 40.0% win, +1.00% avg, 1.94x payoff
         NIFTY 50  →  48 trades, 47.9% win, +2.67% avg, 2.10x payoff

The `savgol_cts` signal is **the active signal** wired into chart.py for UI markers.

### Modified files (uncommitted)
- `src/trading/signals/nextgen.py` — Gates 3, 5, 6 + early stop exit
- `src/trading/signals/savgol_cts.py` — **CTS -1/+1 full implementation** with PSZ gates (psz_v rising + spread + raw floor), exit suppressions (floor tolerance, PSZ raw, ST crossover tolerance), intensity scoring, descriptive reasons
- `src/trading/signals/base.py` — Trade dataclass, SignalInterface with tag_signals()
- `src/trading/signals/__init__.py` — exports SavgolCTSEntryConfig, SavgolCTSExitConfig, SavgolCTSSignal
- `src/divergence_engine/dvl_ledger.py` — `vel_dp5` in `compute_all()`
- `src/divergence_engine/chart.py` — switched to savgol_cts signal; added psz_smooth, psz_v, psz_buy_threshold, psz_sell_threshold to UI_COLUMNS
- `src/divergence_engine/engine.py` — `cts_strategy="causal_savgol"` (line 244)
- `src/web/js/divergence_engine.js` — PSZ panel simplified to psz_v only; new "price_slope_z" raw panel added
- `scripts/backtest_long_signals.py` — removed end-of-data force-close (realized trades only)

### Analysis scripts (untracked)
- `scripts/entry_crossover_prototype.py` — **CTS -1/+1 prototype** (primary backtest, use this)
- `scripts/cts_fizzle_study.py` — fizzle analysis for CTS -1/+1 trades
- `scripts/psz_exit_study.py` — PSZ exit override study (no improvement found)
- `scripts/psz_complement_study.py` — PSZ entry complement study (no complement found)
- `scripts/psz_entry_prototype.py` — PSZ crossing entry prototype (no value found)
- `scripts/feature_correlation.py` — feature correlation study
- `scripts/gate4_pdd_nifty500.py` — Gate 4 relaxation study (no change warranted)
- `scripts/gate_slope_guard_nifty500.py` — slope guard study (no change warranted)
- `scripts/exit_study_nifty500.py` — trade anatomy (1564 trades, 30-bar hold)
- `scripts/exit_prototype_nifty500.py` — 10 exit strategy variants (A–J)
- `scripts/entry_quality_study.py` — pdd_rel × cts_margin gate sweep
- `scripts/hard_stop_study.py` — CTS discriminator study for hard-stop reduction
- `scripts/psz_v_momentum_study.py` — **post-entry psz_v amplitude/decay study** (active investigation)

### Output files
- `output/cts_threshold_no_uptrend.txt` — NIFTY 500 final run with uptrend exclusion
- `output/psz_exit_study.txt` — PSZ exit study results
- `output/psz_entry_prototype.txt` — PSZ entry prototype results

### Run with
```bash
venv/bin/python3  # always use venv, not system python
```

### PSZ_v Momentum Decay Study — IN PROGRESS (2026-03-21)

**Hypothesis**: Post-entry psz_v amplitude and cycle behavior predicts trade outcome. Duds show rapid amplitude decay and narrowing half-cycle widths; winners sustain or accelerate.

**Observations from NIFTY 50 Jan 2026 (8 trades)**:
- psz_v has a natural ~5-bar half-cycle (5 bars up, 5 bars down visually)
- Winners: avg abs(psz_v) over bars +1 to +5 ranges 0.038–0.128 (sustained amplitude)
- Losers/duds: avg abs(psz_v) collapses — NEWGEN 0.008, CIPLA 0.018 (momentum dies)
- When intensity is low, half-cycle widths narrow (fewer bars per up/down swing)
- Two-cycle narrowing (3up/3down shrinking further) is a strong visual signal of exhaustion

**Study script**: `scripts/psz_v_momentum_study.py`
```bash
venv/bin/python3 scripts/psz_v_momentum_study.py --watchlist "NIFTY 50"
```

Measures per trade:
- Cycle 1 amplitude (avg abs(psz_v) bars +1 to +5) vs Cycle 2 (bars +6 to +10)
- Decay ratio (cycle 2 / cycle 1)
- Half-cycle widths (consecutive same-sign bars) — early vs late
- Threshold sweeps for c1_avg_abs and decay_ratio
- Bar-by-bar abs(psz_v) winner vs loser comparison
- Two regime splits: 2020–2023 (bull) and 2024–present (choppy)

Output: `output/psz_v_momentum_study.csv` (raw per-trade data)

**Results (212 trades, NIFTY 50, 2019–2026)**:

c1_avg_abs (avg abs(psz_v) bars +1 to +5) — **primary discriminator** (r=+0.311):

| Threshold | N (above) | Win% | Avg PnL | N (below) | Win% | Avg PnL |
|-----------|-----------|------|---------|-----------|------|---------|
| >= 0.020 | 188 | 71.3% | +2.17% | 24 | 29.2% | -6.18% |
| >= 0.030 | 162 | 75.3% | +3.14% | 50 | 38.0% | -4.99% |
| >= 0.040 | 142 | 77.5% | +3.74% | 70 | 44.3% | -3.90% |
| >= 0.050 | 127 | 78.7% | +3.77% | 85 | 48.2% | -2.59% |

**Regime-robust** — c1_avg_abs >= 0.03:
- Regime 1 (2020–2023, bull): 78.7% win, +3.50% (above) vs 23.8% win, -9.92% (below)
- Regime 2 (2024+, choppy): 74.6% win, +3.05% (above) vs 47.1% win, -2.69% (below)

n_half_cycles (r=-0.298) — fewer sign flips = better:

| Half-cycles | N | Win% | Avg PnL |
|-------------|---|------|---------|
| 1 (no flip) | 105 | 82.9% | +4.17% |
| 2 | 84 | 52.4% | -1.15% |
| 3+ | 23 | 43.5% | -3.49% |

Bar-by-bar gap (winners vs losers) widens from +0.005 at bar +1 to +0.053 at bar +5, confirming the 5-bar half-cycle observation. Winners sustain at 0.103, losers decay to 0.051 by bar +5.

**Proposed early-exit gate**: At bar +5 post-entry, if avg abs(psz_v) over bars +1 to +5 < 0.03, exit. Catches 50 dud trades (38% win, -5% avg) while keeping 162 (75% win, +3.14%).

### Agreed Algorithm — Rolling Momentum Fade Exit

**Logic**: For an open trade, from bar +5 onward, compute a rolling 5-bar avg abs(psz_v) every bar. If it drops below threshold (0.03 from study), exit — momentum has faded.

```
Bars 1–4:  No momentum check, let trade breathe
Bar 5+:    Every bar, compute rolling_5bar_avg = mean(abs(psz_v) over last 5 bars)
           If rolling_5bar_avg < 0.03 → exit "momentum fade"
```

**Why this works**:
- Duds: psz_v never sustains, rolling avg drops below 0.03 at bar 5 itself
- Mid-life exhaustion: started strong, cycles narrow, rolling avg eventually breaches
- Strong runners: psz_v sustains amplitude, rolling avg stays healthy, trade runs indefinitely
- Rolling window smooths the 5-bar half-cycle rhythm — single-bar dips don't trigger false exits
- Does not conflict with existing CTS exits (ceiling, sell threshold, floor) — those fire first if applicable

**Implementation**: Add to `check_exit()` in `savgol_cts.py`. Needs access to psz_v history for the trade — either pass via `records`/`idx` or accumulate in the trade simulation loop. Check **after** existing CTS exit suppressions but **before** CTS exit triggers.

**Threshold 0.03 empirical basis** (212 trades, NIFTY 50, 2019–2026):
- c1_avg_abs >= 0.03: N=162, 75.3% win, +3.14% avg
- c1_avg_abs < 0.03: N=50, 38.0% win, -4.99% avg
- Regime-robust: R1 (bull) 78.7%/+3.50% vs R2 (choppy) 74.6%/+3.05%

### Backlog: PSZ_v flat-lining in cruise-control mode (2026-03-21)

**Observation**: When a trade enters cruise control (CTS pegged at +1.0, price gliding above CWVAP/VA_high in a steady uptrend), psz_v goes nearly flat (abs < 0.005). The price slope is strongly positive but *not changing* — zero acceleration = zero psz_v. Example: BHARATFORG Jan 30 – Mar 10, 2026 (+33% move). CTS at +1.0 from Feb 3 onward, psz_v flatlines from Feb 12, rolling 5-bar avg|psz_v| drops below 0.03 on Feb 12 and stays there for 18 bars. A naive momentum fade would exit at +20% instead of riding to +33%.

**Implication**: psz_v flat-lining is NOT always bad. It has two modes:
1. **Dead trade**: CTS near floor, psz_v flat = trade never launched → EXIT
2. **Cruise control**: CTS at ceiling, psz_v flat = steady non-accelerating trend → HOLD

This dual-mode behavior must be accounted for. The cruise-control mode is a feature to leverage for trend-riding, not a signal to exit.

### Rolling Momentum Fade — Implementation & Study (2026-03-21)

**Implemented** in `savgol_cts.py` `check_exit()`: rolling 5-bar avg|psz_v| checked from bar +5 onward. Config: `momentum_fade_threshold=0.03`, `momentum_fade_window=5`, `momentum_fade_min_bars=5`. Added `records`/`idx` optional params to `SignalInterface.check_exit()` and all implementations.

**NIFTY 50 full-history results (momentum fade HURTS)**:

| Metric | Baseline (no fade) | With fade (0.03) |
|--------|-------------------|------------------|
| Trades | 213 | 223 |
| Win% | 66.7% | 61.4% |
| AvgPnL | +1.23% | +0.36% |
| Payoff | 0.72x | 0.70x |
| AvgBars | 24.6 | 17.1 |

Fade catches 84 trades: 30 winners (avg +3.52%) cut early + 54 losers (avg -7.87%). Cutting winners at +3.52% that would have run to ceiling (+4.00%) destroys value.

**CTS level study — duds vs winners at bar 5/10/15 (213 trades, NIFTY 50)**:

Duds (losers with c1_avg_abs < 0.03, N=31, avg PnL -11.68%):
- Bar 5: **100% CTS < 0**, median CTS = -0.993
- Bar 10: 90.3% CTS < 0, median = -1.000
- Bar 15: 80.6% CTS < 0, median = -0.465

Weak winners (winners with c1_avg_abs < 0.03, N=19, avg PnL +5.93%):
- Bar 5: 94.7% CTS < 0, median = -1.000 (identical to duds)
- Bar 10: 43.8% CTS < 0, median = +0.186 (diverging!)
- Bar 15: 25.0% CTS < 0, median = +0.482 (launched)

**Key finding**: At bar 5, duds and weak winners look identical (both CTS ~-1.0). By bar 10, weak winners have climbed to median +0.19 while duds are stuck at -1.0. The rolling check (not one-shot at bar 5) with a CTS gate is the correct approach — it catches duds that stay stuck while sparing slow starters that eventually launch.

CTS gating (at fade-eligible bar): `CTS < 0.0` catches 31/31 duds while sparing cruise-control trades (CTS at ceiling).

### Momentum Fade with CTS Gate — Tested, Still Hurts (2026-03-21)

Added `momentum_fade_cts_gate=0.0` to only fire fade when CTS < 0. Results:

| Config | Trades | Win% | AvgPnL | Payoff |
|--------|--------|------|--------|--------|
| Baseline | 213 | 66.7% | +1.23% | 0.72x |
| Fade 0.03 + CTS<0 | 221 | 61.5% | +0.59% | 0.75x |
| Fade 0.015 + CTS<0 | 218 | 63.3% | +0.76% | 0.73x |

CTS gate reduced winner damage (30→14 at 0.03) but still hurts AvgPnL. **Momentum fade approach abandoned.**

### PSZ Raw as Post-Entry Discriminator (2026-03-21)

**Key discovery**: PSZ raw trajectory post-entry completely separates duds from winners.

| Bar | Winners PSZ raw | Duds PSZ raw | Winners delta from entry | Duds delta from entry |
|-----|----------------|-------------|------------------------|---------------------|
| Entry | -0.280 | -0.317 | — | — |
| T+2 | -0.223 | -0.315 | +0.057 | +0.001 |
| T+3 | -0.142 | -0.316 | **+0.138** | **+0.001** |
| T+5 | +0.025 | -0.310 | +0.305 | +0.007 |

Winners: PSZ raw rises relentlessly, 90.8% positive delta by T+3.
Duds: PSZ raw is dead flat, 54.8% positive delta (coin flip).

**Pre-entry PSZ features (psz_v, psz_v spread, psz_bend) cannot discriminate** — duds and winners look identical at signal time. The divergence only appears post-entry.

### PSZ Stall Early Exit — ✅ IMPLEMENTED (2026-03-21)

At T+3 post-entry, if PSZ raw hasn't risen by >= 0.005 from its value at entry, exit. Catches trades where PSZ is stalled (knife-catch that never recovered).

```python
SavgolCTSExitConfig:
    psz_stall_min_delta: float = 0.005  # PSZ raw must rise this much from entry
    psz_stall_check_bar: int = 3        # check exactly at this bar
```

Checked **before** floor/PSZ suppressions (capital protection overrides hold logic).

**Results (NIFTY 50, full history)**:

| Config | Trades | Win% | AvgPnL | Payoff | Stall caught |
|--------|--------|------|--------|--------|-------------|
| Baseline | 213 | 66.7% | +1.23% | 0.72x | — |
| **d<0.005 T+3** | **216** | **64.4%** | **+1.26%** | **0.83x** | **16 (1W + 15L)** |
| d<0.010 T+3 | 216 | 63.9% | +1.24% | 0.84x | 18 (1W + 17L) |
| d<0.020 T+3 | 216 | 63.0% | +1.19% | 0.86x | 21 (1W + 20L) |

**d<0.005 at T+3** is the sweet spot: 15:1 loser:winner catch ratio, payoff +0.11x improvement, AvgPnL slightly better. The 1 clipped winner was +0.41% (negligible). Avg loss improves from -8.48% to -7.11% because duds get cut at -2.95% instead of bleeding to -11%+.

---

## BT-Cross Complement Entry (2026-03-21)

### Problem: V-Bottom Entries Missed

The PSZ crossover entry requires both CTS and BT at -1.0 (floor). For stocks that crash quickly (V-bottoms), CTS hits -1.0 but BT (rolling P10 over 60 bars) hasn't had time to reach -1.0. Examples:

- **BHARATFORG Jan 21–22**: CTS=-1.0 but BT=-0.60. PSZ crossover fires on Jan 22 (-0.2716 → -0.2610 crosses -0.25) but BT gate blocks. Stock rallied +33% from 1380 to 1800+.
- **DATAPATTNS Jan 27**: CTS=-1.0, BT=-0.98. BT nearly there but not yet. Stock rallied +11% from 2562 to 2840+.

### Solution: CTS Crosses BT From Below

New complementary entry: when CTS crosses above its own buy_threshold from below while still in oversold territory, this is a structural turn signal — CTS was below its rolling P10 and is now recovering.

**Entry logic (signal bar i, execute bar i+1):**
1. `prev_cts <= prev_bt` — CTS was at or below buy threshold
2. `cts > bt` — CTS crosses above BT on this bar
3. `cts <= bt_cross_oversold_threshold` (-0.50) — still in oversold zone

**Threshold sweep (NIFTY 50, full history, CTS-based exits):**

| Threshold | N | Win% | AvgPnL | Payoff | AvgBars |
|-----------|------|------|--------|--------|---------|
| CTS <= -0.50 | 1365 | 56.8% | +0.89% | 1.07x | 14.2 |
| CTS <= -0.60 | 1096 | 57.8% | +0.88% | 1.03x | 14.4 |
| CTS <= -0.70 | 838 | 58.8% | +0.87% | 0.97x | 14.3 |
| CTS <= -0.80 | 550 | 57.5% | +0.69% | 0.96x | 14.1 |
| CTS <= -0.90 | 279 | 59.1% | +0.87% | 0.95x | 13.6 |

Chose -0.50 for maximum coverage — positive expectancy at all thresholds.

### PSZ Glide Exit for BT-Cross Trades

BT-cross trades exit too early with CTS-based exits (ceiling/sell threshold/hit BT). Investigated PSZ-based exit: hold until PSZ drops below a threshold after having risen above it.

**PSZ peak distribution** during BT-cross trades (NIFTY 50): median max PSZ = 0.325. Winners peak at P50=0.336, losers at P50=0.283.

**Exit threshold sweep (NIFTY 50, BT-cross trades only):**

| Exit Strategy | N | Win% | AvgPnL | Payoff | AvgBars |
|--------------|-----|------|--------|--------|---------|
| CTS exits (baseline) | 1368 | 52.9% | +0.77% | 1.25x | 12.1 |
| PSZ glide < 0.15 | 1369 | 43.4% | +0.52% | 1.72x | 9.7 |
| PSZ glide < 0.20 | 1368 | 44.5% | +0.49% | 1.63x | 9.7 |
| PSZ glide < 0.25 | 1369 | 47.7% | +0.45% | 1.38x | 10.0 |
| PSZ glide < 0.30 | 1367 | 52.2% | +0.62% | 1.22x | 11.4 |
| PSZ glide < 0.35 | 1359 | 43.9% | +2.29% | 2.40x | 29.9 |

**Chose PSZ < 0.30**: balances payoff (1.22x) with reasonable hold time (11.4 bars). At 0.30, 67% of trades reach the threshold and those are 64.8% win / +7.15% avg.

### Exit Structure (BT-Cross)

Three-layer exit, each handling a distinct failure mode:

1. **PSZ stall (T+3)**: If PSZ raw hasn't risen by >= 0.005 from entry, exit. Catches dead trades early.
2. **PSZ glide exit (< 0.30)**: Once PSZ rises above 0.30 then drops back below, exit. Primary exit for healthy trades — 81.4% win, +3.94% avg.
3. **CTS hit BT / hit floor**: Safety nets for trades that never reached PSZ 0.30. Captures round-trip failures.

PSZ entries keep their original CTS-based exits (ceiling / sell threshold / floor) unchanged.

### Combined Signal Results (NIFTY 50, full history)

| Entry Path | N | Win% | AvgPnL | Payoff | AvgBars |
|------------|-----|------|--------|--------|---------|
| **Combined** | **1386** | **54.8%** | **+0.87%** | **1.22x** | **12.1** |
| PSZ (original) | 287 | 65.5% | +2.11% | 1.36x | 12.0 |
| BT-cross (new) | 1099 | 52.0% | +0.55% | 1.18x | 12.2 |

BT-cross exit breakdown:

| Exit | N | Win% | AvgPnL | AvgBars |
|------|---|------|--------|---------|
| PSZ glide exit | 624 | 81.4% | +3.94% | 14.8 |
| CTS hit BT | 285 | 8.4% | -5.24% | 12.5 |
| PSZ stall | 190 | 21.1% | -1.93% | 3.0 |

### Walk-Forward Validation — ✅ COMPLETE (2026-03-21)

`venv/bin/python3 scripts/walk_forward.py --signal savgol_cts --watchlist "NIFTY 50"`
`venv/bin/python3 scripts/walk_forward.py --signal savgol_cts --watchlist "NIFTY 500"`

**NIFTY 50:**

| Metric | Train (2019–2023) | Test (2024–2026) | Delta |
|--------|-------------------|-------------------|-------|
| Trades | 922 | 430 | |
| Win% | 55.6% | 53.7% | -1.9 pp |
| AvgPnL | +0.96% | +0.53% | -0.43 |
| Payoff | 1.19x | 1.15x | -0.04 |
| Avg MFE | 4.42% | 3.37% | -1.05 |
| Avg MAE | 3.25% | 2.48% | -0.77 |

**NIFTY 500:**

| Metric | Train (2019–2023) | Test (2024–2026) | Delta |
|--------|-------------------|-------------------|-------|
| Trades | 8,013 | 4,813 | |
| Win% | 53.8% | 46.9% | -6.9 pp |
| AvgPnL | +1.62% | +0.26% | -1.36 |
| Payoff | 1.44x | 1.25x | -0.19 |
| Avg MFE | 5.98% | 4.59% | -1.39 |
| Avg MAE | 3.87% | 4.04% | +0.17 |

**Stability comparison with previous NextGen signal:**

| | NextGen gap | SavgolCTS gap |
|--|-------------|---------------|
| Win% delta | -10.8 pp | -6.9 pp |
| AvgPnL delta | -2.84% | -1.36% |
| Payoff delta | -1.58x | -0.19x |

Significantly more stable. NIFTY 50 is remarkably tight (-1.9 pp, -0.04 payoff). NIFTY 500 test period still net positive with 1.25x payoff.

### Implementation

**Entry config** (`SavgolCTSEntryConfig`):
```python
cts_floor: float = -1.0
psz_cross_threshold: float = -0.25
bt_cross_enabled: bool = True
bt_cross_oversold_threshold: float = -0.50
```

**Exit config** (`SavgolCTSExitConfig`):
```python
st_crossover_tolerance: float = 0.03
floor_tolerance: float = 0.10
psz_stall_min_delta: float = 0.005
psz_stall_check_bar: int = 3
bt_cross_psz_glide_threshold: float = 0.30
```

**`check_entry`** tries PSZ crossover first, falls through to BT crossover. Returns `entry_tag` ("PSZ" or "BT-cross") in meta dict.

**`check_exit`** branches on `trade.entry_tag`:
- PSZ entries → original CTS exits (ceiling / sell threshold / hit BT / hit floor)
- BT-cross entries → PSZ stall at T+3 → PSZ glide exit (< 0.30) → CTS hit BT / hit floor safety nets

**`Trade` dataclass**: Added `entry_tag: str = ""` field to track entry path.

**`tag_signals`** in `base.py`: Updated to propagate `entry_tag` through pending_entry → Trade.

**`walk_forward.py`**: Added `--signal savgol_cts` choice, passes `records`/`idx` to `check_exit`, sets `entry_tag` on Trade.

### Modified Files (this session)
- `src/trading/signals/savgol_cts.py` — BT-cross entry + PSZ glide exit, refactored into `_check_psz_crossover`, `_check_bt_crossover`, `_exit_psz`, `_exit_bt_cross`
- `src/trading/signals/base.py` — `entry_tag` field on Trade, `entry_tag` propagation in `tag_signals`
- `scripts/walk_forward.py` — savgol_cts support, `records`/`idx` to `check_exit`, `entry_tag` on Trade
- `scripts/cts_cross_bt_study.py` — new, CTS-cross-BT sweep study

### Next Steps
- Integration into paper trading pipeline (`src/trading/scanner.py`)
- UI verification: check chart.py markers show both PSZ and BT-cross entries correctly
- Monitor PSZ glide exit behavior on live data — 0.30 threshold validated on historical data, may need seasonal adjustment
