# Cumulative Evidence Index (CEI) — Design & Implementation Plan

> **Goal:** Replace single-bar isolated scoring markers with a **cumulative, context-aware index** that detects institutional accumulation/distribution pressure building over time, and signals only when that pressure releases.

> **Motivation:** FACT case study (Feb–Mar 2026) — RSZ rose from -0.11 → +0.26 over 12 bars while price fell. Every bar was marked Supply because momentum factors dominate per-bar. The undercurrent was invisible.

---

## Phase 1: Feature Engineering — Accumulation Divergence + CEI Computation

### 1.1 Definition

CEI is a **rolling net-directional-evidence score** on a signed axis centered at zero:
- **Positive** → Demand evidence building (accumulation)
- **Negative** → Supply evidence building (distribution)
- **Near zero** → neutral

**Core formula:**
```
CEI_raw[t] = Σ( w_i × signed_score_i[t] )     # per-bar signed evidence
CEI[t]     = EMA(CEI_raw, span=N)               # smoothed cumulative index
CEI_slope  = linreg_slope(CEI, window=5)        # momentum of the index itself
```

**All parameters are config-driven** (YAML) for future UI exposure via settings screen.

### 1.2 CEI Feature Inventory

Features available at the scoring stage (Module 7), grouped by role:

#### A: Price Momentum
| Feature | Column | Typical Range | Signed for CEI |
|---------|--------|--------------|----------------|
| PSZ Δ 2d | `psz_delta_2d` | -0.3 to +0.3 | Positive = Demand |
| PSZ Δ 4d | `psz_delta_4d` | same | same |
| PSZ Δ 9d | `psz_delta_9d` | same | same |

#### B: Delivery Momentum
| Feature | Column | Typical Range | Signed for CEI |
|---------|--------|--------------|----------------|
| RSZ Δ 2d | `rsz_delta_2d` | -0.3 to +0.3 | Positive = Demand |
| RSZ Δ 4d | `rsz_delta_4d` | same | same |
| RSZ Δ 9d | `rsz_delta_9d` | same | same |

#### C: Price-Delivery Alignment
| Feature | Column | Typical Range | Signed for CEI |
|---------|--------|--------------|----------------|
| MCS Δ 2d | `mcs_delta_2d` | -1 to +1 | Positive = Demand |
| MCS Δ 4d | `mcs_delta_4d` | same | same |
| MCS Δ 9d | `mcs_delta_9d` | same | same |

#### D: Accumulation Divergence (NEW)
| Feature | Column | Range | Signed for CEI |
|---------|--------|-------|----------------|
| Net Divergence | `accum_div - distrib_div` | -0.15 to +0.15 | Positive = accumulation (price↓ + delivery↑) |

```python
# New columns in analysis.py:
accum_div   = max(0, rdv_slope_z) × max(0, -price_slope_z)   # price↓ + delivery↑
distrib_div = max(0, -rdv_slope_z) × max(0, price_slope_z)   # price↑ + delivery↓
```

Meaningful range: PSZ/RSZ values are ±0.4 max, crossovers at 0 are significant. The 0.2–0.4 range is where strong signals live. Max divergence product ≈ 0.4 × 0.4 = 0.16 theoretical, ~0.10 practical.

#### E: Macro Volume
| Feature | Column | Typical Range | Signed for CEI |
|---------|--------|--------------|----------------|
| CDVL | `cdvl` | -1 to +5 | Positive = Demand (delivery velocity rising) |

#### F: Directional Levels
| Feature | Column | Range | Signed for CEI |
|---------|--------|-------|----------------|
| PSZ | `price_slope_z` | -0.4 to +0.4 | Positive = price rising (Demand) |
| RSZ | `rdv_slope_z` | -0.4 to +0.4 | Positive = delivery rising (Demand) |

#### G: Structural Coherence (Cross-Window Confirmation)
| Feature | Column | Range | Use in CEI |
|---------|--------|-------|------------|
| CWC | `cwc` | -1 to +1 | **Conviction multiplier**: high CWC = all delivery windows aligned → amplify CEI |
| Coherence | `coherence` | 0 to 1 | **Confidence multiplier**: high coherence = multi-timeframe trend alignment → amplify CEI |

CWC and Coherence are the backbone of the multi-window anchor thesis. Rather than contributing signed direction, they serve as **multipliers** on the raw CEI — when all delivery windows agree (high CWC) and timeframes align (high coherence), we have higher confidence that the directional evidence is real:

```python
# Coherence-weighted CEI:
coherence_mult = 0.5 + 0.5 × coherence      # range: 0.5 → 1.0
cwc_mult       = 0.5 + 0.5 × max(0, cwc)    # range: 0.5 → 1.0
structure_mult = coherence_mult × cwc_mult    # range: 0.25 → 1.0

CEI_raw[t] = Σ(w_i × signed_score_i) × structure_mult
```

This means: when CWC and Coherence are both high (all windows aligned), CEI gets full weight. When they're low/negative, CEI is dampened to 25-50% — preventing noisy divergences from generating false CEI build-up.

### 1.3 CEI Weight Allocation

| Group | Weight | Components | `max_value` | Rationale |
|-------|--------|------------|-------------|-----------|
| **Price Δ** | 25% | PSZ Δ 2d (10%), 4d (10%), 9d (5%) | 0.15 | Price inflection — fast-moving |
| **Delivery Δ** | 20% | RSZ Δ 2d (8%), 4d (8%), 9d (4%) | 0.15 | Institutional participation change |
| **Money Flow Δ** | 20% | MCS Δ 2d (8%), 4d (8%), 9d (4%) | 0.5 | Price-delivery correlation momentum |
| **Divergence** | 15% | net (accum-distrib) | 0.10 | **Key addition** — accumulation/distribution divergence |
| **Macro Volume** | 10% | CDVL | 0.15 | Long-term delivery velocity |
| **Levels** | 10% | PSZ (5%), RSZ (5%) | 0.4 | Signed slope magnitudes |

**Signed score per feature:**
```python
signed_score = clip(value / max_value, -1.0, +1.0)
```

### 1.4 Configuration (YAML)

All CEI parameters go in a new `cei` section of `default_rules.yaml`:

```yaml
cei:
  ema_span: 10                    # parameterized, exposable to settings UI later
  slope_window: 5                 # bars for CEI slope computation
  structure_multiplier:
    coherence_floor: 0.5          # minimum multiplier from coherence
    cwc_floor: 0.5                # minimum multiplier from CWC
  features:
    psz_delta_2d:  { weight: 0.10, max_value: 0.15 }
    psz_delta_4d:  { weight: 0.10, max_value: 0.15 }
    psz_delta_9d:  { weight: 0.05, max_value: 0.15 }
    rsz_delta_2d:  { weight: 0.08, max_value: 0.15 }
    rsz_delta_4d:  { weight: 0.08, max_value: 0.15 }
    rsz_delta_9d:  { weight: 0.04, max_value: 0.15 }
    mcs_delta_2d:  { weight: 0.08, max_value: 0.50 }
    mcs_delta_4d:  { weight: 0.08, max_value: 0.50 }
    mcs_delta_9d:  { weight: 0.04, max_value: 0.50 }
    divergence:    { weight: 0.15, max_value: 0.10 }
    cdvl:          { weight: 0.10, max_value: 0.15 }
    psz_level:     { weight: 0.05, max_value: 0.40 }
    rsz_level:     { weight: 0.05, max_value: 0.40 }
```

### 1.5 Implementation Steps

- [x] **1.5.1** Add `accum_div`, `distrib_div` columns to `analysis.py` → `compute_trend_participation()`
- [x] **1.5.2** Create `src/divergence_engine/cei.py` — new Module 7.5:
  - `compute_cei(df, config) → df` with columns: `cei_raw`, `cei`, `cei_slope`
  - Config-driven weights and parameters from YAML `cei` section
  - CWC × Coherence structure multiplier applied before EMA
- [x] **1.5.3** Add `cei` config section to `default_rules.yaml`
- [x] **1.5.4** Wire `compute_cei()` into `engine.py` after Module 7 (scoring)
- [x] **1.5.5** Add `cei`, `cei_raw`, `cei_slope` to `chart.py` UI_COLUMNS

### 1.6 Verification

- [x] Run engine on FACT — verify CEI shows Demand build-up from ~Feb 23 onward
  - ✓ CEI turns positive on Mar 10 (breakout day) — confirms thesis
- [ ] Run engine on 3–5 known accumulation/distribution examples
- [x] Dump CEI values alongside PSZ/RSZ/MCS to confirm divergence capture
- [ ] Verify CWC/Coherence multiplier dampens noisy signals

