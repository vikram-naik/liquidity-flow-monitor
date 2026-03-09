# Handoff — Liquidity Flow Monitor

## Current Status
The **Unified Weighted Scoring Model (v2)** is live. Binary gates have been
replaced with continuous factor scoring (0-1) and weighted signal_strength (0-100).

**Current focus:** Run signal quality report (Run 5) with PDD disabled + rebalanced weights,
then add `mcs_delta` as a new scoring factor.

---

## What's Live Now

### Unified Weighted Scoring (v2) — 2026-03-08
Replaced the binary-gate conviction system with continuous factor scoring.

**Direction detection: Dual-Score (factors decide)** — 2026-03-09
No separate direction classifier. Every bar is scored for BOTH Demand and Supply.
The stronger score wins. Direction emerges from the directional factors themselves.

How it works per bar:
```
demand_strength, demand_details = _score_bar(row, "Demand", factors)
supply_strength, supply_details = _score_bar(row, "Supply", factors)

if demand > supply and demand >= min_strength → Demand signal
if supply > demand and supply >= min_strength → Supply signal
otherwise → No Signal (shows winner's direction + details)
```

**Directional factor scoring matrix:**

| Factor | Scoring Fn | Demand scores high when... | Supply scores high when... |
|--------|-----------|---------------------------|--------------------------|
| PSZ | counter_directional | price_slope_z < 0 (falling) | price_slope_z > 0 (rising) |
| PSZ Delta | directional | psz_delta > 0 (turning up) | psz_delta < 0 (turning down) |
| RSZ Delta | directional | rdv_sz_delta > 0 (delivery rising) | rdv_sz_delta < 0 (delivery falling) |
| Regime | higher_is_better | regime_score (context-aware) | regime_score (context-aware) |
| CWC, RSZ, Coherence | non-directional | Same score both sides | Same score both sides |

**Regime context scoring** (replaces static per-regime scores):

| Context | Score | Example |
|---------|-------|---------|
| Counter-trend | 1.0 | Supply in uptrend, Demand in downtrend |
| Transition | 0.5 | Either direction in transition regime |
| Trend-aligned | 0.3 | Demand in uptrend, Supply in downtrend |
| No trend | 0.15 | Either direction in notrend regime |

Regime score is computed per-direction per-bar (not a static column). A Supply signal
in an uptrend gets regime=1.0, while Demand in the same bar gets regime=0.3.

**Worked example — downtrend bar (PSZ=+0.3, PSZ Delta=-0.05, RSZ Delta=-0.08):**
- Demand: PSZ=0 (positive=bad for demand), PSZ Delta=0, RSZ Delta=0, Regime=1.0 (counter) → low
- Supply: PSZ=0.75 (positive=good for supply), PSZ Delta=0.33, RSZ Delta=0.53, Regime=0.3 (aligned) → high
- Winner: Supply ✓

**Worked example — uptrend bar turning down (PSZ=-0.15, PSZ Delta=-0.04, RSZ Delta=+0.06):**
- Demand: PSZ=0.375 (negative=good for demand), PSZ Delta=0, RSZ Delta=0.4, Regime=0.3 (aligned)
- Supply: PSZ=0 (negative=bad for supply), PSZ Delta=0.27, RSZ Delta=0, Regime=1.0 (counter)
- Winner: depends on non-directional factors — no hard gate, continuous competition

**Current factors (7 active):**

| Factor | Column | Scoring Fn | Weight | Description |
|--------|--------|-----------|--------|-------------|
| PSZ Delta | psz_delta_3d | directional | 20% | Price slope inflection (Tier 1 primary) |
| RSZ Delta | rdv_sz_delta_3d | directional | 10% | Delivery slope inflection (Tier 1 primary) |
| PSZ | price_slope_z | counter_directional | 15% | Price slope (requires: psz_delta) |
| RSZ | rdv_slope_z | abs_higher_is_better | 10% | Delivery slope z-score (requires: rsz_delta) |
| CWC | cwc | lower_is_better | 5% | Cross-window coherence (requires any delta) |
| Coherence | coherence | higher_is_better | 5% | Multi-timeframe alignment (requires any delta) |
| Regime | regime_score | higher_is_better | 5% | Direction-aware regime context (standalone) |
| ~~PDD~~ | ~~pdd_30~~ | ~~abs_higher_is_better~~ | ~~disabled~~ | ~~Negatively correlated with returns~~ |

**`requires` system:** Child factor's score is capped by parent's score.
- `requires.factor: X` — single parent cap
- `requires.any: [X, Y]` — cap by max(parent scores)

**Tiered factor hierarchy:**
- Tier 1 (Primary): PSZ Delta, RSZ Delta — no requirements, computed first
- Tier 2 (Level): PSZ requires psz_delta, RSZ requires rsz_delta
- Tier 3 (Confirmation): CWC, Coherence require any: [psz_delta, rsz_delta]
- Tier 4 (Context): Regime — standalone

This prevents both: (a) saturated absolute values propping up signals without inflection,
and (b) structural factors (CWC, Coherence) pushing dead signals over the threshold
when neither delta shows meaningful change.

**Scoring functions** (`scoring/functions.py`):
- `higher_is_better` — linear ramp: value/max_value, capped at 1.0
- `lower_is_better` — inverted: 1 - value/ref_value
- `abs_higher_is_better` — absolute magnitude: |value|/max_value
- `directional` — Demand wants positive, Supply wants negative
- `counter_directional` — Demand wants negative (adverse), Supply wants positive
- `count_ratio` — count/max_count (currently unused, was for rdv_consistency)

