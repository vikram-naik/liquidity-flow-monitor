# DIVERGENCE ENGINE
---

## CONTEXT & OBJECTIVE

You are building a **stock trend analysis engine** for NSE (India) EOD data. The system analyses OHLC + Volume data with Delivery Quantity % to classify trend state, accumulation, distribution, and divergence — each with a probability/confidence score.

The engine must be modular, well-documented, and produce a final annotated price chart with all markers and a DVL ledger output.

---

## INPUT DATA SPECIFICATION

```
Source: NSE EOD CSV / DataFrame
Database: SQLite
schema:@liquidity-flow-monitor.db
table: nse_delivery_log - refer (@src/database.py)

The data is downloaded and persisted by @src/agents/nse_agent.py & @src/agents/nse_indices_agent.py
```

---

## MODULE ARCHITECTURE

Build the following modules in sequence. Each module must be a separate class or function group. Do not proceed to the next module until the current one is complete and unit-tested with sample data.

There is existing code in @src - which should be analyzed and utilized as per the requirements.
We have existing package structure for api, cache, analysis, and web
For DB access, we should centralize using repository pattern for all DML & select operations.

---

### MODULE 1 — BASE CALCULATIONS
**File: `base_calc.py`**

```
1.1 ATR Calculation
    - True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    - ATR_20 = 20-period Wilder smoothed average of TR

1.2 Relative Delivery Volume (RDV)
    - RDV = delivery_qty / rolling_mean(delivery_qty, 20)
    - Represents delivery participation relative to its own norm
    - RDV > 1.5 = elevated institutional participation
    - RDV < 0.5 = below-average commitment

1.3 Money Flow Multiplier (MFM) - use true high/low
    - MFM = ((close - true_low) - (true_high - close)) / (true_high - true_low)
    - Range: -1 to +1
    - Handles high == low edge case: MFM = 0

1.4 Typical Price
    - TP = (high + low + close) / 3

1.5 MFM_TP (Adjusted MFM)
    - MFM_TP = MFM × typical_price
    - Used as primary input to MCS
```

---

### MODULE 2 — ROLLING ANCHOR DVL LEDGER
**File: `dvl_ledger.py`**

Compute 4 rolling anchor windows: [10, 30, 60, 120] days.

For each window `n` in [10, 30, 60, 120]:

```
2.1 DVL (Delivery Volume Ledger)
    - DVL_n = rolling_sum(delivery_qty, n)
    - DVL_rate_n = DVL_n / n  (avg daily delivery — normalised for comparability)

2.2 DVL Velocity
    - Velocity_n = slope of DVL_n over last (n/2) bars
    - Use linear regression slope (numpy.polyfit degree=1)
    - Normalise: Velocity_n_norm = Velocity_n / mean(DVL_rate_n, 60)

2.3 Anchor Cost Basis
    - Price_anchor_n = close n bars ago (the cohort's entry reference)
    - Price_distance_n = (close - Price_anchor_n) / ATR_20
    - Positive = price above that cohort's cost basis
    - Negative = price below

2.4 ARS — Anchor Relative Strength
    - Expected_DVL_n = mean(DVL_rate_n, 20) × n
    - ARS_n = DVL_n / Expected_DVL_n
    - ARS > 1.0 = overperforming expected delivery
    - ARS < 1.0 = underperforming

2.5 PDD — Price-DVL Divergence
    - Price_move_n = (close - close_n_bars_ago) / ATR_20
    - DVL_bias_n = DVL_rate_n / mean(DVL_rate_n, 20) - 1
    - PDD_n = Price_move_n - DVL_bias_n
    - Positive PDD = price moved more than delivery justifies (extension risk)
    - Negative PDD = delivery exceeds price movement (accumulation signal)

2.6 DVL Ledger Output (pandas DataFrame, one row per date):
    Columns: date, DVL_10, DVL_30, DVL_60, DVL_120,
             DVL_rate_10..120, Velocity_10..120 (normalised),
             Price_distance_10..120, ARS_10..120, PDD_10..120
```

