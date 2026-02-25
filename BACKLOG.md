# LFM Product Backlog

> Sourced from CFA / Quant Developer functional review — Feb 2026  
> Priority: 🔴 High | 🟡 Medium | 🟢 Low  
> Size: `S` = hours | `M` = 1-2 days | `L` = 3-5 days | `XL` = week+

---

## 🔴 High Priority — Core Analytical Gaps

### 1. Divergence Engine (DVL vs Price)
**Size: M** — Highest analytical value addition

Automate detection of DVL-to-Price divergences instead of relying on the user's eye:

| Pattern | Definition | Significance |
|---------|-----------|--------------|
| **Bullish Divergence** | Price makes lower low, DVL makes higher low | Hidden accumulation during selloff |
| **Bearish Divergence** | Price makes higher high, DVL makes lower high | Distribution behind rising prices |
| **Confirmation** | Both making new highs | Healthy trend |

- [ ] Implement swing-high/low detection on both price and DVL series
- [ ] Classify divergence type (bullish/bearish/confirmation)
- [ ] Surface as chart markers and in API response metadata
- [ ] Add to screener (e.g., `SCR: Divergence-Bull`, `SCR: Divergence-Bear`)

---

### 2. Distribution-Side Signal Markers
**Size: M** — Completes the signal framework

The current marker set (Coil, Ignition, Spring, Grind) is exclusively bullish. Add bearish equivalents:

- [ ] **Distribution Marker** — High-volume expansion candle closing in lower half of range near DAVWAP (inverse of Ignition)
- [ ] **Bearish Absorption (BD)** — Volume spiking on down candles with DVL declining
- [ ] **Exhaustion Marker** — DVL flattening at extreme highs while price grinds higher
- [ ] Add to screener watchlists (`SCR: Distribution`, `SCR: Exhaustion`)
- [ ] Add legend entries, help modals, and chart marker rendering in frontend

> **Note**: D and BD signals were previously explored in conversations `f182024a` and `638c6dfa` via a `smart_money.py` module that has since been removed. Review those conversations for prior logic.

---

### 3. Sector Flow Heatmap
**Size: M** — Answers "where is smart money flowing" at scale

- [ ] Create API endpoint that aggregates DVL slopes across sector watchlist constituents
- [ ] Build heatmap UI component showing sector-level flow direction and intensity
- [ ] Track week-over-week changes for rotation detection
- [ ] Data infrastructure already exists (NIFTY sector watchlists, `nse_indices_agent`)

---

## 🟡 Medium Priority — Reliability & Quality

### 4. Anchor Health Score & Lifecycle
**Size: S**

The anchor is a single point of failure. Add lifecycle awareness:

- [ ] **Anchor Age Warning** — Alert when anchor is >18 months old
- [ ] **Campaign Health** — Track if DVL has returned to zero and gone negative for >60 days since anchor
- [ ] States: `Born → Active → Aging → Expired`
- [ ] Surface health status in API metadata and dashboard (next to anchor date)

---

### 5. Delivery Percentage Filter on Signals
**Size: S** — Trivial effort, improves signal quality

`delivery_pct` exists in DB but is unused in signal logic:

- [ ] Add `delivery_pct` as a confirming filter on Ignition marker (require >40% on signal day)
- [ ] Add `delivery_pct` as a confirming filter on Grind G3 (require avg >35% across 3 days)
- [ ] Consider surfacing delivery % in the volume bar tooltip
- [ ] Flag F&O stocks during expiry weeks (elevated delivery from physical settlement noise)

---

### 6. Signal Backtesting Module
**Size: M** — Builds confidence in signal quality

Lightweight historical scan (not a full backtesting engine):

- [ ] Script that scans historical data for all past Ignition/Coil/Spring/Grind markers
- [ ] For each, measure forward returns at +5, +10, +20, +30 days
- [ ] Output summary statistics: hit rate, median return, max drawdown after signal
- [ ] Segment by score threshold (e.g., score >70 vs score 50-70)
- [ ] Output as a markdown report or CSV

---

### 7. F&O Expiry Week Flag
**Size: S**

- [ ] Add `is_expiry_week` boolean to data pipeline (last Thursday of month for monthly, weekly expiry for index)
- [ ] Surface in API response so frontend can display a subtle indicator
- [ ] Consider dampening signal scores during expiry weeks for F&O stocks

