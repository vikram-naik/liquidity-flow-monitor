# Handoff — Liquidity Flow Monitor

## Current Status
**CTS+PSZ signal strategy validated** — new CWVAP Trend Score (CTS) indicator built,
backtested across NIFTY 50 with acceleration-based entry and slope-based exit.
Signal markers on UI now sourced from backend (single source of truth). As of 2026-03-16.

## What Changed This Session

### 1. CWVAP Trend Score (CTS) — New Indicator
Computed in `src/divergence_engine/cwvap.py` at the end of `_compute_cwvap()`.

**CTS** = average of ATR-normalized spreads between adjacent CWVAP EMA pairs:
- EMAs: 5, 8, 14, 21 of CWVAP
- For each pair (5/8, 8/14, 14/21): `clip((short - long) / ATR, -1, 1)`
- Average of the 3 clipped values → range [-1, +1]
- **+1** = strongly rising CWVAP (bullish), **0** = sideways, **-1** = strongly falling

**CTS derivatives** (also in `cwvap.py`):
- `cts_slope` — 5-bar linear regression of CTS (1st derivative, trend direction)
- `cts_accel` — 5-bar linear regression of cts_slope (2nd derivative, curve inflection)

**Columns in final ledger**: `cts`, `cts_slope`, `cts_accel` (EMA intermediates are dropped)

### 2. Signal Markers — Single Source of Truth
Previously, entry signal markers were computed in the JS frontend (raw PSZ crossing).
Now computed in the backend via `src/divergence_engine/chart.py`:
- `_tag_entry_signals()` runs `check_entry()` from `src/trading/signals.py`
- Adds `entry_signal` boolean column to the ledger JSON
- JS simply reads `r.entry_signal` to place markers
- Config: `_ENTRY_CFG = EntryConfig(min_soft_filters=0)` in `chart.py`

### 3. Signal Entry Logic — Layered PSZ Thresholds
`src/trading/signals.py` — `check_entry()` now uses OR across 5 PSZ thresholds:
- `-0.27, -0.25, -0.20, -0.15, -0.10` (configurable via `EntryConfig`)
- Layered because some stocks never dip to -0.25; accepts first upward crossing of any tier
- Quality gates (PDD_120, RSZ) currently **disabled** in both chart.py and scanner.py
- Scanner config: `EntryConfig(min_soft_filters=0)`

### 4. Engine State Panel — Updated Metrics
Removed: ATR(20), CWVAP Dist, Delivery %, Coherence, CWC (from initial load, some re-added)
Current engine state panel fields:
- Date, Regime, Close, CWVAP, RDV, PSZ, RSZ, PDD 120, CTS, MCS, CWC, G Shape

### 5. CTS Sub-Panel on Chart
- Panel key: `"cts"` in `PANEL_DEFINITIONS` → label "CWVAP Trend"
- Line color: `#4fc3f7`, with zero-line reference
- Add via panel selector on the UI

### 6. New Columns in State Summary (`engine.py` → `latest` property)
Added: `pdd_120`, `mcs_composite`, `cts`, `cts_slope`, `cts_accel`, `gradient_shape`

### 7. New Columns in `UI_COLUMNS` (`chart.py`)
Added: `pdd_120`, `mcs_composite`, `cts`, `cts_slope`, `cts_accel`, `entry_signal`
Removed: `cwvap_ema` (replaced by CTS)

### 8. Cache Busting
`divergence_engine.html` JS/CSS references bumped to `?v=6`. Update version on future changes.

---

## CTS+PSZ Signal Strategy — Backtest Results (NIFTY 50)

### Strategy Design
**Entry**: PSZ crosses threshold upward AND `cts_accel >= 0` (curve bending upward)
**Exit**: `cts_slope < exit_threshold` (trend turning downward) OR hard stop (2 ATR)

### Backtest: Acceleration Entry, Slope Exit (PSZ=-0.25)

| Exit Slope Threshold | Trades | Win Rate | Avg P&L | Payoff | Avg Duration |
|---------------------|--------|----------|---------|--------|-------------|
| 0.000 | 1917 | 16.5% | +0.29% | 7.21x | 4.0 bars |
| -0.005 | 1894 | 25.2% | +0.71% | 4.87x | 7.9 bars |
| -0.010 | 1840 | 32.4% | +1.14% | 3.57x | 12.7 bars |
| **-0.015 (recommended)** | **1746** | **37.9%** | **+1.63%** | **3.00x** | **17.9 bars** |
| -0.020 | 1616 | 42.0% | +2.11% | 2.62x | 23.3 bars |
| -0.030 | 1432 | 43.8% | +2.65% | 2.46x | 30.1 bars |

