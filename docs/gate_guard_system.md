# Gate & Guard: Two-Stage Hybrid Equity Screening System
## Hybrid Quantitative-Qualitative Framework for Institutional Delivery Volume Anomaly Detection

The **Gate & Guard** system is a state-of-the-art hybrid equity screening system integrated natively into the **Liquidity Flow Monitor (LFM)**. It combines high-throughput, mathematically rigorous quantitative screening (**The Gate**) with a web-grounded, adversarial qualitative LLM risk audit (**The Guard**) running on Google Gemini 3.5 Flash.

This document serves as the core technical reference for the system’s mathematical formulas, setup classifications, orchestrator architecture, data models, and dashboard interface.

---

## 1. The Core Philosophy & Pitfall Mitigation

Standard volume-based screening systems are highly susceptible to market noise. The LFM platform explicitly rejects the retail fallacy that *"every volume spike is accumulation."* The quantitative stage (**The Gate**) is designed specifically to mitigate five core delivery volume pitfalls:

1. **The Block/Bulk Deal Distortion**: Instantly filters out massive, pre-negotiated promoter or institutional liquidity-neutral crossings using a dynamic **Free-Float Truncation Filter** to prevent rolling standard deviation pollution.
2. **Passive Index Rebalancing & ETF Flows**: Programmatically scales up anomaly thresholds by $2.5\times$ on scheduled quarterly/monthly index rebalancing windows to neutralize non-directional mechanical flow.
3. **Institutional Distribution vs. Accumulation**: Enforces strict intraday price location parameters via the **Money Flow Multiplier ($MFM$)** to ensure volume is backed by buying pressure.
4. **Cash-Futures Expiry Arbitrage**: Automatically increases threshold bounds during F&O expiry weeks to filter out operational physical delivery settlements.
5. **Corporate Action Adjustment**: Integrates with NSE corporate actions to ensure OHLCV and delivery series are split/bonus adjusted before rolling calculations.

---

## 2. Stage 1: Quantitative Anomaly Detection (The Gate)

The Gate operates inside `DivergenceEngine.run()` and `scripts/daily_screener.py` utilizing vectorized Pandas/NumPy operations. 

```
Raw EOD OHLCV + Delivery Data
            │
            ▼
    [ Outlier Truncation ]  ──> Drop if Delivery > 1.5% of Free Float
            │
            ▼
  [ Nested Rolling Z-Scores ] ──> Compute Z_5, Z_20, Z_60, Z_252
            │
            ▼
   [ Price-Action Filters ] ──> MFM_t >= 0.3 AND RP_252 Range AND Gap_t >= -1.5%
            │
            ▼
   [ Structural Trend Gate ] ──> C_t >= CWVAP_t
            │
            ▼
  [ Multi-Factor Score ]    ──> Calculate S_total (Vol, Price, Trend)
            │
            ▼
 [ Setup Mapping & Triage ] ──> Map to SIAB, CDMA, CLFR, or ISP (S_total >= 80)
            │
            ▼
   Passed to The Guard
```

### A. Mathematical Formulations

Let:
* $D_t$ = Delivery Quantity on day $t$.
* $V_t$ = Total Traded Volume on day $t$.
* $DP_t = \frac{D_t}{V_t}$ = Delivery Percentage on day $t$.
* $FF_t$ = Total Free-Float shares of the equity outstanding on day $t$.

#### Outlier Truncation (The Block Deal Filter)
Before calculating any rolling averages or standard deviations, the delivery series is cleaned to prevent statistical outlier pollution:

$$\tilde{D}_t = \begin{cases} 
      \min\left(D_t, \, \theta_{\max} \cdot FF_t\right) & \text{if } D_t > 0 \\
      D_t & \text{otherwise}
   \end{cases}$$

*Where $\theta_{\max} = 0.015$ (1.5% of free float).*

#### Nested Rolling Z-Scores
The system calculates delivery anomalies over four nested horizons $N \in \{5, 20, 60, 252\}$:

$$\mu_{t, N} = \frac{1}{N} \sum_{i=0}^{N-1} \tilde{D}_{t-i}$$

$$\sigma_{t, N} = \sqrt{\frac{1}{N-1} \sum_{i=0}^{N-1} \left(\tilde{D}_{t-i} - \mu_{t, N}\right)^2}$$

The Delivery Anomaly Z-Score for horizon $N$ is defined as:

$$Z_{t, N} = \frac{\tilde{D}_t - \mu_{t, N}}{\sigma_{t, N}}$$

#### Money Flow Multiplier (MFM)
To guarantee buying pressure and filter out institutional distribution on high volume, the price candle must close in the upper 65% of its true range:

