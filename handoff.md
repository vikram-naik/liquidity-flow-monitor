# Handoff — Liquidity Flow Monitor

## Current Status
The **Unified Weighted Scoring Model (v2.2)** is live. v2.2 adds intensity
discrimination via power-law scoring curves and a convergence multiplier.

**Current focus:** Next backtester improvements — parameter sensitivity sweep,
risk metrics, multi-symbol batch (NIFTY 500).

**Completed (2026-03-15):**
1. **CEI threshold exit implemented** — `cei_exit_threshold: 0.08` is now the primary
   exit gate. Signal_Exit only fires when `|CEI| >= 0.08`. NIFTY 50: **+443,188 P&L**
   (4× baseline). Old MinHold+VA filters disabled by default (redundant, cost -42K when
   combined with CEI threshold). CLI: `--cei-exit N` (default 0.08, 0=disabled).
2. **Assister cooldown fix** — `_overlay_assister_signals()` in `cei.py` tracked cooldown
   variables but never enforced them. Fixed by adding `(i - last_demand_bar > cooldown)`
   check to both Demand_Assister and Supply_Assister crossing conditions.
3. **Exit strategy investigation** — NIFTY 50 analysis (802 Signal_Exit trades): exits
   correct only 46% at 10d, 94% from Supply_Assister (cutting winners). Tested 7 strategies;
   CEI threshold 0.08 is optimal: +443K P&L (4× baseline), 50% WR, 322 trades, 99d avg hold.
4. **Dual-EMA assister experiment** — Tested EMA(5)×EMA(14) vs current raw×EMA(14) as
   assister method via `scripts/test_dual_ema_assister.py`. Current method wins decisively
   (Supply 5d hit 50.9% vs 46.4%). Dual-EMA rejected.
5. **Test scripts created** — `scripts/test_exit_filters.py` (8-variant exit filter
   strategy harness), `scripts/test_dual_ema_assister.py` (dual-EMA comparison).

**Completed (2026-03-14):**
1. **CEI noise reduction** — empirical investigation identified three root causes of
   `cei_raw` flickering (2d delta sign flips, generous structure floors, weak crossings).
   Five-layer fix: 2d→9d weight shift, structure floors 0.5→0.3, EMA span 10→14,
   cumul_divergence boosted 15%→20%, `min_crossing_gap: 0.005` filter on signal generation.
   Results: primary signals -64%, raw flips -8%, RELIANCE backtest +14.71%→**+44.96%**,
   PF 2.03→**3.63**, win rate 33%→**60%**. HDFCBANK -7.94%→**-0.05%**.
2. **VA trendlines on OHLC chart** — `va_high`/`va_low` plotted as purple dashed lines
   on Panel 1, with `cbVA` toggle checkbox. Added to `UI_COLUMNS` in `chart.py`, pill
   styling in CSS, line series + legend + toggleMap in JS.
3. **`cei_raw` plotted on CEI panel** — orange line (`#ff8a65`) in CEI chart panel.
   Histogram switched from smoothed `cei` to `cei_raw` data for visual consistency
   (histogram = raw evidence, teal line = EMA smoothed on top).
4. **ABCAPITAL investigation** — Supply expected Mar 2 (price closed below `va_low`),
   fired Mar 4. Root cause: zero-crossing lag. On Mar 2, `cei_raw`=+0.1162 (still
   positive, no zero-crossing yet), spring=-0.0985 (below 0.20 threshold → EMA path).
   Signal only fires Mar 4 when `cei_raw` crosses zero at -0.0487.
5. **Marker redesign investigation** — discovered `cei_raw` crossing `cei` (EMA) as
   better signal trigger. On Mar 2, `cei_raw` (+0.1162) < `cei` (+0.1904) — this
   crossing catches the ABCAPITAL signal 2 bars earlier than zero-crossing.
6. **Backtester overhaul (v2.0)**:
   - **Watchlist Support**: `--watchlist` flag for batch processing with tabular results.
   - **Conditional Exit**: Exit on Supply signals inside VA; trailing CWVAP stop above VA.
   - **VA Entry Filter**: `--exclude-va` flag to skip entries when price is within VA.
   - **Export Refinement**: All results land in root `data/` directory.

