# 🌊 Liquidity Flow Monitor (LFM)

An institutional-grade analytics system that tracks **smart money flow** across NSE equities by isolating **Delivery Volume** and **Directional Conviction** to reveal accumulation, distribution, and trend intensity in real time.

> **Philosophy**: LFM marks *what is happening* and *at what intensity* — it does not predict.

---

## 🏗 System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Data Ingestion Layer                                            │
│  ┌─────────────┐  ┌──────────────────┐  ┌──────────────────┐    │
│  │ nse_agent.py │  │ nse_indices_agent│  │   sync_ca.py     │    │
│  │ (Bhavcopy)   │  │  (Index OHLCV)   │  │ (Corp Actions)   │    │
│  └──────┬───────┘  └────────┬─────────┘  └────────┬─────────┘    │
│         └──────────────┬────┘                      │             │
│                        ▼                           ▼             │
│              ┌──────────────────┐  ┌──────────────────────┐      │
│              │   SQLite (WAL)   │  │    Redis Cache        │      │
│              │ liquidity_monitor│  │  (adjusted dataframes)│      │
│              └────────┬─────────┘  └──────────┬───────────┘      │
│                       ▼                       ▼                  │
│              ┌──────────────────────────────────────┐            │
│              │     Analytics Engine                  │            │
│              │  data.py → ledger.py                  │            │
│              │  MFM · DVL · DAVWAP · MCS · Signals   │            │
│              └────────────────┬─────────────────────┘            │
│                               ▼                                  │
│              ┌──────────────────────────────────────┐            │
│              │     FastAPI + Lightweight Charts       │            │
│              │     Dashboard (Single-Page App)        │            │
│              └──────────────────────────────────────┘            │
└──────────────────────────────────────────────────────────────────┘
```

### 1. Data Ingestion (`src/agents/`)
| Agent | Source | Data |
|-------|--------|------|
| `nse_agent.py` | NSE Bhavcopy Archives | Daily OHLCV + Delivery Qty for all equities |
| `nse_indices_agent.py` | NSE Indices | OHLC + Volume for index symbols (NIFTY 50, etc.) |
| `sync_ca.py` | yfinance | Corporate actions (stock splits) for price adjustment |

All agents support **smart sync** (incremental from last DB date) and **backfill** (historical fill from a start date).

### 2. Analytics Engine (`src/analysis/`)

The engine computes all metrics on **daily granularity** first, then aggregates to weekly/monthly for display:

| Metric | Module | Purpose |
|--------|--------|---------|
| **True MFM** | `ledger.py` | Gap-adjusted Money Flow Multiplier — measures where price closed relative to True Range (anchored to Prev Close) |
| **DVL** | `ledger.py` | Delivery Volume Ledger — cumulative `MFM × Delivery Qty` from Day Zero anchor |
| **DAVWAP** | `ledger.py` | Delivery Anchored VWAP — institutional cost basis since Day Zero |
| **MCS** | `ledger.py` | Money Capacity Score — 30-day Pearson correlation between Typical Price and Relative Delivery Volume |
| **Day Zero Anchor** | `ledger.py` | Volume-Confirmed Volatility Pivot algorithm (2-year lookback, ATR + delivery confirmation) |
| **Volume Profile** | `ledger.py` | Delivery-weighted price distribution with Point of Control (POC) |
| **Trend Intensity** | `data.py` | Ledger Angle (0–90°) and Velocity Status (Accelerating / Steady / Weakening / Reversing) |

#### Signal Markers (computed in `data.py`)

| Marker | Type | Triggers |
|--------|------|----------|
| **Coil** 🔵 | Compression | Tight range + dry volume + near DAVWAP + positive ledger (scored 0–100, threshold ≥50) |
| **Ignition** 🟣 | Breakout | Expansion ≥0.8 ATR + high delivery + origin near DAVWAP + accelerating ledger (scored 0–100, threshold ≥50) |
| **Grind** 🟡 | Slow Markup | 3-day progressive buildup (G1→G2→G3) with rising closes, cumulative expansion, and MCS confirmation |
| **Spring** 🩵 | Deep Reversal | Origin ≥1.5 ATR below DAVWAP + 0.8 ATR reversal + positive MFM + improving ledger |

### 3. API Layer (`src/api/main.py`)

FastAPI service exposing:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/lfm/dashboard` | GET | Serve the SPA dashboard |
| `/lfm/api/analysis/stock/{symbol}` | GET | Full analysis: candles, volumes, DVL, DAVWAP, MCS, signals, volume profile |
| `/lfm/api/analysis/stocks` | GET | List all available symbols |
| `/lfm/api/analysis/stock/{symbol}/anchor/manual` | POST | Set manual Day Zero anchor |
| `/lfm/api/analysis/stock/{symbol}/anchor/auto` | POST | Force auto-anchor recalculation |
| `/lfm/api/upload/nse-delivery` | POST | Bulk upload NSE delivery data |
| `/lfm/api/watchlists/*` | CRUD | Watchlist management (create, rename, delete, import index constituents) |
| `/lfm/api/health` | GET | Health check |