**Adding a factor:** YAML-only (zero code change) unless a new scoring function is needed.

**Adding a scoring function:** `@register("name")` decorator in functions.py (3 lines).

### UI
- **Sidebar:** Scoring progress bars per factor with raw value + contribution (weighted score × 100)
- **Total row:** Sum of contributions = signal_strength
- **No Signal bars:** Show `scoring_direction` (Demand/Supply) so user sees which side was evaluated
- **Settings:** Auto-generated from API — weight sliders per factor per direction, min_signal_strength
- **No hardcoded JS** — UI renders whatever the backend sends

### Key Architecture Files
- `src/divergence_engine/regime.py` — ADX/DMI regime classifier (uptrend/downtrend/notrend/transition)
- `src/divergence_engine/scoring/__init__.py` — `compute_signal_strength()`, dual-score direction, `_regime_context_score()`
- `src/divergence_engine/scoring/functions.py` — scoring function registry
- `src/divergence_engine/config/default_rules.yaml` — v2 schema: settings + factors + regime config
- `src/divergence_engine/config_manager.py` — flat config for UI, v2 override merging
- `src/divergence_engine/analysis_integrated.py` — thin wrapper delegating to scoring package
- `src/divergence_engine/chart.py` — `signal_strength` + `scoring_details` + `regime` in UI_COLUMNS
- `src/divergence_engine/engine.py` — Module 1.5 regime, `signal_strength` + `regime` in EngineResult.latest

### Legacy (can be deleted)
- `src/divergence_engine/rule_engine.py` — binary gate engine, no longer called
- `src/divergence_engine/state_types.py` — `MarketContext` dataclass, only used by rule_engine

---

## Signal Quality Run History

### Runs 1-3 (v1 binary gates — historical baseline)

| Run | Config | All 5d Hit | Large Demand (elite) | n |
|-----|--------|-----------|---------------------|---|
| 1 | Old gates, no delta | 49.0% | 54.2% | 912K |
| 2 | + PSZ delta + dual scoring | 49.2% | 57.1% (high accum+coh) | 129K |
| 3 | Coherence in accum_score | 49.2% | 58.1% (high accum+coh) | 129K |

Key findings: coherence strongest discriminator, conviction score doesn't discriminate,
binary gates kill good signals → motivated v2 rearchitecture.

### Run 4 — v2 unified scoring + tiered requires (2026-03-09)
Config: tiered requires (delta gates level + confirmation), PDD enabled at 15%.
973K signals, 2176 symbols.

| Segment | 5d Hit | 5d Avg Ret | n |
|---------|--------|-----------|---|
| All | 49.5% | +0.16% | 973K |
| Demand | 46.7% | +0.57% | 474K |
| Supply | 52.1% | -0.23% | 500K |
| Large | 49.1% | -0.03% | 59K |
| Mid | 49.0% | -0.01% | 150K |

Key findings:
- Supply signals outperform Demand (52.1% vs 46.7%)
- PDD is actively harmful: r=-0.043 with returns, Q4 hit=37.6% for Supply → disabled
- `mcs_delta` identified as strongest unused predictor (5.4% Q1→Q4 spread, r=+0.089 Supply)
- MFM useful for Demand (r=+0.071)

### Run 5 — post PDD removal + weight rebalance (PENDING)
Changes: PDD disabled, CWC/Coherence reduced to 5% each.

---

## Other Live Features
- **Trading Holidays** — `nse_trading_holidays` table, `is_trading_holiday()` checks
- **Screener** — `scripts/run_screener.py`, `--min-strength` filter
- **Cache** — Redis-based, `de:` and `sq:` prefixes, `delete_pattern()` for bulk flush
- **Dynamic Panels** — user-configurable chart sub-panels (including PDD 30) via Settings → Panels tab
- **Signal Quality** — `scripts/signal_quality_report.py` + `/de/signal-quality` UI

---

## XGBoost Investigation — Key Findings (PAUSED)
- Target leakage: `cwvap_dist` sign = zone = label → 100% by learning zone
- Followthrough mode: 11-12% precision = base rate, model can't separate classes
- Simple gates achieve 83% precision at 60% recall on 65 examples — doesn't generalize

---

## Backlog
- **Add `mcs_delta` as scoring factor** — strongest unused predictor. 5.4% Q1→Q4 hit spread,
  r=+0.089 for Supply. Low redundancy with existing deltas (r=0.105 with rdv_sz_delta).
  Decision needed: Tier 1 (primary, ungated) or Tier 3 (gated by deltas).
  Column: `mcs_delta`, scoring: `directional`, normalize max_value TBD.
- **Add `mfm` as scoring factor** — r=+0.071 for Demand specifically. Would be `directional`
  (Demand wants positive MFM, Supply wants negative). Lower priority than mcs_delta.
- **Multi-thread signal quality report** — per-symbol runs are independent/IO-bound, use ThreadPoolExecutor
- **Tier-specific weights** — different factor weights for Large/Mid/Small/Micro tiers

## Dev Notes
- Always use `venv/bin/python3` to run scripts
- Always use latest versions of external libs — verify availability before use
- Flush `de:*` Redis cache after any scoring/weight change
- Re-run `signal_quality_report.py` after any scoring change to measure impact