**Gradient Vector (for trend shape classification):**
```
Gradient = [Velocity_10_norm, Velocity_30_norm, Velocity_60_norm, Velocity_120_norm]

Classify gradient shape:
  uptrend_forming     : V10 > 0, V30 > 0, V60 <= 0, V120 <= 0
  uptrend_mature      : all > 0, V10 >= V30 >= V60
  uptrend_exhausting  : V10 < V30, V60 > 0, V120 > 0
  distribution        : V10 < 0, V30 near 0, V60 > 0, V120 > 0
  accumulation        : V10 > 0 and rising (last 5d slope positive), V30..V120 <= 0
  downtrend_forming   : V10 < 0, V30 < 0, V60 >= 0
  downtrend_mature    : all < 0
  recovering          : V10 > 0, V30 turning (slope of V30 positive), V60 < 0
  sideways            : all near zero (abs value < 0.2)
```

---

### MODULE 3 — COMPOSITE VWAP (CWVAP)
**File: `cwvap.py`**

```
3.1 Individual Anchor DVWAP
    For each window n in [10, 30, 60, 120]:
      DVWAP_n = rolling_sum(typical_price × volume, n) / rolling_sum(volume, n)
      
    Note: Use volume (total traded) for VWAP calculation, delivery_qty for DVL.
    These are intentionally different — VWAP = price discovery, DVL = commitment.

3.2 Individual Anchor POC
    For each window n:
      - Create price histogram with bins of ATR_20 / 4 width
      - Weight each bin by delivery_qty (not total volume)
      - POC_n = price bin midpoint with highest cumulative delivery_qty
      
    Implementation note: 
      Use rolling window of last n bars, compute histogram per row.
      For performance, use a loop with vectorised histogram per window.

3.3 CWVAP — Composite Weighted VWAP
    Weight_n = DVL_rate_n × (1 / (1 + abs(close - DVWAP_n) / ATR_20))
    
    CWVAP = sum(DVWAP_n × Weight_n for n in windows) / sum(Weight_n)
    
    CWVAP_slope = slope of CWVAP over last 10 bars (linear regression)
    CWVAP_slope_norm = CWVAP_slope / ATR_20  (normalised, ATR units per bar)

3.4 CPOC — Composite POC
    Method A — Weighted average:
      CPOC_weight_n = delivery_qty_at_POC_n × DVL_rate_n
      CPOC = sum(POC_n × CPOC_weight_n) / sum(CPOC_weight_n)
    
    Method B — POC Spread (regime signal):
      POC_spread = std([POC_10, POC_30, POC_60, POC_120]) / ATR_20
      POC_spread < 0.5   → strong confluence (all cohorts agree on value)
      POC_spread 0.5–1.5 → moderate agreement
      POC_spread > 1.5   → cohort disagreement (transition / complex market)

3.5 Composite Value Area
    For each window n:
      Price_std_n = rolling_std(close, n)
      CVAH_n = DVWAP_n + Price_std_n
      CVAL_n = DVWAP_n - Price_std_n
    
    CVAH = max delivery-weighted CVAH across windows
    CVAL = min delivery-weighted CVAL across windows
    VA_width = (CVAH - CVAL) / ATR_20

3.6 Price Location Classification
    Compare close vs CWVAP vs CPOC:
    
    above_value       : close > CWVAP and close > CPOC
    at_value          : abs(close - CWVAP) < 0.3 × ATR_20
    below_value       : close < CWVAP and close < CPOC
    extended_above    : close > CPOC > CWVAP (price extended, distribution risk)
    reclaiming_value  : close crossed above CWVAP in last 3 bars
    losing_value      : close crossed below CWVAP in last 3 bars
    in_value_area     : CVAL <= close <= CVAH
```

---

### MODULE 4 — CROSS-WINDOW COHERENCE (CWC)
**File: `cwc.py`**