**Recommended config** (PSZ=-0.25, exit slope=-0.015):
- 1,746 trades, 37.9% WR, **+1.63% avg P&L**, **3.00x payoff**
- Avg winner +9.52%, avg loser -3.17%
- Exit breakdown: cts_slope_neg 1336 (+3.30%), hard_stop 360 (-6.23%), time_decay 44 (+15.66%)
- MFE +6.23%, MAE -2.64%, MFE left on table +4.60%

### Comparison to Previous Strategies

| Strategy | Trades | WR | Avg P&L | Payoff |
|----------|--------|----|---------|--------|
| Old signals (7 exit rules) | 3260 | 40.6% | +1.45% | 2.64x |
| CTS delta-based (v1) | 724 | 43.5% | +0.87% | 1.88x |
| **CTS accel/slope (v2)** | **1746** | **37.9%** | **+1.63%** | **3.00x** |

### Per-PSZ Threshold (exit slope = -0.015)

| PSZ | Trades | WR | Avg P&L | Payoff | Duration |
|-----|--------|----|---------|--------|----------|
| -0.30 | 1184 | 33.9% | +1.51% | 3.60x | 16.0 |
| -0.25 | 1746 | 37.9% | +1.63% | 3.00x | 17.9 |
| -0.20 | 2027 | 40.1% | +1.71% | 2.71x | 18.7 |

---

## What's Live (Full System)

### Core Engine Pipeline (59 columns, 7 modules)
1. `base_calc.py` — ATR20, RDV, MFM, TP, MFM_TP
2. `regime.py` — ADX/DMI regime (uptrend/downtrend/notrend/transition)
3. `dvl_ledger.py` — DVL, DVL_rate, Velocity, Price_distance, ARS, PDD, CDVL, gradient_shape per window
4. `cwvap.py` — DVWAP, POC, CWVAP, va_high/va_low, **CTS, cts_slope, cts_accel**
5. `cwc.py` — Cross-Window Coherence, CWC_delta, CWC_slope
6. `mcs.py` — MCS, MCS_MFM, MCS_composite, MCS_composite_slope
7. `analysis.py` — price_slope_z, rdv_slope_z, coherence_raw, coherence, accum_div, distrib_div

### Signal System (`src/trading/signals.py`)
- **Entry**: Layered PSZ crossings (OR of 5 thresholds) + regime block + soft filters
- **Exit**: 7 rules (hard stop, trail, delivery, coherence, MCS, PSZ reversal, PSZ momentum fail)
- Quality gates (PDD_120, RSZ) available but currently **disabled**
- Scanner (`src/trading/scanner.py`) uses `EntryConfig(min_soft_filters=0)`

### Trading Module (`src/trading/`)
- `signals.py` — Entry/exit logic (shared by backtest + scanner + chart markers)
- `scanner.py` — 4-phase daily scanner
- `repository.py` — DB CRUD for 4 trading tables
- `broker.py` — PaperBroker (live) + KiteBroker (stub)
- `sizing.py` — Equal-weight with 2% ATR risk cap
- Position lifecycle: proposed → pending_entry → open → closed

### Trading API (`src/api/trading_routes.py`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/de/trades` | Dashboard UI |
| GET | `/de/api/trading/positions?status=` | List positions |
| GET | `/de/api/trading/positions/{id}` | Single position |
| GET | `/de/api/trading/signals?days=30` | Signal history |
| GET | `/de/api/trading/trades?limit=100` | Closed trades |
| GET | `/de/api/trading/pnl?days=90` | Daily P&L series |
| GET | `/de/api/trading/summary` | Dashboard summary |
| GET | `/de/api/trading/funds` | Capital breakdown |
| GET | `/de/api/trading/config` | Scanner config |
| PUT | `/de/api/trading/config` | Update config |
| POST | `/de/api/trading/positions/{id}/approve` | Approve proposed signal |
| POST | `/de/api/trading/positions/{id}/reject` | Reject proposed signal |
| POST | `/de/api/trading/positions/approve-all` | Approve all proposed |
| POST | `/de/api/trading/scan` | Trigger manual scan |

