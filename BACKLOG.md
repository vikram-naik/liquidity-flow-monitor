# LFM Product Backlog

> Sourced from CFA / Quant Developer functional review — Feb 2026  
> Priority: 🔴 High | 🟡 Medium | 🟢 Low  
> Size: `S` = hours | `M` = 1-2 days | `L` = 3-5 days | `XL` = week+

---

## 🔴 High Priority — Core Analytical Gaps

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

### 10. Ledger Engine — OOAD Refactoring
**Size: M** — Converts standalone functions into composable, testable components

`ledger.py` currently has **9 standalone functions** with no class structure. Functions share implicit contracts (column names, DataFrame shape) but these contracts are not formalized. The anchor logic (`find_day_zero_anchor`) at 146 lines is the largest and mixes detection algorithm, DB persistence, and fallback logic in a single function.

**Current state** (in `ledger.py`):
```
calculate_mfm(df)           # Pure transform
calculate_dvl(df, anchor)   # Depends on MFM output
calculate_cumulative_dvl(df) # Depends on MFM output
calculate_davwap(df, anchor) # Independent
calculate_mcs(df, window)   # Independent
calculate_volume_profile(df) # Independent
find_day_zero_anchor(df)    # Algorithm + DB I/O (mixed concerns)
get_or_create_anchor(sym, df) # DB I/O wrapper
```

**Target state** — Composable engine:

- [ ] Define `LedgerEngine` class that owns the computation pipeline and enforces execution order (MFM → DVL → DAVWAP → MCS)
- [ ] Extract anchor logic into `AnchorService` (separates detection algorithm from persistence)
- [ ] Extract volume profile into `VolumeProfileAnalyzer`
- [ ] Formalize the DataFrame column contract via a `LedgerSchema` (typed column names, expected dtypes)
- [ ] Each component becomes independently testable with mock DataFrames
- [ ] `data.py` instantiates `LedgerEngine` instead of calling 6 standalone functions in sequence

---

### 11. Query Centralization — Repository Pattern
**Size: M** — Eliminates raw SQL dispersion, single source of truth for all DB access

Raw `cursor.execute()` calls are currently **scattered across 7 files** with ~50+ individual SQL statements:

| File | SQL Calls | Domain |
|------|-----------|--------|
| `src/api/main.py` | ~20 | Watchlist CRUD, symbol listing, upload |
| `scripts/run_screener.py` | ~15 | Screener list init, population, universe fetch |
| `src/analysis/ledger.py` | 2 | Anchor read/write |
| `src/agents/nse_agent.py` | ~8 | Bhavcopy upsert, change calc |
| `src/agents/nse_indices_agent.py` | ~4 | Index data upsert |
| `scripts/sync_ca.py` | ~3 | Corporate action upsert |
| `src/database.py` | DDL only | Schema creation |

**Problems**: Duplicate queries (e.g., "get watchlist by name" appears in `main.py` and `run_screener.py`), no query reuse, schema changes require hunting across all files.

**Target state** — Repository layer:

- [ ] Create `src/repositories/watchlist_repo.py` — all watchlist & item CRUD
- [ ] Create `src/repositories/market_data_repo.py` — all price/delivery/CA read/write
- [ ] Create `src/repositories/anchor_repo.py` — anchor persistence (extracted from `ledger.py`)
- [ ] Create `src/repositories/screener_repo.py` — screener list management
- [ ] Each repo takes a `connection` parameter (DI), returns domain objects (dicts/dataclasses), never exposes cursors
- [ ] Refactor `main.py`, `run_screener.py`, `ledger.py`, agents to use repos instead of raw SQL
- [ ] Add `__init__.py` barrel export for clean imports

---

## 🟢 Low Priority — Polish & Extensions

### 12. Relative Strength (DVL-Based)
**Size: S**

- [ ] Calculate aggregate DVL slope for NIFTY 500 (or broad index)
- [ ] For each stock, compute `RS = Stock DVL Slope / Index DVL Slope`
- [ ] Surface as a metric in the header bar (e.g., "RS: 1.8x")
- [ ] Add to screener as a filter (e.g., show only stocks with RS > 1.5)