```
4.1 CWC — Cross-Window Coherence
    Compute day-over-day changes in DVL_rate for each window:
      dDVL_rate_n = DVL_rate_n.diff()
    
    CWC = mean pairwise Pearson correlation of 
          [dDVL_rate_10, dDVL_rate_30, dDVL_rate_60, dDVL_rate_120]
          over rolling 10-bar window
    
    High CWC (>0.70)   → all cohorts moving together → trend is real and coherent
    Medium CWC (0.3–0.7) → partial alignment
    Low CWC (<0.30)    → cohorts diverging → transition / noise
    Negative CWC       → cohorts opposing each other → accumulation or distribution

4.2 Pairwise Coherence
    C_10_30  = rolling 10-bar Pearson(dDVL_rate_10, dDVL_rate_30)
    C_30_60  = rolling 10-bar Pearson(dDVL_rate_30, dDVL_rate_60)
    C_60_120 = rolling 10-bar Pearson(dDVL_rate_60, dDVL_rate_120)
    
    Interpretation:
      C_10_30 breaks first  → early warning of trend change (short-term turning)
      C_60_120 breaks       → major regime change underway

4.3 CWC Trend
    CWC_delta = CWC - CWC.shift(5)
    Rising CWC   (CWC_delta > 0.1) = cohorts aligning = trend strengthening
    Falling CWC  (CWC_delta < -0.1) = cohorts diverging = trend weakening
```

---

### MODULE 5 — MCS (MONEY COMPOSITE SCORE)
**File: `mcs.py`**

```
5.1 MCS Definition
    MCS = 30-day rolling Pearson correlation between:
      Series A: typical_price (TP = (H+L+C)/3)
      Series B: RDV (Relative Delivery Volume)
    
    MCS = rolling_pearson(TP, RDV, window=30)
    
    Interpretation:
      MCS > +0.60  → price rising WITH delivery participation → genuine trend
      MCS +0.20 to +0.60 → moderate confluence
      MCS -0.20 to +0.20 → no relationship → sideways / noise
      MCS -0.20 to -0.60 → price and delivery diverging → distribution or accumulation
      MCS < -0.60  → strong divergence → high-probability reversal or absorption zone

5.2 MCS_MFM (Secondary MCS using MFM_TP)
    MCS_MFM = rolling_pearson(MFM_TP, RDV, window=30)
    
    This measures whether intraday buying/selling pressure (MFM) 
    is aligned with delivery commitment (RDV)
    
    MCS_MFM > 0.5  → intraday buyers are also committing to delivery → strong bull
    MCS_MFM < -0.5 → intraday selling with delivery → strong distribution

5.3 MCS_delta
    MCS_delta = MCS - MCS.shift(5)
    
    Tracks whether the price-delivery relationship is strengthening or breaking.
    MCS_delta crossing zero = potential regime change point.

5.4 MCS Composite Signal
    MCS_composite = (MCS × 0.6) + (MCS_MFM × 0.4)
    
    Range: -1 to +1
    This is your primary money flow health score.
```

---

### MODULE 6 — DIVERGENCE ENGINE
**File: `divergence.py`**

