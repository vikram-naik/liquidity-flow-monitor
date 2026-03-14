# Handoff — Liquidity Flow Monitor

## Current Status
The **Unified Weighted Scoring Model (v2.2)** is live. v2.2 adds intensity
discrimination via power-law scoring curves and a convergence multiplier.

**Current focus:** **CEI signal fine-tuning (Phase 3.5)** — reducing false positives
from noisy zero-crossings, rebalancing CEI feature weights with empirical data.

**Completed today (2026-03-13):**
1. **Delivery-Profile Value Area boundaries** — new `va_high`/`va_low` columns in
   `cwvap.py`. Uses TPO-style expansion from POC bin: accumulate delivery outward until
   70% (`va_pct`) of total delivery is captured. Per-window (`va_high_n`/`va_low_n`)
   then composite via DVL-rate weighted average. Also computes `va_profile_width`
   (VA width / ATR). These stay anchored at the consolidation zone because that's where
   delivery actually happened — unlike Bollinger-style CVAH/CVAL which widen immediately
   on breakout. Existing CVAH/CVAL kept as-is for `_classify_location` and `position_score`.
2. **VA Spring redesigned as Marker Modifier (Design A)** — removed VA Spring from the
   CEI evidence sum (was 5% weight, contributed ~0.016 to `cei_raw` — negligible, could
   not overcome EMA lag). Now operates as a **marker override**: when spring score
   exceeds `spring_threshold` (0.20), the marker logic checks `cei_raw` zero-crossings
   instead of EMA-smoothed `cei`, bypassing smoothing lag on breakout bars. Philosophy:
   spring energy is a discrete event (breakout after long containment), not a gradual
   trend — running it through EMA destroys its timing value.
   - GESHIP: Demand marker moved from Jan 30 → **Jan 28** (actual breakout bar, +5%)
   - ABCAPITAL: Supply marker moved from Mar 11 → **Mar 4** (actual breakdown bar, -5%)
3. Signal trigger changed from slope to CEI zero-crossings
4. CEI feature max_value recalibration (P90-based)
5. CEI weight rebalance: PSZ 25%→15%, MCS 20%→30%
6. Cumulative divergence: rolling 20-bar sum
7. RSZ–PSZ per-window confirmation: dampened 0.2× on sign disagreement

**Previous session (complete):**
- CEI markers replace scoring markers, slope zero-crossings, Supply CWVAP gate,
  marker intensity, screener rewrite, EngineResult.latest expansion.

**Next:**
- VA trendlines on OHLC chart (draw va_high/va_low on Panel 1 for visual validation)
- Intensity calculation to move into cei.py (currently in JS/screener) — parked
- Run 5 signal quality report with current markers

---

## What's Live Now

### Unified Weighted Scoring (v2.2) — 2026-03-11
Replaced the binary-gate conviction system with continuous factor scoring.
v2.2 adds intensity discrimination via power-law exponents and convergence multiplier.

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
| MCS Delta | directional | mcs_delta > 0 (momentum rising) | mcs_delta < 0 (momentum falling) |
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

**Current factors (15 active, weights sum to exactly 1.00/100%):**

> **v2.2 Intensity scoring**: Delta factors use `exponent: 1.5`, level factors use
> `exponent: 1.3`. Scoring: `(linear_score) ^ exponent` — suppresses weak, preserves strong.
> Post-scoring convergence multiplier: 0/3 families → 0.85×, 1→1.0×, 2→1.10×, 3→1.20×.

| Factor | Column | Scoring Fn | Weight | Description |
|--------|--------|-----------|--------|-------------|
| PSZ Δ (2, 4, 9) | psz_delta_Xd | directional | 15% (Σ) | Price slope momentum turning points |
| RSZ Δ (2, 4, 9) | rsz_delta_Xd | directional | 10% (Σ) | Delivery slope momentum turning points |
| MCS Δ (2, 4, 9) | mcs_delta_Xd | directional | 20% (Σ) | Price/Delivery correlation momentum |
| CDVL | cdvl | directional | 10% | Composite Delivery Velocity (Macro 10-120d volume momentum) |
| PSZ | price_slope_z | abs_higher_is_better | 15% | Price slope z-score |
| RSZ | rdv_slope_z | abs_higher_is_better | 10% | Delivery slope z-score |
| CWC | cwc | lower_is_better | 5% | Cross-window coherence (noise filter) |
| Coherence | coherence | higher_is_better | 5% | Multi-timeframe trend alignment |
| Regime | regime_score | higher_is_better | 5% | Direction-aware Price Trend context |
| Volume Phase | gradient_shape | categorical_map | 5% | Direction-aware Institutional Volume phase multiplier |