### 4. Dashboard (`src/web/`)

Single-page app built with **Lightweight Charts v4** featuring:
- **3-pane synchronized charts**: Price & Delivery Volume, Momentum Ledger (DVL + Ghost Line), MCS Histogram
- **Signal markers** overlaid on price chart (Coil, Ignition, Grind, Spring)
- **DAVWAP line** and **POC price line** on the price pane
- **Trend Intensity panel**: Ledger Tilt (angle + velocity), MCS Tilt, Power Matrix (HH/HL/LH/LL)
- **Watchlist manager**: Multiple lists, NSE index import (NIFTY 50/100/200/500, sector indices), sort, download
- **Manual/Auto anchor** controls (Daily view only)
- **Built-in methodology** documentation page
- **Symbol autocomplete** with full universe search

### 5. Screener (`scripts/run_screener.py`)

Nightly batch scanner across NIFTY 500 + CORE watchlists:
- Detects latest-bar signals: Ignition, Coil, Spring, Grind
- Detects DAVWAP crossovers: CO-U (Crossover Up), CO-D (Crossover Down)
- Populates dedicated `SCR:` prefixed watchlists for next-day review

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- Redis (optional, for caching — falls back to in-memory)
- Docker & Docker Compose (for production)

### Local Development

```bash
# 1. Create virtual environment
python3 -m venv venv && source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Initialize database
python3 -c "from src.database import init_db; init_db()"

# 4. Backfill NSE data (e.g., last 30 days)
python3 src/agents/nse_agent.py --backfill 30

# 5. Start the dashboard
uvicorn src.api.main:app --host 0.0.0.0 --port 8000

# Open http://localhost:8000/lfm/dashboard
```

### Docker (Production)

```bash
# Build and push
docker compose build
docker compose up -d

# Daily sync (run via cron)
docker exec scripts-lfm-api-1 python3 src/agents/nse_agent.py --sync
```

---

## 🛠 Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/prod_run.sh` | **Daily production pipeline**: Sync CA → NSE Data → NSE Indices → Run Screener |
| `scripts/run_screener.py` | Scan NIFTY 500 for active signals, populate screener watchlists |
| `scripts/sync_ca.py` | Fetch stock split data from yfinance and persist to `corporate_actions` table |
| `scripts/flush_lfm_cache.py` | Clear all Redis cache entries |
| `scripts/reset_anchors.py` | Reset all persisted anchors (forces recalculation on next load) |

### Cron Setup (Weekday EOD)

```cron
# Run at 7:30 PM IST (14:00 UTC) on weekdays
0 14 * * 1-5 /path/to/scripts/prod_run.sh >> /var/log/lfm-cron.log 2>&1
```

---

## 📁 Project Structure

```
├── src/
│   ├── analysis/
│   │   ├── data.py          # Main analysis engine (signals, intensity, aggregation)
│   │   └── ledger.py        # Core metrics (MFM, DVL, DAVWAP, MCS, Anchor, Volume Profile)
│   ├── api/
│   │   └── main.py          # FastAPI application
│   ├── agents/
│   │   ├── nse_agent.py     # NSE Bhavcopy fetcher
│   │   └── nse_indices_agent.py  # NSE Index data fetcher
│   ├── cache/
│   │   ├── interface.py     # Cache interface
│   │   ├── redis_provider.py # Redis implementation
│   │   └── factory.py       # Cache provider factory
│   ├── web/
│   │   ├── dashboard.html   # SPA dashboard
│   │   ├── js/dashboard.js  # Chart rendering, watchlists, signals
│   │   └── css/dashboard.css
│   └── database.py          # SQLite schema & migrations
├── scripts/                  # Automation & debugging scripts
├── tests/                    # Verification scripts
├── docker-compose.yml
├── Dockerfile
├── BACKLOG.md               # Product backlog & roadmap
├── PRODUCT_REVIEW.md        # CFA functional review
└── requirements.txt
```

---

## 📊 Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, FastAPI, Pandas, NumPy |
| Database | SQLite (WAL mode) |
| Cache | Redis (optional) |
| Frontend | Lightweight Charts v4, Vanilla JS, Inter font |
| Data Source | NSE Archives (Bhavcopy), yfinance (splits) |
| Deployment | Docker, Docker Compose |