```
6.1 Price vs RDV Divergence
    For each rolling window n:
    
    Price_momentum_n = (close - close.shift(n)) / ATR_20
    RDV_momentum_n   = (RDV - RDV.shift(n)) / rolling_std(RDV, n)
    
    Divergence_n = sign mismatch between Price_momentum_n and RDV_momentum_n
    
    Bullish Divergence : Price_momentum_n < 0 AND RDV_momentum_n > 0.5
                         (price falling, delivery rising → accumulation)
    Bearish Divergence : Price_momentum_n > 0 AND RDV_momentum_n < -0.5
                         (price rising, delivery falling → distribution)

6.2 CWVAP Divergence
    CWVAP_momentum = (CWVAP - CWVAP.shift(10)) / ATR_20
    Price_momentum  = (close - close.shift(10)) / ATR_20
    
    CWVAP_divergence = Price_momentum - CWVAP_momentum
    
    Positive CWVAP_divergence = price running ahead of composite fair value
    Negative CWVAP_divergence = price lagging composite fair value

6.3 MCS Divergence
    MCS_divergence_signal:
      Trigger when MCS_composite crosses below -0.3 
      while close > CWVAP (price above value but money flow deteriorating)
      → Bearish Divergence Marker
      
      Trigger when MCS_composite crosses above +0.3 
      while close < CWVAP (price below value but money flow improving)
      → Bullish Divergence Marker

6.4 Multi-Window Divergence Confluence
    For each bar, count how many of the 4 windows show divergence in same direction:
    
    Divergence_count_bull = count of windows with Bullish Divergence
    Divergence_count_bear = count of windows with Bearish Divergence
    
    Divergence_confluence_score = max(Divergence_count_bull, Divergence_count_bear) / 4
    Divergence_direction = 'bullish' if bull > bear else 'bearish' if bear > bull else 'none'

6.5 Divergence Probability Score
    divergence_probability = sigmoid(
        0.3 × Divergence_confluence_score +
        0.3 × abs(MCS_composite) +
        0.2 × abs(PDD weighted average across windows) +
        0.2 × (1 - CWC)  # low coherence amplifies divergence signal
    )
    
    Where sigmoid(x) = 1 / (1 + exp(-5 × (x - 0.5)))
    Output range: 0.0 to 1.0
```

---

### MODULE 7 — STATE CLASSIFIER
**File: `classifier.py`**

```
7.1 Input Features (computed from all prior modules):
    - gradient_shape          (categorical from Module 2)
    - CWC                     (float)
    - CWC_delta               (float)
    - MCS_composite           (float)
    - POC_spread              (float)
    - price_location          (categorical from Module 3)
    - CWVAP_slope_norm        (float)
    - Divergence_direction    (categorical)
    - divergence_probability  (float)
    - ARS_weighted_avg        (mean of ARS_10..120, weighted by DVL_rate)
    - PDD_weighted_avg        (mean of PDD_10..120, weighted by DVL_rate)

7.2 State Classification (Rule-Based Scoring)

    For each bar compute a score vector across 6 states:
    States: [UPTREND, DOWNTREND, ACCUMULATION, DISTRIBUTION, SIDEWAYS, RECOVERY]

    Score each state using weighted evidence:

    UPTREND score:
      + 2.0 if gradient_shape in ['uptrend_mature', 'uptrend_forming']
      + 1.5 if CWC > 0.6
      + 1.5 if MCS_composite > 0.5
      + 1.0 if price_location == 'above_value'
      + 1.0 if CWVAP_slope_norm > 0.3
      + 0.5 if ARS_weighted_avg > 1.1
      - 1.0 if divergence_direction == 'bearish' and divergence_probability > 0.6

    DOWNTREND score:
      + 2.0 if gradient_shape in ['downtrend_mature', 'downtrend_forming']
      + 1.5 if CWC > 0.6
      + 1.5 if MCS_composite < -0.5
      + 1.0 if price_location == 'below_value'
      + 1.0 if CWVAP_slope_norm < -0.3
      - 1.0 if divergence_direction == 'bullish' and divergence_probability > 0.6

    ACCUMULATION score:
      + 2.0 if gradient_shape == 'accumulation'
      + 1.5 if divergence_direction == 'bullish' and divergence_probability > 0.5
      + 1.5 if PDD_weighted_avg < -0.5  (delivery > price justifies)
      + 1.0 if price_location in ['below_value', 'at_value']
      + 1.0 if MCS_composite between -0.2 and +0.4 (transitioning)
      + 0.5 if C_10_30 < 0.3 and C_60_120 > 0.3  (short-term cohorts diverging from long)

    DISTRIBUTION score:
      + 2.0 if gradient_shape == 'distribution'
      + 1.5 if divergence_direction == 'bearish' and divergence_probability > 0.5
      + 1.5 if PDD_weighted_avg > 0.5  (price > delivery justifies)
      + 1.0 if price_location in ['extended_above', 'above_value']
      + 1.0 if MCS_composite between -0.4 and +0.2 and CWVAP_slope_norm < 0.1
      + 0.5 if C_10_30 < 0.3 and C_60_120 > 0.3

    SIDEWAYS score:
      + 2.0 if gradient_shape == 'sideways'
      + 1.5 if CWC < 0.25
      + 1.0 if POC_spread < 0.5
      + 1.0 if abs(MCS_composite) < 0.2
      + 0.5 if VA_width < 1.5

    RECOVERY score:
      + 2.0 if gradient_shape == 'recovering'
      + 1.5 if divergence_direction == 'bullish' and divergence_probability > 0.4
      + 1.0 if price_location == 'reclaiming_value'
      + 1.0 if CWC_delta > 0.1
      + 0.5 if MCS_delta > 0.1

7.3 Probability Normalisation
    total_score = sum of all state scores
    
    probability_n = score_n / total_score   (softmax-style normalisation)
    
    predicted_state = state with highest probability
    confidence = probability of predicted_state
    
    Output per bar:
      {
        state: 'ACCUMULATION',
        confidence: 0.71,
        probabilities: {
          UPTREND: 0.08,
          DOWNTREND: 0.04,
          ACCUMULATION: 0.71,
          DISTRIBUTION: 0.09,
          SIDEWAYS: 0.06,
          RECOVERY: 0.02
        }
      }
```