**`requires` system:** Child factor's score is capped by parent's score.
- `requires.factor: X` — single parent cap
- `requires.any: [X, Y]` — cap by max(parent scores)

**Tiered factor hierarchy:**
- Tier 1 (Primary): PSZ Delta, RSZ Delta, MCS Delta — no requirements, computed first
- Tier 2 (Level): PSZ requires psz_delta, RSZ requires rsz_delta
- Tier 3 (Confirmation): CWC, Coherence require any: [psz_delta, rsz_delta, mcs_delta]
- Tier 4 (Context): Regime — standalone

This prevents both: (a) saturated absolute values propping up signals without inflection,
and (b) structural factors (CWC, Coherence) pushing dead signals over the threshold
when neither delta shows meaningful change.

**Convergence multiplier** (`scoring/__init__.py`):
- Counts how many delta families (PSZ, RSZ, MCS) have ≥1 window scoring ≥0.3
- 0 families → 0.85×, 1 → 1.0×, 2 → 1.10×, 3 → 1.20×
- Applied post-scoring as: `signal_strength = base_strength × multiplier`
- Config-driven via `settings.convergence` in YAML

**Scoring functions** (`scoring/functions.py`):
- `higher_is_better` — linear ramp: value/max_value, capped at 1.0
- `lower_is_better` — inverted: 1 - value/ref_value
- `abs_higher_is_better` — absolute magnitude: |value|/max_value
- `directional` — Demand wants positive, Supply wants negative
- `counter_directional` — Demand wants negative (adverse), Supply wants positive
- `count_ratio` — count/max_count (currently unused)
- `categorical_map` — dynamically maps raw strings (e.g. `sideways`) to float config scores using a demand/supply dict lookup

**Adding a factor:** YAML-only (zero code change) unless a new scoring function is needed.

**Adding a scoring function:** `@register("name")` decorator in functions.py (3 lines).

### UI
- **Sidebar Expansion:** New `>` button expands the sidebar to show both Demand and Supply scoring details side-by-side, with the dominant signal anchored on the left.
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

### Run 5 — PDD removed + MCS Delta added + weight rebalance (PENDING)
Changes: PDD disabled, `mcs_delta` added at Tier 1 (20%, directional, max_value=0.3),
weights rebalanced to sum to 100% (PSZ Delta 25%, RSZ Delta 15%, MCS Delta 20%,
PSZ 15%, RSZ 10%, CWC 5%, Coherence 5%, Regime 5%).

---

## Cumulative Evidence Index (CEI) — Phases 1-2 Complete + Position Tuning

### What it does
Rolling net-directional-evidence score centered at zero. Positive = Demand evidence
building (accumulation), negative = Supply evidence building (distribution).

### Architecture
- **Module 7.5** in pipeline: `src/divergence_engine/cei.py` — runs after scoring (Module 7)
- Config: `cei` section in `default_rules.yaml` — all weights/params config-driven
- Outputs: `cei_raw` (per-bar signed evidence × structure mult), `cei` (EMA smoothed), `cei_slope` (linreg)

### CEI Formula
```
signed_score_i = clip(value / max_value, -1, +1)      # per feature
pos_score      = f(close, cwvap, cpoc)                 # position awareness
evidence       = Σ(weight_i × signed_score_i) + pos_weight × pos_score
structure_mult = (0.5 + 0.5×coherence) × (0.5 + 0.5×cwc)  # 0.25–1.0
cei_raw        = evidence × structure_mult
cei            = EMA(cei_raw, span=10)
cei_slope      = linreg_slope(cei, window=5)

# Marker emission (with spring override):
spring_score   = va_dwell(60) × breakout_direction     # computed separately
if |spring_score| >= 0.20:
    marker checks cei_raw zero-crossing               # bypass EMA lag
else:
    marker checks cei zero-crossing                    # normal smoothed path
```

### Feature weights (updated 2026-03-13)
| Group | Weight | Features | max_value |
|-------|--------|----------|-----------|
| Price Δ | 15% | PSZ Δ 2d/4d/9d | 0.25/0.45/0.55 (P90) |
| Delivery Δ | 20% | RSZ Δ 2d/4d/9d (dampened 0.2× when PSZ disagrees) | 0.23/0.35/0.40 (P90) |
| Money Flow Δ | 30% | MCS Δ 2d/4d/9d | 0.50 |
| Cumul Divergence | 15% | rolling 20-bar sum(accum_div - distrib_div) | 0.35 (P90) |
| Position | 5% | CWVAP/CPOC price-position score | — |
| Levels | 10% | PSZ, RSZ (signed) | 0.40 |
| Macro Volume | 5% | CDVL | 0.15 |

