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

## State of the Codebase

### Current signal: 6-gate NextGen in `src/trading/signals/nextgen.py`

```
Gate 1: cts_accel > cts_accel_threshold (×0.8 in bear regime)
Gate 2: cts >= cts_buy_threshold
Gate 3: vel_dp5 >= 4 AND velocity_60_norm > -0.10
Gate 4: pdd_120 < pdd_120_threshold
Gate 5: pdd_rel >= -0.40 AND velocity_60_norm <= 0.60
Gate 6: cts >= -0.12 (absolute CTS floor)
Bull extra: cts_slope >= 0.0001
```

Exit order in `check_exit()`:
1. Hard stop: pnl < -2×ATR → `hard_stop`
2. CWVAP-based exits: `can_exit()` from price_divergence
3. Early stop: bars 2–7, pnl < -1×ATR → `early_stop`

### Modified files (uncommitted)
- `src/trading/signals/nextgen.py` — Gates 3, 5, 6 + early stop exit
- `src/divergence_engine/dvl_ledger.py` — `vel_dp5` in `compute_all()`
- `src/divergence_engine/chart.py` — `vel_dp5` in `UI_COLUMNS`

### Analysis scripts (untracked)
- `scripts/gate4_pdd_nifty500.py` — Gate 4 relaxation study (no change warranted)
- `scripts/gate_slope_guard_nifty500.py` — slope guard study (no change warranted)
- `scripts/exit_study_nifty500.py` — trade anatomy (1564 trades, 30-bar hold)
- `scripts/exit_prototype_nifty500.py` — 10 exit strategy variants (A–J)
- `scripts/entry_quality_study.py` — pdd_rel × cts_margin gate sweep
- `scripts/hard_stop_study.py` — CTS discriminator study for hard-stop reduction

### Run with
```bash
venv/bin/python3  # always use venv, not system python
```