---

## Phase 2: CEI Chart Panel + Visualization

### 2.1 Definition

New sub-panel `"cei"` rendering CEI as a zero-centered line with context shading:
- Green zone when CEI > 0 → Demand evidence
- Red zone when CEI < 0 → Supply evidence
- Intensity via opacity (stronger CEI = more opaque)

### 2.2 Implementation Steps

- [x] **2.2.1** Add `"cei"` to `PANEL_DEFINITIONS` in `divergence_engine.js`
- [x] **2.2.2** Render CEI line (signed, zero-baseline) + histogram area fill (green >0, red <0)
- [x] **2.2.3** Add zero line reference
- [x] **2.2.4** Update legend to show CEI value + CEI Slope on crosshair
- [x] **2.2.5** Enable `"cei"` panel by default in `getActivePanels()` defaults

### 2.3 Verification

- [x] Load FACT on dashboard — visually confirm accumulation build-up (Feb 19 – Mar 9)
- [x] Verify CEI crosses zero or positive around Mar 10 (breakout day) ✓
- [x] Check 3–5 other stocks for visual coherence
  - HDFCLIFE investigated: CEI lag during sharp reversal explained by genuine delivery divergence + CDVL macro anchor
- [x] **Decision point:** Proceed to Phase 3. CDVL constant contribution flagged as tuning target.

---

## Phase 3: CEI-Based Markers

### 3.1 Definition

New marker logic based on CEI transitions instead of per-bar scoring:

| Marker | Condition | Meaning |
|--------|-----------|---------|
| **Demand** ↑ | CEI crosses above `+threshold` AND `cei_slope > 0` | Cumulative Demand evidence reached conviction |
| **Supply** ↓ | CEI crosses below `-threshold` AND `cei_slope < 0` | Cumulative Supply evidence reached conviction |

- Cooldown: no repeat markers within N bars (configurable)
- Marker shows CEI value as text (like current strength score)

### 3.2 Implementation Steps

- [x] **3.2.1** Add CEI signal generation to `cei.py`: `cei_signal` column
  - Threshold crossings (±0.01) + slope direction confirmation + 5-bar cooldown
  - `_generate_cei_signals()` function, config-driven via `cei.markers` in YAML
- [x] **3.2.2** Add threshold + cooldown config to YAML `cei` section
  - `demand_threshold: 0.01`, `supply_threshold: -0.01`, `slope_threshold: 0.0`, `cooldown_bars: 5`
- [ ] **3.2.3** Update marker logic in JS (option to toggle current vs CEI markers) — after exit strategy finalized
- [ ] **3.2.4** Decision: replace or supplement existing markers — after signal quality comparison complete

### 3.2.5 Value-Exit Strategy (added 2026-03-13)

Entry/exit rules for measuring CEI signal efficacy:

| Direction | Entry | Exit | Gate |
|-----------|-------|------|------|
| **Demand** | CEI crosses above +0.01 with positive slope | CEI < 0 AND close < CWVAP (compound) | None |
| **Supply** | CEI crosses below -0.01 with negative slope | CEI > 0 (CEI-only, no CWVAP cross) | Entry price must be < CWVAP |

Supply gate rationale: below-CWVAP entries = trend already bearish, CWVAP drifts down
(gravity works for you). Above-CWVAP Supply signals are shown but not acted on.

Supply CEI-only exit rationale: compound exit (CEI > 0 AND price > CWVAP) exits too late
for below-CWVAP entries — price has to travel the full distance back through CWVAP.
CEI flip alone signals distribution is over.

**Results (Nifty 50):**
- Demand: 41.4% hit, +3.42% avg ret, 17 bar hold (n=2,932)
- Supply: 37.8% hit, -0.43% avg ret, 8 bar hold (n=1,214)
- Combined: 40.4% hit, +2.29% avg ret (n=4,146)

**Supply issue:** MFE +4.43% but exits at -0.43%. Still giving back too much.
Next: study more examples visually, explore tighter exit timing.

### 3.3 Verification

- [x] Compare CEI markers vs current markers on FACT and other test stocks
- [x] Run `signal_quality_report.py` with CEI-based signals for hit-rate comparison
- [x] Fine-tune thresholds based on signal quality results (±0.01 winner)
- [ ] Supply exit strategy fine-tuning (in progress)

---

