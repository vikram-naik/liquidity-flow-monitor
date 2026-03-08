# Handoff — Liquidity Flow Monitor

## Current Status
The conviction-gated 2-marker system (Demand / Supply) is **live in production**
with gate diagnostics UI. The current binary-gate architecture is being replaced
with a **Unified Weighted Scoring Model** (see architecture plan below).

**Current focus:** Implement the unified scoring rearchitecture.

---

## What's Live Now

### Conviction-Gated Demand/Supply Markers
Two markers with per-marker binary gates + dual conviction scoring:

- **Demand** — price below CWVAP + all gates pass → green arrow below bar
- **Supply** — price above CWVAP + all gates pass → red arrow above bar
- **Three scores** per signal: `accum_score`, `diverg_score`, `conviction_score` (blend)

**Per-marker conviction gates (all must pass):**

| Gate | Demand Default | Supply Default |
|------|----------------|----------------|
| CWC Gate (≤) | 0.75 | 0.65 |
| RDV Gate (≥) | 0.6 | 0.6 |
| RDV Consistency (≥) | 0 | 2 |
| Price Slope Z | ≤ 0 | ≥ 0.2 |
| PSZ Delta 3d | ≥ 0 (turning up) | ≤ 0 (turning down) |
| Coherence (≥) | 0.5 | 0.5 |

**Dual conviction scoring:**

| Accumulation Score (50%) | Weight | Divergence Score (50%) | Weight |
|--------------------------|--------|------------------------|--------|
| CWC (1-cwc) | 25% | PSZ Delta (inflection) | 40% |
| RDV | 20% | PDD (price-delivery) | 35% |
| RDV Consistency | 15% | PSZ Extreme (stretched) | 25% |
| CWVAP Depth | 15% | | |
| Coherence | 15% | | |
| Delivery % | 10% | | |

`conviction_score = 50% * accum_score + 50% * diverg_score`

### Gate Diagnostics UI (2026-03-08)
- **Backend-driven**: `rule_engine.classify_with_gates()` returns per-gate `{attr, val, lo, hi, passed}` for every rule on every bar
- **`gate_results`** column added to DataFrame + `UI_COLUMNS` → sent to UI as JSON
- **Sidebar** shows ✔/✘ per gate for both Demand and Supply rules on every bar hover
- No business logic in JS — UI just renders what backend sends
- Sidebar layout: Search → Engine State (with gate diagnostics) → Watchlist
- Settings panel now shows **all 12 gate thresholds** (was 7)

### Signal Quality Analysis
A monthly self-correction loop: run the engine on all stocks, measure outcomes.

**Script:** `scripts/signal_quality_report.py`
```bash
venv/bin/python3 scripts/signal_quality_report.py              # full run
venv/bin/python3 scripts/signal_quality_report.py --limit 50   # test subset
```

**UI Explorer:** `/de/signal-quality` — AG Grid (v35.1.0) with floating filters

### Other Live Features
- **Trading Holidays** — `nse_trading_holidays` table, `is_trading_holiday()` checks
- **Screener** — `scripts/run_screener.py`, `--min-conviction` filter
- **Cache** — Redis-based, `de:` and `sq:` prefixes, `delete_pattern()` for bulk flush
- **Dynamic Panels** — user-configurable chart sub-panels via Settings → Panels tab

---

## Signal Quality Run History

### Run 1 — Baseline (912K signals, old gates, no delta)
| Segment | 5d Hit | 10d Hit | Avg 5d Ret |
|---------|--------|---------|------------|
| All | 49.0% | 49.2% | +0.52% |
| Demand | 49.1% | 50.2% | +0.52% |
| Large-cap Demand | 54.2% | 55.1% | +0.37% |

### Run 2 — PSZ Delta + Dual Scoring (129K signals, 2026-03-07)
86% signal reduction. New gates: psz_delta_3d, relaxed CWC/PSZ for Demand.

| Segment | 5d Hit | 10d Hit | Avg 5d Ret | n |
|---------|--------|---------|------------|---|
| All | 49.2% | 49.9% | +0.06% | 128,541 |
| Demand | 48.8% | 51.0% | +0.43% | 83,847 |
| Supply | 49.9% | 47.7% | -0.64% | 44,694 |
| Large Demand | 51.7% | 53.4% | +0.06% | 5,639 |
| **Large Demand (high accum + high coh)** | **57.1%** | **56.3%** | **+0.80%** | **382** |