**Completed (2026-03-13):**
1. Delivery-Profile Value Area boundaries (`va_high`/`va_low`) in `cwvap.py`
2. VA Spring → Marker Modifier (Design A): bypasses EMA on spring breakout bars
3. Signal trigger: slope → CEI zero-crossings
4. CEI max_value recalibration (P90), weight rebalance (PSZ 15%, MCS 30%)
5. Cumulative divergence (rolling 20-bar sum), RSZ–PSZ confirmation (0.2× dampening)
6. Position score reduced 10%→5%

**Next:**
- Run 5 signal quality report with noise-reduced CEI config
- Multi-symbol batch backtest (NIFTY 500) with CEI threshold exit
- Parameter sensitivity sweep (cei_exit_threshold 0.05-0.12, stop_cwvap_pct -1% to -5%)
- Risk metrics (Sharpe, Calmar, monthly returns)
- Intensity calculation to move into cei.py (parked)
- Trend-riding state machine (Phase 4)

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
structure_mult = (0.3 + 0.7×coherence) × (0.3 + 0.7×cwc)  # 0.09–1.0
cei_raw        = evidence × structure_mult
cei            = EMA(cei_raw, span=14)
cei_slope      = linreg_slope(cei, window=5)

# Marker emission (with spring override + min_crossing_gap):
spring_score   = va_dwell(60) × breakout_direction     # computed separately
if |spring_score| >= 0.20:
    marker checks cei_raw zero-crossing               # bypass EMA lag
else:
    marker checks cei zero-crossing                    # normal smoothed path