$$MFM_t = \frac{(C_t - L^{true}_t) - (H^{true}_t - C_t)}{H^{true}_t - L^{true}_t} \ge 0.30$$

*Where $H^{true}_t = \max(H_t, C_{t-1})$ and $L^{true}_t = \min(L_t, C_{t-1})$.*

#### Range Position (RP) & Gap Filters
* **Range Position**: $RP_{t, 252} = \frac{C_t - L_{t, 252}}{H_{t, 252} - L_{t, 252}}$ must satisfy $0.15 \le RP_{t, 252} \le 0.85$ (except for breakouts within 3% of the 52-week high, where $RP \ge 0.85$ is allowed).
* **Gap-Down Filter**: $\text{Gap}_t = \frac{O_t - C_{t-1}}{C_{t-1}} \ge -0.015$ (discards any stock opening with a gap-down exceeding -1.5%).

### B. Multi-Factor Probabilistic Scoring Matrix

The final triage score $S_{total} \in [0, 100]$ represents the composite probability of genuine institutional accumulation:

$$S_{total} = w_V \cdot S_V + w_P \cdot S_P + w_T \cdot S_T$$

* **Weights**: $w_V = 0.40$ (Volume Anomaly), $w_P = 0.35$ (Price Candle Quality), $w_T = 0.25$ (Trend Coherence).
* **Volume Sub-Score ($S_V$)**:
  $$S_V = 0.15 \cdot \Phi(Z_{5}) + 0.35 \cdot \Phi(Z_{20}) + 0.30 \cdot \Phi(Z_{60}) + 0.20 \cdot \Phi(Z_{252})$$
  *Where $\Phi(Z) = \text{clamp}\left(\frac{Z}{3.0} \times 100, \, 0, \, 100\right)$.*
* **Price Sub-Score ($S_P$)**:
  $$S_P = 0.40 \cdot \left(\frac{MFM_t + 1}{2} \times 100\right) + 0.30 \cdot \left(100 \times \left(1 - |RP_{252} - 0.5| \times 2\right)\right) + 0.30 \cdot \text{clamp}\left(\frac{ESR_t}{\text{median}(ESR, 20)} \times 50, \, 0, \, 100\right)$$
* **Trend Sub-Score ($S_T$)**:
  $$S_T = 0.50 \cdot \text{clamp}\left(CWC_t \times 100, \, 0, \, 100\right) + 0.50 \cdot \text{clamp}\left((PSZ_v + 1) \times 50, \, 0, \, 100\right)$$

#### Triage Thresholds:
* **$S_{total} \ge 80$**: Clears the Gate (Dispatched immediately to **The Guard**).
* **$70 \le S_{total} < 80$**: Logged in engine logs for trend tracking.
* **$S_{total} < 70$**: Discarded.

---

## 3. The 4 Structural Trading Setups

Triage candidates clearing the Gate are mapped into one of four highly specialized institutional setups based on engine configurations:

### 1. Silent Institutional Accumulation Base (SIAB)
* **Rationale**: Heavy long-term delivery accumulation in a highly compressed range. Price remains suppressed to avoid drawing retail attention until the breakout.
* **Metrics**:
  * $RP_{252} < 0.35$ (Flat multi-month basing).
  * $\text{base\_tightness} = \frac{\text{range\_width}_{10}}{\text{range\_width}_{63}} < 0.25$ (Extreme range compression).
  * $Z_{252} \ge 1.5$ and $Z_{60} \ge 2.0$ (Steady long-term accumulation).
  * $DP_t \ge 45\%$ (Institutional delivery backing).
  * $1.0 \le RDV_{vol} \le 2.0$ (Controlled volume expansion).
  * $Z_5 \ge 2.5$ and $C_t \ge CWVAP_t$ (Fresh spark breakout).

### 2. Catalyst-Driven Momentum Acceleration (CDMA)
* **Rationale**: High-velocity buying driven by sudden fundamental tailwinds (earnings beat, major order, sector shift). Institutions aggressively chase liquidity in the open market.
* **Metrics**:
  * $RP_{252} \ge 0.80$ (Near 52-week highs).
  * $RDV_{vol} \ge 2.5$ (Massive volume expansion).
  * $DP_t \ge 35\%$ and $Z_5 \ge 3.0$ (Heavy institutional participation).
  * $MFM_t \ge 0.60$ and $ESR_t \ge 1.5 \cdot \text{median}(ESR, 20)$ (Efficient price expansion).
  * $CWC_t \ge 0.60$ and $cts \ge 0.5$ (Confirmed trend).