### Run 3 — Coherence moved to accum_score (128K signals, 2026-03-08)
Same signal count (gates unchanged), scoring recalibrated.

| Segment | 5d Hit | 10d Hit | Avg 5d Ret | n |
|---------|--------|---------|------------|---|
| All | 49.2% | 49.9% | +0.06% | 128,541 |
| **Large Demand (high accum + high coh)** | **58.1%** | **56.8%** | **+0.70%** | **544** |

Key: elite segment grew (382→544) and improved (57.1%→58.1%) after coherence fix.

### Key Findings Across All Runs
- **Coherence is the strongest discriminator** in large-caps: Q4 hit=53.6% vs Q1=48.1%
- **Accumulation score correlates with returns** (Q4: +0.65% vs Q1: +0.35%)
- **Conviction score (composite) doesn't discriminate well** across broad universe — all quartiles hover 48-50%
- **Binary gates kill good signals**: a bar failing one gate by 0.01 gets zero signal
- **This motivates the unified weighted scoring rearchitecture** (see below)

---

## NEXT: Unified Weighted Scoring Model — Architecture Plan

### Why
Binary gates are too deterministic — a bar with CWC=0.76 (fails by 0.01) and everything else stellar gets **zero signal**. The conviction score only decorates survivors, not ranking all candidates. Additionally, CWVAP direction (price above/below CWVAP) is too rigid — signals are missed when price closes barely on the wrong side.

### Core Concept
Replace binary gates + separate conviction scoring with a **single unified weighted scoring model**:
- Each factor contributes a **continuous score (0→1)** via a registered scoring function
- Weighted sum produces **signal_strength (0→100)**
- Signal fires if `signal_strength >= min_signal_strength` (configurable, default 40)
- CWVAP direction uses an **ATR-based tolerance band** instead of hard zero boundary

### New YAML Config Schema (v2)

```yaml
schema_version: 2

settings:
  min_signal_strength: 40        # 0-100, minimum to fire a signal
  direction_tolerance_atr: 0.3   # CWVAP tolerance band as fraction of ATR

factors:
  cwc:
    column: cwc
    scoring: lower_is_better     # registered scoring function name
    normalize:
      ref_value: 1.0
    weight:
      demand: 0.20
      supply: 0.20
    ui:
      label: "CWC"
      description: "Cross-Window Coherence"
      format: ".2f"

  rdv:
    column: rdv
    scoring: higher_is_better
    normalize:
      max_value: 2.0
    weight:
      demand: 0.15
      supply: 0.15
    ui:
      label: "RDV"
      format: ".1f"

  # ... (see full schema in architecture plan)
```

### New Module Structure

```
src/divergence_engine/
    scoring/
        __init__.py          # compute_signal_strength() — main entry point
        functions.py         # scoring function registry + built-in functions
        config_schema.py     # Pydantic models for YAML validation (optional)
```

### Scoring Function Registry

```python
# functions.py — decorator-based registry
@register("higher_is_better")
def _higher(value, params, direction): ...

@register("lower_is_better")
def _lower(value, params, direction): ...

@register("abs_higher_is_better")
def _abs_higher(value, params, direction): ...

@register("directional")
def _directional(value, params, direction): ...
```

Adding a new scoring function = write a 3-line function + reference by name in YAML.

### Data Flow

```
Modules 1-6 (UNCHANGED) → DataFrame with all columns
    ↓
Module 7: scoring.compute_signal_strength(df)
    For each bar:
    1. _determine_direction(cwvap_dist, atr_20) → Demand/Supply/None
    2. For each factor in YAML config:
       a. Read column value from row
       b. Apply scoring_fn(value, normalize_params, direction)
       c. Multiply score × weight
       d. Record in scoring_details[]
    3. signal_strength = weighted_sum / total_weight × 100
    4. Fire signal if signal_strength >= min_signal_strength
    ↓
DataFrame + integrated_state, signal_strength, scoring_details
    ↓
chart.py → JSON → API → UI renders scoring_details as progress bars
```

### UI Changes
- **No business logic in JS** — backend sends `scoring_details` per bar with `{factor, score, weight, weighted, ui: {label, format}}`
- **Settings auto-generated from API** — DELETE hardcoded `THRESHOLD_META`, `GROUPS`, `GATE_LABELS`
- **Sidebar** renders factor breakdown with progress bars instead of ✔/✘
- **Settings panel** auto-builds weight sliders per factor from `/de/api/config/state-rules` response