## Phase 4: Phase Machine + Intensity Labels (Future — post Phase 3 validation)

### 4.1 Definition

Explicit phase labels derived from CEI state, with **intensity scoring** for screener filtering:

| Phase | Condition | Intensity |
|-------|-----------|-----------|
| **Accumulating** | CEI > 0 and rising for 3+ bars | `intensity = abs(CEI) × cei_slope` |
| **Distributing** | CEI < 0 and falling for 3+ bars | `intensity = abs(CEI) × abs(cei_slope)` |
| **Neutral** | CEI near zero or directionless | 0 |
| **Breakout** ↑ | Accumulating → CEI spikes above threshold | `intensity` = CEI value at spike |
| **Breakdown** ↓ | Distributing → CEI plunges below threshold | `intensity` = abs(CEI) at plunge |

**Intensity** is a continuous float enabling:
- Screener filtering: `--min-cei-intensity 0.3`
- UI badge: color-coded by intensity band (low / medium / high / extreme)
- Sorting watchlist items by breakout/breakdown strength

### 4.2 Implementation — deferred until Phase 3 validates CEI markers.

---

## Resolved Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | EMA span | **Parameterized** in YAML, default 10. Uses trading days (not calendar). Exposable to settings UI in future. |
| 2 | Weight balance | **Rebalanced** to include CWC/Coherence as structure multipliers (not direct additive weights). Sum of additive feature weights = 100%. |
| 3 | Divergence max_value | **0.10** (practical max of RSZ×PSZ product). PSZ/RSZ meaningful range is 0.2–0.4, crossover at 0 is significant. |
| 4 | Replace vs supplement markers | **Deferred** to post-Phase 2 visual review. |
| 5 | Signal quality report | **Yes**, run post-Phase 3 to compare CEI signals vs current. |
| 6 | CWC + Coherence | **Included** as structural multipliers (not additive). They modulate CEI amplitude based on cross-window delivery alignment. |
| 7 | HDFCLIFE lag (Phase 2 review) | **Not a bug.** CEI detects genuine delivery divergence during price decline (RSZ deltas positive = institutions buying into weakness). CDVL constant +0.10 is a tuning target. Proceed to Phase 3. |
| 8 | Position awareness | **Added `position_score` (10% weight).** CWVAP pct-based sweet spot (-3% to 0%) + CPOC confirmation. CDVL reduced 10%→5%. Backtested on 20 symbols: Demand sweet spot 58→65% hit, Supply 45→48% hit, FP zones -32%. TCS false positive eliminated, FACT breakout preserved. |
| 9 | CDVL saturation decay | **Not needed.** Position score addresses the root cause (position blindness) more directly. CDVL decay alone hurt Demand hit rate without meaningful FP reduction. Simple weight reduction (10%→5%) sufficient. |
| 10 | CPOC vs CWVAP | **Both included.** CWVAP = 60% of position score (avg cost basis), CPOC = 40% (volume concentration). Best combo: "below CWVAP, above CPOC" = 69.6% Demand hit rate at 10d. |
| 11 | CEI threshold | **±0.01.** Tested 0.05, 0.015, 0.01. Lower threshold wins — slope direction does the real filtering, CEI level just needs to be marginally above zero. |
| 12 | Slope threshold | **0.0.** Tested 0.01 — no improvement over simple direction check (slope > 0 / < 0). |
| 13 | Supply CWVAP gate | **Below-CWVAP entries only.** 72% of Supply trades see price cross CWVAP on bar 1. Below-CWVAP = trend already bearish, gravity works for you. Above-CWVAP Supply signals shown but not acted on. |
| 14 | Asymmetric exit | **Demand: compound (CEI < 0 AND close < CWVAP). Supply: CEI-only (CEI > 0).** Compound exit too slow for Supply below-CWVAP entries — price must travel full distance back through CWVAP. CEI flip alone signals distribution over. |

---

## Execution Plan

```
Phase 1   (Complete) → Feature engineering + CEI computation + verify on FACT ✓
Phase 2   (Complete) → Chart panel + visual validation → decision point ✓
Phase 2.5 (Complete) → Position score tuning (CWVAP/CPOC + CDVL rebalance) ✓
Phase 3   (Active)   → CEI-based markers + value-exit strategy (Supply tuning in progress)
Phase 4   (Future)   → Phase machine + intensity labels for screener
```