### 3. Capitulation Liquidity Floor Reversal (CLFR)
* **Rationale**: Reversals at major historical value areas where retail panic is met with massive institutional block absorption.
* **Metrics**:
  * $RP_{252} \le 0.15$ (Deeply oversold capitulation).
  * $C_t$ touches or falls below the Value Area Low ($\text{va\_low}_{120}$).
  * $RDV_{vol} \ge 3.5$ and $Z_5 \ge 4.0$ (Extreme panic volume).
  * $DP_t \ge 40\%$ and $ESR_t < 0.5 \cdot \text{median}(ESR, 20)$ (Massive delivery volume absorbed with tight price spread).
  * $MFM_t \ge 0.20$ with a strong hammer/lower shadow candle structure.

### 4. Institutional Secular Pullback (ISP)
* **Rationale**: Buying pullbacks of secular uptrend leaders at their "fair value" (Composite VWAP) as institutions defend their cost basis.
* **Metrics**:
  * $RP_{252} \ge 0.60$ and $cts > 0$ (Secular uptrend intact).
  * $RDV_{vol} < 1.0$ during the multi-day pullback (dry liquidity, no distribution).
  * On entry: $RDV_{vol}$ expands to $\ge 1.5$ with $DP_t \ge 40\%$ at $C_t \approx CWVAP_t$ ($\pm 1.5\%$).
  * $Z_5 \ge 2.0$ and $MFM_t \ge 0.0$ (Institutional defense).

---

## 4. Stage 2: Asynchronous Qualitative Audit (The Guard)

The Guard is implemented in `scripts/guard_orchestrator.py` as an asynchronous EOD pipeline running after the daily screener. It behaves as an adversarial short-seller, analyzing corporate governance, accounting metrics, and news integrity to veto risky plays.

### A. Core Architecture

```
[ Screener Signals Table ]
           │ (Fetch top 20 candidates sorted by S_total)
           ▼
[ Asynchronous Queue ] ──> Rate Limit: 15s Delay (5 RPM Ceiling)
           │
           ▼
[ Web Search Grounding ]
  ├── 1. Screener.in parse: promoter trends, pledges, receivables, debt
  ├── 2. Exchange announcements: SEBI filings, auditor changes
  └── 3. Mainstream press: Catalyst verification
           │
           ▼
[ Gemini 3.5 Flash ] ──> Adversarial forensic analysis & risk audit
           │
           ├── (Network/Billing Block: HTTP 429 / 400)?
           │   └── Fallback: Standard Search-less API execution
           ▼
[ SQLite Persistence ] ──> Ingest into `gate_guard_signals` table
```

### B. Quotas & Operational Guardrails
To prevent developer billing overruns and API rate exhaustion, the orchestrator implements strict operational bounds:
1. **Daily Quota Cap**: Enforces `ORDER BY s_total DESC LIMIT 20` when querying candidates, running audits *only* on the top 20 highest-conviction signals each day.
2. **Rate Limit Padding**: An asynchronous sleep delay of $15\text{ seconds}$ is enforced sequentially between candidates, capping API execution strictly below the $5\text{ RPM}$ tier.
3. **Dual-Stage Fallback Strategy**: If web-grounding triggers an API exception (such as `429` quota limits or regional query bans), it automatically catches the error and retries the audit using a standard, search-less API call, ensuring zero pipeline failures.

### C. Forensic JSON Schema
The LLM response is parsed and validated against the following schema:
```json
{
  "symbol": "TICKER",
  "verdict": "APPROVE" | "VETO",
  "veto_reasons": ["List of critical red flags"],
  "catalyst_type": "GENUINE_ACCUMULATION" | "BLOCK_DEAL_DISTRIBUTION" | "PASSIVE_INDEX_FLOW" | "RETAIL_CHURN_PUMP" | "DEBT_STRESSED_LIQUIDATION",
  "fundamental_grade": "A" | "B" | "C" | "F",
  "governance_risk": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
  "qualitative_score": 85,
  "key_metrics_checked": {
    "debt_to_equity": 0.12,
    "promoter_pledge_pct": 0.0,
    "ocf_to_net_profit_3yr": 0.95,
    "receivable_days_trend": "IMPROVING" | "STABLE" | "DETERIORATING"
  },
  "evidence_citations": ["https://url1", "https://url2"]
}
```

---

## 5. Production Database Integration

The LFM database schema (`src/database.py`) was updated to include the dynamic screening fields and the `gate_guard_signals` forensic ledger.