### CWVAP Direction Tolerance Band
```python
tolerance_pct = (atr_20 * direction_tolerance_atr / close) * 100
if cwvap_dist < -tolerance_pct: → Demand
if cwvap_dist > +tolerance_pct: → Supply
else: → No Signal (ambiguous)
```

### Adding a New Scoring Factor (zero code change path)
1. Ensure upstream pipeline produces the column (e.g., `new_metric` in Module 5)
2. Add entry to `factors:` in `default_rules.yaml` with column, scoring function, weight, ui metadata
3. Done. No changes to scoring engine, API, UI, chart serialization.

If a new scoring function type is needed:
4. Add a `@register("new_fn_name")` function in `functions.py` (3 lines)

### Migration Path (6 phases)
1. **Backend foundation** — create `scoring/` package, new YAML schema, config_manager v2 support
2. **Pipeline integration** — replace `analysis_integrated.apply_integrated_matrix()` call
3. **API changes** — serve factor UI metadata, accept weight overrides
4. **UI changes** — auto-generated settings, scoring breakdown sidebar
5. **Scripts** — update signal_quality_report.py and run_screener.py
6. **Cleanup** — delete rule_engine.py, remove MarketContext

### Files to Create / Modify / Delete

**Create:**
- `src/divergence_engine/scoring/__init__.py`
- `src/divergence_engine/scoring/functions.py`
- `src/divergence_engine/scoring/config_schema.py`

**Modify:**
- `src/divergence_engine/config/default_rules.yaml` — complete rewrite to v2
- `src/divergence_engine/config_manager.py` — schema versioning, v2 overrides
- `src/divergence_engine/analysis_integrated.py` — delegate to scoring package
- `src/divergence_engine/state_types.py` — remove MarketContext
- `src/divergence_engine/chart.py` — replace gate_results with scoring_details
- `src/divergence_engine/engine.py` — update import, EngineResult
- `src/api/main.py` — config endpoints serve v2 shape
- `src/web/js/divergence_engine.js` — auto-generated settings + scoring breakdown
- `src/web/divergence_engine.html` — rename gate diagnostics div
- `src/web/css/divergence_engine.css` — scoring progress bar styles
- `scripts/signal_quality_report.py` — use signal_strength
- `scripts/run_screener.py` — use signal_strength

**Delete:**
- `src/divergence_engine/rule_engine.py` — replaced by scoring package

---

## Files Changed (2026-03-08 Session)

| File | Change |
|------|--------|
| `src/divergence_engine/analysis_integrated.py` | Moved coherence to accum_score (non-inverted, 15%), removed from diverg_score. Uses `classify_with_gates()` to capture per-gate results. |
| `src/divergence_engine/config/default_rules.yaml` | New weights: accum (CWC 25%, RDV 20%, RDV cons 15%, depth 15%, del 10%, coherence 15%), diverg (psz_delta 40%, PDD 35%, psz_extreme 25%). Added coherence gate (≥0.5) for both markers. Demand PSZ gate relaxed to -0.1. User modified: demand_rdv_consistency_gate=0, demand_price_slope_z_gate=0. |
| `src/divergence_engine/rule_engine.py` | Added `classify_with_gates()` returning per-gate `{attr, val, lo, hi, passed}` |
| `src/divergence_engine/chart.py` | Added `psz_delta_3d`, `pdd_30`, `gate_results` to UI_COLUMNS |
| `src/web/js/divergence_engine.js` | Gate diagnostics sidebar (renders backend gate_results), all 12 gate thresholds in settings, removed redundant variable rows, engine state moved above watchlist |
| `src/web/divergence_engine.html` | Added gate-diagnostics div, reordered sidebar (engine state above watchlist) |
| `src/web/css/divergence_engine.css` | Gate diagnostics styles (pass=green, fail=red) |

---

## XGBoost Investigation — Key Findings (PAUSED)
- Target leakage: `cwvap_dist` sign = zone = label → 100% by learning zone
- Followthrough mode: 11-12% precision = base rate, model can't separate classes
- Simple gates achieve 83% precision at 60% recall on 65 examples — doesn't generalize

---

## Dev Notes
- Always use `venv/bin/python3` to run scripts
- `config_manager.py` is threshold-key agnostic
- Dead features (zero ML importance): `dvl_rate_10/30/60/120`, `cwvap_lag_3d`
- Always use latest versions of external libs — verify availability before use
- Flush `de:*` Redis cache after any gate/scoring change
- Re-run `signal_quality_report.py` after any gate change to measure impact