### Divergence Engine UI (`/de/dashboard/{symbol}`)
- OHLC chart with CWVAP overlay and VA trendlines
- **Entry signal markers**: green up-arrows sourced from backend `entry_signal` column (single source of truth)
- Configurable sub-panels: slopes, coherence, RDV, CWC, RDV consistency, ATR, CWVAP dist, delivery %, PDD, **CWVAP Trend (CTS)**
- Overlay toggles: CWVAP, VA, Vol, Entry
- Engine state sidebar: Date, Regime, Close, CWVAP, RDV, PSZ, RSZ, PDD 120, CTS, MCS, CWC, G Shape
- Watchlist management + symbol search

### Divergence Engine API
- `/de/api/divergence-engine/{symbol}` — engine data (ledger + state summary)
- `/de/dashboard/{symbol}` — chart UI
- `/de/api/watchlists/*` — watchlist CRUD + index import
- `/de/api/analysis/stocks` — symbol listing
- `/de/help` — help guide

### Data Ingestion
- `src/agents/nse_agent.py` — NSE Bhavcopy download + smart_sync
- `src/agents/nse_indices_agent.py` — NSE indices sync
- `scripts/sync_ca.py` — yfinance corporate action sync
- `scripts/sync_nse_ca.py` — NSE API corporate action sync

### Scripts
- `scripts/backtest_long_signals.py` — Long-only backtester (old signal system)
- `scripts/backtest_cts_psz.py` — **CTS+PSZ backtester** (accel/slope entry/exit, param sweep)
- `scripts/walk_forward.py` — Train/test split validation
- `scripts/mfe_psz_crossings.py` — MFE/MAE analysis for PSZ threshold crossings

### Infrastructure
- Docker: `Dockerfile` + `docker-compose.yml`
- Build/deploy: `scripts/build_lfm.sh`, `scripts/deploy_lfm.sh`, `scripts/stop_lfm.sh`
- Cache: Redis-based (`src/cache/`) — **clear with `de:*` pattern after engine changes**

## Daily Paper Trading Workflow
1. **~3:45 PM** — `venv/bin/python3 src/agents/nse_agent.py --sync` + `nse_indices_agent.py --sync`
2. **~4:00 PM** — `venv/bin/python3 -m src.trading.scanner` (or "Run Scan" on UI)
3. **Review** — Open `/de/trades`, approve/skip proposed signals
4. **Next day** — Scanner executes approved entries, manages exits, proposes new signals

## Key File Paths
- Engine orchestrator: `src/divergence_engine/engine.py`
- CWVAP + CTS: `src/divergence_engine/cwvap.py`
- Chart data: `src/divergence_engine/chart.py` (signal tagging + UI column trimming)
- Signal logic: `src/trading/signals.py`
- Scanner: `src/trading/scanner.py`
- Trading repo: `src/trading/repository.py`
- Trading API: `src/api/trading_routes.py`
- Main API: `src/api/main.py`
- DB helper: `src/database.py`
- Chart UI: `src/web/divergence_engine.html` + `css/divergence_engine.css` + `js/divergence_engine.js`
- Dashboard UI: `src/web/trades.html` + `css/trades.css` + `js/trades.js`
- CTS backtest: `scripts/backtest_cts_psz.py`

## Next Steps
1. **Wire CTS into signal system**: Replace or augment `check_entry`/`check_exit` in `signals.py` with CTS accel entry + slope exit — the backtest script (`backtest_cts_psz.py`) has the validated logic
2. **Walk-forward validate CTS strategy**: Run train/test split (2019-2023 / 2024-2026) on the accel/slope approach
3. **CTS on chart markers**: Update `_ENTRY_CFG` in `chart.py` to use CTS accel gate once wired
4. **Exit slope threshold as config**: Make `-0.015` configurable in scanner/trading_config
5. **MFE recovery**: Time-decay exits averaged +15.66% — investigate raising max_bars or adaptive trailing for runners
6. **Earnings avoidance**: Skip entries near earnings announcements
7. **KiteBroker**: Zerodha Kite API integration for live execution
8. **Cron automation**: Set up cron jobs for NSE sync + scanner
