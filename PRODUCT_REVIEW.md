# Liquidity Flow Monitor — Functional Review

> **Reviewer perspective**: Senior CFA Charterholder & Quantitative Developer  
> **Scope**: Full-stack functional review — analytics engine, signal framework, data pipeline, screener, and dashboard UX.

---

## Executive Summary

**The LFM is one of the most intellectually honest retail-grade tools I've reviewed.** Its core thesis — *isolate institutional intent through delivery volume and directional conviction, anchored to structural pivots* — is sound, well-executed, and fills a genuine gap in the Indian equity analytics landscape.

The system does what it claims: **it marks what is happening and at what intensity, without predicting.** That philosophical discipline is its greatest strength.

Below I offer candid observations organized as **Strengths**, **Risks & Blind Spots**, and **Suggestions for the next evolution**.

---

## 1. What's Working Well (Strengths)

### 1.1 Delivery Volume as the Primary Signal — Correct Structural Bet

Most retail platforms use total traded volume, which is polluted by intraday speculators and algo-driven churn. NSE's delivery data is a unique Indian market edge — it represents shares that actually changed beneficial ownership and were held overnight. By anchoring the entire system to `delivery_qty` rather than `volume_total`, LFM structurally filters out noise at the data layer itself.

> **CFA Take**: This is analogous to how institutional flow desks separate "real money" (pension, mutual fund) from "fast money" (HFT, prop). The delivery filter achieves something similar at the retail level — a genuine analytical edge.

### 1.2 The Gap-Adjusted MFM — Technically Superior