**Note:** VA Spring is **not** in the evidence sum. It operates as a marker modifier
(see below).

### Position Score (`position_score` in YAML, `_compute_position_scores()` in cei.py)
Makes CEI aware of where price sits relative to institutional cost basis. Prevents
false positives when delivery evidence is real but price hasn't confirmed.

**CWVAP component (60%):** Percentage-based piecewise curve.
- Sweet spot: -3% to 0% below CWVAP (peaks at -1.5%) → score 0.5 to 1.0
- Above CWVAP: 0% to +6% → decays 0.5 to -0.5
- Below value: -3% to -6% → decays 0.5 to -0.3
- Deep below: -6% to -12% → decays -0.3 to -1.0

**CPOC component (40%):** Linear ramp, saturates at ±5%. Above CPOC = positive (Demand).

All thresholds config-driven for future UI settings exposure.

**Backtest results (20 symbols, 2025-01 to 2026-03):**
- Demand sweet spot (-3% to 0% CWVAP): 58.4% → **64.5%** hit rate at 10d
- Demand false positive zone (<-3%): 51.4% → **35.5%** (correctly flagged)
- Supply overall: 44.7% → **48.1%** hit rate (+3.4pp)
- False positive zones (CEI>0 while price <-3% CWVAP): 421 → **288** bars (-32%)

**Key scenario improvements:**
- TCS (Feb 26): CEI no longer crosses positive during -10% decline
- HDFCLIFE: CEI crosses negative Mar 11 (was Mar 12 — 1 bar earlier)
- FACT breakout: CEI still goes positive on Mar 10, surges to +0.17 by Mar 12

### Delivery-Profile Value Area (`cwvap.py`)
Proper delivery-weighted VA boundaries replacing Bollinger-style CVAH/CVAL for VA Spring.

**Problem:** CVAH/CVAL = `DVWAP ± rolling_std(close)` — widen immediately on breakout,
destroying the box signal (GESHIP CVAH jumped 1145→1215 in 2 bars after breakout).

**Solution:** Per-window TPO Value Area expansion from POC bin:
1. Start with POC bin (argmax of delivery histogram)
2. Expand outward: include adjacent bin with more delivery (tie → go lower)
3. Stop when accumulated delivery >= `va_pct` (70%) of total
4. `va_low_n = bin_edges[lo_idx]`, `va_high_n = bin_edges[hi_idx + 1]`
5. Composite: DVL-rate weighted average across windows (same as CPOC)
6. `va_profile_width = (va_high - va_low) / atr_20`

**Config:** `settings.va_pct: 0.70` in YAML, passed to `CompositeVWAP(va_pct=...)`.

**Columns:** `va_high_n`/`va_low_n` (per-window), `va_high`/`va_low` (composite),
`va_profile_width`. Existing `cvah`/`cval` kept for `_classify_location` and `position_score`.

### VA Spring — Marker Modifier (Design A)
VA Spring detects breakouts after long VA containment. Originally a 5% weighted evidence
term in the CEI sum — but at that weight it contributed ~0.016 to `cei_raw`, couldn't
overcome EMA(10) inertia, and produced identical marker timing to having no spring at all.

**Key insight:** Spring energy is a **discrete event** (breakout after long containment),
not a gradual trend. Running it through EMA destroys its timing value. Delta features
(PSZ, RSZ, MCS) need EMA filtering because they're noisy. Spring isn't noisy — it's
either releasing or it isn't.

**Design A — Spring Override:** When spring score exceeds `spring_threshold` (0.20), the
marker logic checks `cei_raw` zero-crossings instead of EMA-smoothed `cei`:
```
if |spring_score| >= spring_threshold:
    check cei_raw zero-crossing (bypasses EMA lag)
else:
    check cei (normal EMA-smoothed path)
```

**Config:** `cei.va_spring.spring_threshold: 0.20` — no `weight` key (not in evidence sum).

**Validated results:**
- GESHIP: Demand marker **Jan 28** (was Jan 30) — actual breakout bar (+5% move)
- ABCAPITAL: Supply marker **Mar 4** (was Mar 11) — actual breakdown bar (-5% move)

### Chart panel (`divergence_engine.js`)
- Green/red histogram (area fill) + teal CEI line + purple dashed slope line + zero ref
- Enabled by default in `getActivePanels()`