---

### MODULE 8 — VISUALISATION ENGINE
**File: `chart.py`**

```
Use: fastapi + ligthweight-charts from TradeView
Look and Feel: Dark, Professional.

8.1 Main Chart Layout (4 panels):

Panel 1 (60% height) — Price + Levels:
  - OHLC candlesticks
  - CWVAP line (solid, weighted color: green if slope +ve, red if -ve)
  - CPOC line (dashed horizontal, redrawn each bar)
  - DVWAP_10 (thin dotted, light blue)
  - DVWAP_30 (thin dotted, blue)
  - DVWAP_60 (thin dotted, dark blue)
  - DVWAP_120 (thin dotted, navy)
  - CVAH / CVAL shaded zone (light gray band = composite value area)
  
  State Markers on candles:
    UPTREND marker     : small green triangle above candle
    DOWNTREND marker   : small red triangle below candle
    ACCUMULATION marker: small blue circle below candle
    DISTRIBUTION marker: small orange circle above candle
    RECOVERY marker    : small cyan diamond below candle
    
  Divergence Markers:
    Bullish Divergence : green arrow pointing up, below bar, 
                         opacity scaled by divergence_probability
    Bearish Divergence : red arrow pointing down, above bar,
                         opacity scaled by divergence_probability
    Only show if divergence_probability > 0.5
    
  Confidence Label:
    For each state marker, annotate with confidence % 
    if confidence > 0.65 (avoid clutter on low-confidence bars)

Panel 2 (15% height) — CWC + Pairwise Coherence:
  - CWC line (main, thick)
  - C_10_30, C_30_60, C_60_120 (thin lines)
  - Horizontal bands: 0.7 (green dashed), 0.3 (red dashed)
  - Background color: green tint if CWC > 0.7, red tint if CWC < 0.3

Panel 3 (15% height) — MCS:
  - MCS_composite line (primary signal)
  - MCS and MCS_MFM as thinner background lines
  - Zero line reference
  - Color fill: green above 0.3, red below -0.3, gray between
  - MCS_delta as bar chart overlay (secondary axis)

Panel 4 (10% height) — DVL Velocity Gradient:
  - Stacked area chart of Velocity_10, 30, 60, 120 (normalised)
  - Shows gradient shape visually across windows
  - Color by sign: green positive, red negative

8.2 DVL Ledger Export
  Export complete DVL ledger as CSV:
  All columns from Module 2 + Module 5 + state/probability output
  One row per trading date
  
  Filename: {ticker}_{start_date}_{end_date}_dvl_ledger.csv

8.3 Annotations Table (optional overlay or separate figure)
  Bottom table showing last bar's full state:
  
  | Metric          | Value    |
  | State           | ACCUMULATION |
  | Confidence      | 71%      |
  | MCS_composite   | -0.18    |
  | CWC             | 0.28     |
  | CWVAP           | 1842.50  |
  | CPOC            | 1835.00  |
  | POC_spread      | 0.9 ATR  |
  | Divergence      | Bullish 0.64 |
  | Gradient Shape  | accumulation |
```