The True Money Flow Multiplier correctly anchors to `Prev_Close` to account for overnight gaps. This is a meaningful improvement over the standard Chaikin MFM (which uses the day's OHLC range only and therefore misses gap conviction).

A stock that gaps up 5% and holds there all day registers MFM ≈ +1.0 in this system, correctly identifying aggressive overnight accumulation. Standard MFM would show a mid-range value, completely missing the institutional intent behind the gap.

### 1.3 The Day Zero Anchor — Regime-Aware Analysis

The auto-anchor algorithm (Volume-Confirmed Volatility Pivot) is the product's conceptual crown jewel. By resetting the measurement window to the structural pivot of the *current* institutional campaign, it avoids the classic flaw of running indicators over stale regimes.

The anchor scoring system (volume expansion × base tightness) intelligently prioritizes pivots with high institutional footprints. The manual override gives the experienced user the flexibility to correct edge cases.

### 1.4 Multi-Pillar Scoring System — Anti-Overfitting Design

The Coil (100-pt) and Ignition (110-pt) scoring systems use **multiple independent pillars** (Geometry, Dryness/Volume, Proximity, Ledger) with hard-trigger gates. This is architecturally superior to single-metric threshold systems because:

- Hard triggers act as **noise gates** (no signal passes without structural validity)
- Scoring pillars provide **granularity** (a 90-score Ignition is materially different from a 55-score one)
- The pillars are orthogonal enough to avoid multicollinearity

### 1.5 The Grind Marker — Captures Institutional Reality

The 3-day Composite Grind with its ghosting mechanic is an excellent innovation. Real institutional markups often happen over 3-5 days (not in a single explosive bar), and this marker captures exactly that pattern. The retroactive erasure of incomplete sequences keeps the historical chart honest.

### 1.6 Screener as a Diagnostic, Not a Trade Signal

The screener correctly populates watchlists rather than generating "buy signals." It answers *what happened today* across the universe, leaving the interpretation to the user. This is the correct UX for a tool that "marks, not predicts."

---

## 2. Risks & Blind Spots

### 2.1 Delivery Data Limitations — The Elephant in the Room

> [!WARNING]
> Delivery data is a **T+1 proxy**, not real-time institutional flow.

NSE reports delivery data in the bhavcopy after market close. This means:

- **Intra-day signals are impossible.** All LFM signals are inherently end-of-day.
- **Delivery attribution is anonymous.** We know *how much* was delivered, but not *who* delivered it. A 2Cr delivery day could be a single FII block or 10,000 retail investors holding overnight.
- **F&O hedging noise.** Stocks in the F&O segment can have artificially elevated delivery due to physical settlement obligations, arbitrage, and hedging. This can distort the MFM and DVL for derivative-heavy names.

**Suggestion**: Consider adding a "Delivery Quality" flag for F&O stocks during expiry weeks (last Thursday of month). Even a simple `is_expiry_week` boolean on the data would let the user mentally discount those signals.

### 2.2 Anchor Fragility — Single Point of Failure

The entire analytical framework (DVL, DAVWAP, all slopes, all intensity metrics) is anchored to a **single date**. This creates concentration risk:

- If the auto-anchor selects a sub-optimal pivot (which happens — that's why `FALLBACK_MIN` exists), **every downstream metric is contaminated**.
- Stocks in secular bear markets or prolonged distribution may never produce a valid breakout pivot, causing the system to fall back to the absolute 2-year low — a meaningless anchor.
- There's no mechanism for **anchor aging** or degradation. A 2-year-old anchor that was perfect at inception may be irrelevant today if the stock has completed its campaign and started a new one.

**Suggestion**: Consider implementing an **Anchor Health Score** that decays over time and alerts the user when the anchor is stale (e.g., >18 months old, or when the DVL has returned to zero and gone negative for >60 days since anchor).

### 2.3 No Distribution-Side Signals

The current signal set is **exclusively bullish**:
- **Coil** = Compression before rally
- **Ignition** = Breakout from value
- **Spring** = Reversal from deep discount
- **Grind** = Slow markup

There are no bearish equivalents. From a CFA perspective, this is a significant analytical gap. The system can tell you "smart money is accumulating" but cannot systematically flag:

- **Bearish Divergence** (price making new highs with DVL falling)
- **Distribution Markers** (high-volume expansion candles closing in the lower half of range near DAVWAP — the inverse of Ignition)
- **Exhaustion** (DVL flattening at extreme highs while price grinds higher)

> [!IMPORTANT]
> From the conversation history, I can see you previously explored Distribution (D) and Bearish Absorption (BD) signals. These appear to have been implemented in a `smart_money.py` module that no longer exists in the current codebase. **Revisiting distribution-side markers would significantly strengthen the product's analytical completeness.**

### 2.4 MCS Interpretation Nuance

The Money Capacity Score (30-day Pearson correlation between Typical Price and Relative Delivery Volume) is a good concept, but has a subtle interpretation trap:

- A **negative MCS** during a correction doesn't necessarily mean "overhead supply." It could simply mean volume dried up as price fell (normal behavior in healthy corrections where institutions quietly absorb).
- A **positive MCS** during a squeeze doesn't necessarily mean "strong trend participation." It could be short-covering driven.

The MCS is currently used as a binary "spark sensor," which is appropriate. Just be cautious about over-interpreting its magnitude.

### 2.5 Sector/Market Regime Blindness

The system analyzes each stock in complete isolation. There's no mechanism to:

- Compare a stock's flow dynamics against its sector peers
- Contextualize individual signals against broad market regime (e.g., an Ignition during a market-wide rally is less significant than one during a correction)
- Track sector rotation (money flowing out of IT into Metals, for example)

This is understandable for an MVP, but limits the "where is smart money flowing" thesis at the macro level.

### 2.6 Lookback Window Sensitivity

The screener uses `lookback_days=5` for signal detection, which is correct for latest-bar signals. However, the Grind marker requires 3 days of progressive confirmation. If the screener runs on a Monday and Day 1 (G1) was Friday, the 5-day window captures it. But edge cases around holidays, long weekends, or data gaps could cause the G1→G2→G3 chain to break silently.

---

## 3. Suggestions for the Next Evolution

### 3.1 Divergence Engine — The Missing Crown Jewel

The most powerful use of the DVL-to-Price relationship is **divergence detection**. Currently, the system shows both the DVL and price on the chart, but relies on the user's eye to spot divergences. This should be automated:

| Pattern | Definition | Significance |
|---------|-----------|--------------|
| **Bullish Divergence** | Price makes lower low, DVL makes higher low | Hidden accumulation during selloff |
| **Bearish Divergence** | Price makes higher high, DVL makes lower high | Distribution behind rising prices |
| **Confirmation** | Both price and DVL making new highs | Healthy trend, no red flags |

This would be the single highest-impact addition to the product and is a natural extension of the existing analytical framework.

### 3.2 Sector Flow Dashboard — Answering "Where" at Scale

To truly answer *where* smart money is flowing, the system needs a birds-eye view:

- **Sector Flow Heatmap**: Aggregate DVL slopes across sector constituents. If 35 of 50 Nifty Metal stocks have positive 5-day DVL slopes, Metal is the flow destination.
- **Rotation Matrix**: Track week-over-week changes in sector-level aggregate DVL to identify rotation patterns.

The data and infrastructure for this already exist (NIFTY sector watchlists, nse_indices_agent). It's a UI/aggregation layer.

### 3.3 Signal Backtesting Framework

The Coil, Ignition, Spring, and Grind markers are well-designed, but their efficacy has not been systematically validated. A lightweight backtest module that answers:

- *"What % of Ignition markers with score >70 were followed by a >5% move within 20 days?"*
- *"What is the median return 30 days after a Spring marker?"*

This doesn't require a full backtesting engine — a simple historical scan over the existing DB would suffice and would give the user confidence in the signal quality.

### 3.4 Anchor Lifecycle Management

Instead of a static anchor, evolve toward an **anchor lifecycle**:

1. **Born** — Auto-detected or manually set
2. **Active** — DVL rising from anchor, DAVWAP intact
3. **Aging** — DVL flattening, DAVWAP resistance emerging
4. **Expired** — DVL has gone negative from peak for >60 days, or price has broken below anchor low

This gives the user a **campaign health indicator** — knowing whether the institutional campaign that started at the anchor is still alive or has matured/failed.

### 3.5 Delivery Percentage as a Confirming Indicator

The `delivery_pct` field exists in the database but isn't used in any signal logic. Delivery percentage (Delivery Qty / Total Volume × 100) is a powerful confirming indicator:

- **High delivery %** (>60%) on a strong MFM day = High-conviction institutional accumulation
- **Low delivery %** (<20%) on a "breakout" day = Likely speculative/algo-driven, lower conviction

Adding a `delivery_pct` filter to the Ignition and Grind markers would improve signal quality.

### 3.6 Relative Strength Integration

A simple delivery-weighted relative strength metric (stock's DVL slope vs. NIFTY 500 aggregate DVL slope) would add enormous value. It answers: *"Is this stock receiving disproportionate institutional flow relative to the market?"*

---

## 4. Code Quality Observations

| Area | Observation |
|------|-------------|
| **Data Pipeline** | Clean, idempotent, with proper backfill and CA adjustment. The corporate action (split) handling is correct — adjusting pre-ex-date prices/volumes. |
| **Caching** | Redis cache with 86400s TTL and proper invalidation on upload. Well-implemented. |
| **API Design** | RESTful, clean separation of concerns. The `/lfm/api/analysis/stock/{symbol}` endpoint is the correct single-fetch design for the dashboard. |
| **Frontend** | LightweightCharts with synchronized crosshairs across 3 panes. Professional feel. The help modals and methodology page demonstrate good UX discipline. |
| **Screener** | Functionally correct. Scans NIFTY 500 + CORE watchlists, populates screener watchlists. The commit-per-symbol pattern prevents data loss on crashes. |
| **README** | Out of date — still references the original CME/Treasury/FRED architecture that has been decommissioned. Should be updated to reflect the current NSE-focused system. |

---

## 5. Bottom Line

**This is a well-conceived, well-executed analytical tool that correctly identifies its lane — observation and intensity measurement, not prediction.** The delivery-volume focus is a genuine edge in the Indian market. The anchor-based framework is conceptually superior to standard trailing indicators.

The most impactful next steps, ranked by effort-to-value ratio:

1. 🥇 **Divergence Engine** — Automate DVL-vs-Price divergence detection (medium effort, highest analytical value)
2. 🥈 **Distribution-Side Markers** — Complete the signal framework with bearish equivalents (medium effort, completes the product)
3. 🥉 **Sector Flow Heatmap** — Aggregate DVL across sectors for macro "where" answers (low effort, high user value)
4. **Anchor Health Score** — Campaign lifecycle awareness (low effort, improves reliability)
5. **Delivery % filter** — Improve existing signal quality (trivial effort, incremental improvement)