---

### 14. Screener Robustness
**Size: S**

- [ ] Handle holiday/long-weekend edge cases for Grind G1→G2→G3 chain
- [ ] Add timing info to screener output (total scan time, per-stock avg)
- [ ] Consider adding a `--dry-run` mode that prints results without writing to DB

---

### 15. MCS Interpretation Guard
**Size: S**

- [ ] Add contextual tooltip or help note about MCS limitations:
  - Negative MCS during corrections ≠ always "overhead supply"
  - Positive MCS during squeezes ≠ always "strong participation"
- [ ] Consider adding a "MCS Quality" indicator (e.g., suppress MCS signal when ATR is abnormally low)

---

### 16. Volume Profile Enhancements
**Size: M**

- [ ] Render volume profile as horizontal bars on the price chart (not just POC line)
- [ ] Add Value Area High (VAH) and Value Area Low (VAL) — the 70% volume concentration zone
- [ ] These levels provide natural support/resistance context

---

### 17. Multi-Timeframe Confirmation
**Size: L**

- [ ] When viewing daily, show weekly DVL trend direction as a background indicator
- [ ] When an Ignition fires on daily, flag whether weekly DVL is also rising (higher confidence) or falling (lower confidence)
- [ ] Consider a composite "Conviction Score" that weighs daily signal + weekly trend alignment

---

### 18. Export & Sharing
**Size: S**

- [ ] Add "Screenshot Chart" button (canvas-to-image export)
- [ ] Add "Export Data" button (download current symbol's OHLCV + DVL + signals as CSV)

---

### 19. Performance — Screener Parallelization
**Size: M**

- [ ] The screener currently processes symbols sequentially
- [ ] For NIFTY 500 (500 symbols), consider `concurrent.futures.ThreadPoolExecutor` for cache-warm scans
- [ ] Be mindful of SQLite write contention — batch commits after parallel reads

---

## ✅ Completed

### ~~1. Divergence Engine (DVL vs Price)~~ ✅
**Size: M** — *Completed 2026-02-25*

Implemented swing-point detection using fractal windows. Classifies Bullish Divergence, Bearish Divergence, Confirm Bull, and Confirm Bear markers. Integrated into the Screener, Dashboard UI, and Methodology Guide. Includes "Strict Mode" toggle for structural filtering. Added 6-quadrant classification model (Failed High, Strong Low), configurable engine settings (Fractal Period, Min Swing Spacing, Min DVL %), and HTF structural-only markers on weekly/monthly views.

---

### ~~2. Distribution-Side Signal Markers~~ ✅
**Size: M** — *Completed 2026-02-26*

Implemented full distribution-side marker suite: Distribution, Bearish Absorption (BD), Exhaustion, and Bearish Grind. All markers integrated into the Marker Factory pattern with screener support, chart markers, legend entries, and help modals. Added divergence engine settings UI with persistence (`user_settings` table), configurable via modal with inline help text. Strict mode defaults to OFF. Weekly/monthly views show only structural markers.

---

### ~~9. Marker Factory Pattern — Modular Signal Architecture~~ ✅
**Size: L** — *Completed 2026-02-25*

Created `src/analysis/markers/` package with `MarkerInterface` ABC, `MarkerRegistry`, and 8+ marker classes. Refactored `data.py` (395→200 lines), `run_screener.py` (zero-logic orchestrator), `main.py` (dynamic API), and `dashboard.js/html` (dynamic frontend). 29 unit tests.

---

### ~~13. README Refresh~~ ✅
**Size: S** — *Completed Feb 2026*

- [x] Remove references to decommissioned CME/Treasury/FRED architecture
- [x] Document current NSE-focused system architecture
- [x] Update scripts reference section

---

## Reference

See [PRODUCT_REVIEW.md](./PRODUCT_REVIEW.md) for the full functional review with detailed rationale behind each item.