---

### 8. Earnings Announcements Event Flag
**Size: M** — Provides critical context for price/volume spikes

Overlay earnings announcement dates on the chart so the user can immediately contextualize sudden volume surges, gap-ups/downs, and signal markers (e.g., an Ignition on results day vs. a regular day carries very different meaning).

**Data Sourcing (Investigation Required)**:
- [ ] Investigate NSE corporate filings API (`https://www.nseindia.com/api/corporate-board-meetings?index=equities`) for board meeting dates where "purpose" contains "Financial Results"
- [ ] Investigate NSE corporate announcements endpoint for result declaration dates
- [ ] Evaluate `nse-tools` or `jugaad-data` Python packages as potential wrappers
- [ ] Fallback: BSE announcements API or screener.in earnings calendar scrape

**Implementation**:
- [ ] Create `scripts/sync_earnings.py` agent to fetch and persist earnings dates per symbol
- [ ] Add `earnings_calendar` table to `database.py` (symbol, announcement_date, quarter, result_type)
- [ ] Surface earnings dates in `/lfm/api/analysis/stock/{symbol}` response
- [ ] Render as vertical event lines or `E` markers on the price chart
- [ ] Integrate into `prod_run.sh` pipeline (run periodically, not daily — earnings are announced weeks ahead)
- [ ] Consider adding a pre-earnings window flag (e.g., 5 days before results) to contextualize Coil markers as potential "pre-results compression"

---

## 🟢 Low Priority — Polish & Extensions

### 9. Relative Strength (DVL-Based)
**Size: S**

- [ ] Calculate aggregate DVL slope for NIFTY 500 (or broad index)
- [ ] For each stock, compute `RS = Stock DVL Slope / Index DVL Slope`
- [ ] Surface as a metric in the header bar (e.g., "RS: 1.8x")
- [ ] Add to screener as a filter (e.g., show only stocks with RS > 1.5)

---

### 10. ~~README Refresh~~ ✅
**Size: S** — *Completed Feb 2026*

- [x] Remove references to decommissioned CME/Treasury/FRED architecture
- [x] Document current NSE-focused system architecture
- [x] Update scripts reference section
- [ ] Add screenshot of dashboard

---

### 11. Screener Robustness
**Size: S**

- [ ] Handle holiday/long-weekend edge cases for Grind G1→G2→G3 chain
- [ ] Add timing info to screener output (total scan time, per-stock avg)
- [ ] Consider adding a `--dry-run` mode that prints results without writing to DB

---

### 12. MCS Interpretation Guard
**Size: S**

- [ ] Add contextual tooltip or help note about MCS limitations:
  - Negative MCS during corrections ≠ always "overhead supply"
  - Positive MCS during squeezes ≠ always "strong participation"
- [ ] Consider adding a "MCS Quality" indicator (e.g., suppress MCS signal when ATR is abnormally low)

---

### 13. Volume Profile Enhancements
**Size: M**

- [ ] Render volume profile as horizontal bars on the price chart (not just POC line)
- [ ] Add Value Area High (VAH) and Value Area Low (VAL) — the 70% volume concentration zone
- [ ] These levels provide natural support/resistance context

---

### 14. Multi-Timeframe Confirmation
**Size: L**

- [ ] When viewing daily, show weekly DVL trend direction as a background indicator
- [ ] When an Ignition fires on daily, flag whether weekly DVL is also rising (higher confidence) or falling (lower confidence)
- [ ] Consider a composite "Conviction Score" that weighs daily signal + weekly trend alignment

---

### 15. Export & Sharing
**Size: S**

- [ ] Add "Screenshot Chart" button (canvas-to-image export)
- [ ] Add "Export Data" button (download current symbol's OHLCV + DVL + signals as CSV)

---

### 16. Performance — Screener Parallelization
**Size: M**

- [ ] The screener currently processes symbols sequentially
- [ ] For NIFTY 500 (500 symbols), consider `concurrent.futures.ThreadPoolExecutor` for cache-warm scans
- [ ] Be mindful of SQLite write contention — batch commits after parallel reads

---

## Reference

See [PRODUCT_REVIEW.md](./PRODUCT_REVIEW.md) for the full functional review with detailed rationale behind each item.