### A. Database Migrations
* **`screener_signals` Table Additions**:
  * `gate_setup` TEXT: The setup identifier (SIAB, CDMA, CLFR, ISP).
  * `gate_signal` INTEGER: Binary flag (1 if $S_{total} \ge 80$, else 0).
  * `s_total` REAL: The composite quantitative score.
* **`gate_guard_signals` Table**:
  ```sql
  CREATE TABLE IF NOT EXISTS gate_guard_signals (
      symbol TEXT PRIMARY KEY,
      date TEXT NOT NULL,
      price REAL NOT NULL,
      gate_score REAL NOT NULL,
      setup_tag TEXT NOT NULL,
      verdict TEXT NOT NULL,
      catalyst_type TEXT NOT NULL,
      fundamental_grade TEXT NOT NULL,
      governance_risk TEXT NOT NULL,
      qualitative_score REAL NOT NULL,
      red_flags TEXT,         -- JSON array of red flags
      ratios_json TEXT,       -- JSON of promoter metrics, pledges, debt
      citations TEXT,         -- JSON array of source URLs
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  );
  ```

### B. Dynamic Purification (Anti-Stale Records)
To guarantee the dashboard reflects *only* the results of the most recent EOD scan:
1. Every time `daily_screener.py` begins executing, it runs a database wipe:
   ```sql
   DELETE FROM screener_signals;
   ```
2. When `guard_orchestrator.py` runs, it purges its forensic table to eliminate old signals:
   ```sql
   DELETE FROM gate_guard_signals;
   ```
This prevents the presence of stale data, ensuring the operator works exclusively with fresh EOD trading signals.

---

## 6. The Web UI Dashboard

The UI at `/de/screener` provides a command center displaying the EOD sync output.

### A. Tab-Based Layout
* **Technical Screener Tab**: Displays the vectorized engine indicators, including the entries, exits, in-trade positions, and technical signals using a fully responsive ag-Grid instance.
* **Gate & Guard Forensic Audits Tab**: Displays the qualitative LLM reports, key accounting ratios, forensic verdicts (`APPROVED` in emerald, `VETOED` in crimson), qualitative grades, and citations.

### B. Premium Card Mechanics & Reactive Styles
The summary cards at the top of the Technical Screener are engineered for high-performance aesthetics:
* **Glassmorphic Skin**: Styles leverage `backdrop-filter: blur(12px)` and translucent backgrounds for a state-of-the-art dark interface.
* **Reactive Filtering (`:has()`)**:
  Using the modern CSS parent-child pseudo-class `:has()`, cards react dynamically to checkboxes.
  * When a checkbox is **unchecked**, the card drops to `opacity: 0.45`, grays out its border color (`border-color: #21262d`), and scales down visually (`transform: scale(0.97)`).
  * When **checked**, the card glows using a soft `box-shadow` of its active theme color:
    - **Entries**: `#3fb950` (emerald green shadow)
    - **In-Trade**: `#d29922` (warm gold shadow)
    - **Exits**: `#f85149` (crimson red shadow)
    - **Tech-Only**: `#58a6ff` (ocean blue shadow)
* **Hover Micro-animations**: Moving the cursor over active cards triggers a translation slide (`translateY(-3px) scale(1.02)`) and raises the card’s drop-shadow intensity.
* **Squeezing Prevention**: A combination of `min-height: 92px` on the `#screener-summary` container and `min-height: 90px; flex-shrink: 0;` on `.card` prevents individual boxes from collapsing when switching tabs.

---

## 7. Pipeline Orchestration & Execution

### A. Daily Sync Integration (`scripts/daily_sync.sh`)
The Gate & Guard system is hooked directly as the final steps in the 9-stage sync pipeline:
```bash
#!/bin/bash
set -e

# Stages 1 to 7: Actions sync, cache flushes, price validations, data reconciliations, etc.
# ...

echo "Stage 8: Running Multi-Core Market Screener (The Gate)..."
./venv/bin/python scripts/daily_screener.py

echo "Stage 9: Running Asynchronous LLM Qualitative Forensic Audit (The Guard)..."
./venv/bin/python scripts/guard_orchestrator.py

echo "LFM Daily Sync Pipeline completed successfully."
```

### B. Downstream Signal Execution Model (EOD-Lag)
The execution follows the institutional EOD-Lag standard:
1. **Bar $i$ (EOD)**: The EOD sync pipeline runs, computing the quantitative Gate, launching the qualitative Guard audit, and writing the final signals to SQLite.
2. **Bar $i+1$ (Next Session)**: The trader executes the long order on approved symbols at the **Market Open** price.
3. **Bar $i+2$ (Subsequent Sessions)**: Active trailing stop, CWVAP exits, and CTS trend participation exit checks are initiated.