---

## IMPLEMENTATION REQUIREMENTS

```
Language: Python 3.10+

Required libraries:
  pandas, numpy, fastapi, lightweight-charts (js)(https://unpkg.com/lightweight-charts@4.2.2/dist/lightweight-charts.standalone.production.js),
  scipy (for pearsonr, linregress), sklearn (optional, for sigmoid)

File structure to create:
  divergence_engine/
  ├── __init__.py
  ├── base_calc.py
  ├── dvl_ledger.py
  ├── cwvap.py
  ├── cwc.py
  ├── mcs.py
  ├── divergence.py
  ├── classifier.py
  ├── chart.py
  ├── engine.py          ← orchestrator, calls all modules in sequence
  └── utils.py           ← data loading, validation, helpers

engine.py interface:
  from divergence_engine.engine import DivergenceEngine

  engine = DivergenceEngine(ticker='RELIANCE', df=your_dataframe)
  results = engine.run()
  
  results contains:
    .ledger    → full DVL ledger DataFrame
    .states    → state + probability per bar
    .chart()   → renders the annotated chart
    .export()  → saves ledger CSV
```

---

## VALIDATION REQUIREMENTS

After building each module, validate with these checks before proceeding:

```
Module 1: RDV mean over any 20-day period should be ~1.0 by construction
Module 2: DVL_rate values must be positive; ARS should mean ~1.0 over long period
Module 3: CWVAP must always be between min and max of individual DVWAPs
Module 4: CWC must be bounded [-1, 1]; pairwise Pearson must be [-1, 1]
Module 5: MCS and MCS_MFM must be bounded [-1, 1]
Module 6: divergence_probability must be bounded [0, 1]
Module 7: All state probabilities must sum to 1.0 ± 0.001
Module 8: Chart must render without error on minimum 130 bars of data
           (120 for longest anchor + 10 buffer)

Edge cases to handle:
  - Zero volume days: skip or forward fill
  - high == low (no range candle): MFM = 0, ATR uses prior bar
  - Delivery_pct = 0 or 100: valid, do not filter
  - Fewer than 120 bars: raise ValueError with clear message
  - NaN propagation: forward fill after minimum warmup period (120 bars)
```

---

## EXECUTION SEQUENCE

Build and validate modules strictly in this order:
1. base_calc.py
2. dvl_ledger.py
3. cwvap.py
4. cwc.py
5. mcs.py
6. divergence.py
7. classifier.py
8. chart.py
9. engine.py (orchestrator, wire all modules)

Do not skip ahead. Each module's output is input to the next.

---

## FINAL OUTPUT

The completed engine should produce, for any NSE stock with 130+ bars of OHLC + delivery data:

1. **Annotated chart** with:
   - All 4 anchor DVWAPs + CWVAP + CPOC
   - Composite value area band
   - State markers per bar (trend/accumulation/distribution/recovery)
   - Divergence markers with probability-scaled opacity
   - Confidence scores on high-conviction bars
   - CWC, MCS, DVL Velocity panels

2. **DVL Ledger CSV** with all computed metrics per bar

3. **State summary** for most recent bar with full probability distribution

---
*Prompt version 1.0 — NSE Divergence Engine — Multi-Anchor Rolling Window System*
*Input: OHLC + Volume + Delivery % | Windows: 10, 30, 60, 120 days*