### Phase status
- **Phase 1** ✓ — Feature engineering + computation
- **Phase 2** ✓ — Chart panel visualization
- **Phase 2.5** ✓ — Position score tuning (CWVAP/CPOC awareness + CDVL rebalance)
- **Phase 3** ✓ — CEI markers + screener (COMPLETE)
  - ✓ `cei_signal`: CEI zero-crossings + cooldown (was slope zero-crossings)
  - ✓ YAML config: `cooldown_bars: 5`, `supply_below_cwvap: true`
  - ✓ JS markers: CEI replaces scoring markers
  - ✓ Screener: `SCR: Long` / `SCR: Short` via CEI signals
  - ✓ Supply CWVAP gate: Supply only fires when close < cwvap
  - ✓ `EngineResult.latest` includes `close`, `cei`, `cei_slope`, `cei_signal`
- **Phase 3.5** (IN PROGRESS) — CEI signal fine-tuning
  - ✓ Signal trigger: slope zero-crossings → CEI zero-crossings
  - ✓ max_value recalibration (P90-based from Nifty 50 empirical data)
  - ✓ Weight rebalance: PSZ 25%→15%, MCS 20%→30%
  - ✓ Cumulative divergence (rolling 20-bar sum replaces single-bar)
  - ✓ RSZ–PSZ per-window confirmation (dampening 0.2× on sign disagreement)
  - ✓ Delivery-Profile VA boundaries (`va_high`/`va_low`) — TPO 70% delivery volume area
  - ✓ VA Spring → Marker Modifier (Design A): bypasses EMA on spring breakout bars
  - ○ Intensity calculation in cei.py (parked)
- **Phase 4** — Future: Trend-riding state machine (entry vs continuation markers),
  phase machine + intensity labels for screener

---

## Other Live Features
- **Trading Holidays** — `nse_trading_holidays` table, `is_trading_holiday()` checks
- **Screener** — `scripts/run_screener.py`, CEI-based (`SCR: Long` / `SCR: Short`), `--min-intensity` filter (default 2)
- **Cache** — Redis-based, `de:` and `sq:` prefixes, `delete_pattern()` for bulk flush
- **Dynamic Panels** — user-configurable chart sub-panels (PDD 30, MCS Delta, etc.) via Settings → Panels tab
- **Signal Quality** — `scripts/signal_quality_report.py` + `/de/signal-quality` UI

---

## XGBoost Investigation — Key Findings (PAUSED)
- Target leakage: `cwvap_dist` sign = zone = label → 100% by learning zone
- Followthrough mode: 11-12% precision = base rate, model can't separate classes
- Simple gates achieve 83% precision at 60% recall on 65 examples — doesn't generalize

---

## Backlog
- **Trend-riding state machine** — After entry signal, track "Active Long/Short" state.
  While price > CWVAP (Demand) or < CWVAP (Supply), subsequent slope crossings show
  continuation markers (different shape/color) instead of new entry signals. Exit state
  when close crosses CWVAP against the trade. Weekly regime can serve as confirmation
  layer (weekly CEI too laggy, but weekly regime is stable). See GLENMARK Feb 16-Mar 12
  example: 3 Demand markers fired in a single +11% trend that never dropped below CWVAP.
- **Supply exit fine-tuning** — CEI MFE +4.43% but exits at -0.43%. Study HDFCLIFE + others
  visually. Consider: slope-based early exit, trailing value distance, min-hold period.
- **Add `accum_divergence` factor** — `max(0, RSZ) × max(0, -PSZ)`: positive when price↓ + delivery↑ (accumulation).
  `directional` scoring (Demand wants positive, Supply wants negative). Discovered via FACT investigation
  (Feb-Mar 2026): RSZ rising to +0.26 during price decline was missed because PSZ/RSZ use `abs_higher_is_better`
  (direction-agnostic). Requires weight rebalancing + signal quality re-run.
- **Run 5 signal quality report** — with slope-crossing CEI markers
- **Multi-thread signal quality report** — per-symbol runs are independent/IO-bound, use ThreadPoolExecutor
- **Tier-specific weights** — different factor weights for Large/Mid/Small/Micro tiers
- **VA trendlines on OHLC chart** — draw `va_high`/`va_low` (delivery-profile) as trendlines on Panel 1 for visual validation of box detection and breakout/breakdown signals

## Dev Notes
- Always use `venv/bin/python3` to run scripts
- Always use latest versions of external libs — verify availability before use
- Flush `de:*` Redis cache after any scoring/weight change
- Re-run `signal_quality_report.py` after any scoring change to measure impact