# Additional filter: |check_now| >= min_crossing_gap (0.005) to suppress weak crossings
```

### Feature weights (updated 2026-03-14 — noise reduction rebalance)
2d weights halved → shifted to 9d for stability. `cumul_divergence` boosted (most stable
feature at 3.7% sign-flip rate). Total feature weights = 1.00, position = +0.05.

| Group | Weight | Features | max_value |
|-------|--------|----------|-----------|
| Price Δ | 15% | PSZ Δ 2d(3%)/4d(6%)/9d(6%) | 0.25/0.45/0.55 (P90) |
| Delivery Δ | 20% | RSZ Δ 2d(4%)/4d(8%)/9d(8%) (dampened 0.2× when PSZ disagrees) | 0.23/0.35/0.40 (P90) |
| Money Flow Δ | 30% | MCS Δ 2d(7%)/4d(12%)/9d(11%) | 0.50 |
| Cumul Divergence | 20% | rolling 20-bar sum(accum_div - distrib_div) | 0.35 (P90) |
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
- Green/red histogram (area fill, uses `cei_raw` data) + teal CEI line (EMA) +
  orange `cei_raw` line + purple dashed slope line + zero ref
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
- **Phase 3.5** ✓ — CEI signal fine-tuning + marker redesign (COMPLETE)
  - ✓ Signal trigger: slope zero-crossings → CEI zero-crossings
  - ✓ max_value recalibration (P90-based from Nifty 50 empirical data)
  - ✓ Weight rebalance: PSZ 25%→15%, MCS 20%→30%
  - ✓ Cumulative divergence (rolling 20-bar sum replaces single-bar)
  - ✓ RSZ–PSZ per-window confirmation (dampening 0.2× on sign disagreement)
  - ✓ Delivery-Profile VA boundaries (`va_high`/`va_low`) — TPO 70% delivery volume area
  - ✓ VA Spring → Marker Modifier (Design A): bypasses EMA on spring breakout bars
  - ✓ Position score reduced 10%→5%
  - ✓ VA trendlines on OHLC chart (purple dashed lines, `cbVA` toggle)
  - ✓ `cei_raw` plotted on CEI panel (orange line), histogram switched to `cei_raw` data
  - ✓ **Assister markers: `cei_raw` crosses `cei` (EMA)** — Nifty 500 validation showed
    crossing method doesn't outperform zero-crossing as primary signal (50% vs 50% daily,
    1.5pp worse on weekly). Implemented as **assister overlay** (circle "A" markers) that
    leads primary signals by 1-3 bars. Primary zero-crossing unchanged. CWVAP gate for
    Supply assisters validated (+5.9pp on weekly).
  - ✓ **CEI noise reduction (5-layer fix)** — 2d→9d weight shift, structure floors
    0.5→0.3 (min mult 0.25→0.09), EMA span 10→14, cumul_divergence 15%→20%,
    `min_crossing_gap: 0.005` filter. Primary signals -64%, RELIANCE PF 2.03→3.63.
  - ○ Intensity calculation in cei.py (parked)
- **Phase 4** — Future: Trend-riding state machine (entry vs continuation markers),
  phase machine + intensity labels for screener

---

## Marker Redesign Investigation (2026-03-14) — CONCLUDED

### Problem
CEI zero-crossing markers lag by 1-3 bars. Example: ABCAPITAL Supply expected
Mar 2 (price below `va_low`), but zero-crossing doesn't fire until Mar 4.

### Investigation: `cei_raw` crosses `cei` (EMA) as primary replacement
Tested on Nifty 50 (promising), then validated on **Nifty 500** (daily + weekly).

### Nifty 500 validation results (daily, 57,965 signals)

| | Proposed (raw×EMA) | Current (zero-cross) |
|---|---|---|
| Demand 5d hit | 50.49% | 49.47% |
| Supply 5d hit | 49.61% | 50.20% |
| Overall | 50.06% | 49.71% |

**Conclusion:** No meaningful improvement. Crossing generates 55% more signals
without proportional quality gain. Zone classification (full vs assister) works for
Supply (+2.4pp) but is **inverted** for Demand (assisters outperform full).

### Nifty 500 validation results (weekly, 12,092 signals)

| | Proposed | Current |
|---|---|---|
| Demand 5w hit | **55.30%** | 54.95% |
| Supply 5w hit | 47.39% | 47.44% |
| Overall | 51.41% | **52.57%** |

Current method wins overall on weekly. Weekly Demand at 55%+ is the strong finding.

### Outcome: Assister overlay (implemented 2026-03-14)
Raw×EMA crossings implemented as **assister markers** (circle "A" markers) that
visually lead primary zero-crossing signals by 1-3 bars. Primary logic unchanged.

- `_overlay_assister_signals()` in `cei.py` — scans for raw×EMA crossings on bars
  without a primary signal, respecting shared cooldown with primaries
- Config: `cei.markers.assister` in YAML — `enabled`, `cooldown_bars`, `min_gap`,
  `supply_below_cwvap`
- JS: circle markers with "A" label, lighter colours (#66bb6a / #ef9a9a)
- Screener: unaffected (only matches "Demand" / "Supply" exactly)
- ABCAPITAL validated: Supply_Assister fires Mar 2 (primary fires Mar 4)

---

## CEI Noise Reduction Investigation (2026-03-14) — COMPLETE

### Problem
`cei_raw` flickered excessively — 40-47 zero-crossings per 300 bars (once every 6-7
bars), producing too many low-conviction markers despite cooldown filtering.

### Root Causes (empirical analysis on 5 Nifty 50 symbols, 1,410 bars)
1. **2d deltas dominate noise**: `rsz_delta_2d` flips sign 26.6% of bars, `mcs_delta_2d`
   24.9%, `psz_delta_2d` 20.6%. Together carry 26% of CEI weight from the noisiest sources.
2. **Structure multiplier floor too generous**: At 0.5/0.5, min mult = 0.25. Even choppy,
   low-coherence bars pass 25% of evidence through. Median mult was 0.547 — floor barely
   suppresses anything.
3. **Weak crossings dominate**: 12-17% of bars had `|cei_raw| < 0.02`. These produce
   zero-crossings that look like signals but carry no directional conviction.

### Five-Layer Fix (all config-driven)

| # | Change | Config Key | Before → After | Mechanism |
|---|--------|------------|----------------|-----------|
| 1 | `min_crossing_gap` | `markers.min_crossing_gap` | (new) → 0.005 | Crossing must exceed minimum `|cei|` to fire |
| 2 | 2d→9d weight shift | `cei.features.*_delta_2d/9d` | 2d=26%, 9d=13% → 2d=14%, 9d=25% | Smooths evidence at source |
| 3 | Structure floors | `structure_multiplier.coherence_floor/cwc_floor` | 0.5/0.5 → 0.3/0.3 | Min mult 0.25→0.09, crushes noise in choppy regimes |
| 4 | EMA span | `cei.ema_span` | 10 → 14 | +2 bars lag (mitigated by spring override) |
| 5 | Cumul divergence boost | `cei.features.cumul_divergence.weight` | 0.15 → 0.20 | Anchors CEI toward rolling trend (3.7% flip rate) |

Code change: 3 lines in `_generate_cei_signals()` in `cei.py` — reads `min_crossing_gap`
from config and adds `abs(check_now) >= min_gap` to both Demand and Supply crossing checks.

### Results (5 symbols pooled, 2025-01-01 onwards)

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Raw sign flips | 202 | 186 | -8% |
| EMA zero-crossings | 121 | 91 | **-25%** |
| Primary signals (w/ cooldown) | 98 | 35 | **-64%** |
| Total signals (incl. assisters) | 363 | 288 | -21% |

### Backtest Validation (2024-01-01 to 2026-03-13, --chase)

| Stock | Before P&L | After P&L | Before PF | After PF | Before WR | After WR |
|-------|-----------|-----------|-----------|----------|-----------|----------|
| RELIANCE | +14.71% | **+44.96%** | 2.03 | **3.63** | 33.3% | **60.0%** |
| HDFCBANK | -7.94% | **-0.05%** | 0.51 | **1.00** | 50.0% | **57.9%** |

### Key Insight
Fewer weak crossings means entries happen at genuinely directional moments. The noise
reduction didn't just cut signal count — it dramatically improved signal quality by
filtering out the "indecisive zone" where `cei` oscillates near zero without commitment.

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
- **Run 5 signal quality report** — with noise-reduced CEI config (high priority)
- **Multi-symbol batch backtest** — loop over NIFTY 500, aggregate statistics post noise reduction
- **Trend-riding state machine** — After entry signal, track "Active Long/Short" state.
  While price > CWVAP (Demand) or < CWVAP (Supply), subsequent slope crossings show
  continuation markers (different shape/color) instead of new entry signals. Exit state
  when close crosses CWVAP against the trade. Weekly regime can serve as confirmation
  layer (weekly CEI too laggy, but weekly regime is stable). See GLENMARK Feb 16-Mar 12
  example: 3 Demand markers fired in a single +11% trend that never dropped below CWVAP.
- **Supply exit fine-tuning** — CEI MFE +4.43% but exits at -0.43%. Addressed by
  CEI threshold exit (0.08) — now implemented and default. See `backtest_handoff.md`.
- **Add `accum_divergence` factor** — `max(0, RSZ) × max(0, -PSZ)`: positive when price↓ + delivery↑ (accumulation).
  `directional` scoring (Demand wants positive, Supply wants negative). Discovered via FACT investigation
  (Feb-Mar 2026): RSZ rising to +0.26 during price decline was missed because PSZ/RSZ use `abs_higher_is_better`
  (direction-agnostic). Requires weight rebalancing + signal quality re-run.
- **Multi-thread signal quality report** — per-symbol runs are independent/IO-bound, use ThreadPoolExecutor
- **Tier-specific weights** — different factor weights for Large/Mid/Small/Micro tiers

## Dev Notes
- Always use `venv/bin/python3` to run scripts
- Always use latest versions of external libs — verify availability before use
- Flush `de:*` Redis cache after any scoring/weight change
- Re-run `signal_quality_report.py` after any scoring change to measure impact
