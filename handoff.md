# Handoff — Liquidity Flow Monitor

## Current Status
**Signal system rebuild in progress** — core pipeline clean, CPOC removed, long-only
signal backtester built with smart exit system. As of 2026-03-15.

## What's Live

### Core Engine Pipeline (56 columns, 6 modules)
The engine computes delivery-weighted indicators across rolling windows [10, 30, 60, 120]:

1. `base_calc.py` — ATR20 (Wilder), RDV, MFM, TP, MFM_TP
2. `regime.py` — ADX/DMI market regime classification (uptrend/downtrend/notrend/transition)
3. `dvl_ledger.py` — DVL, DVL_rate, Velocity (norm), Price_distance, ARS, PDD, CDVL, gradient_shape per window
4. `cwvap.py` — DVWAP, POC, CWVAP, va_high/va_low (delivery-profile VA)
5. `cwc.py` — Cross-Window Coherence, CWC_delta, CWC_slope
6. `mcs.py` — MCS, MCS_MFM, MCS_composite, MCS_composite_slope
7. `analysis.py` — price_slope_z (TP-based), rdv_slope_z, coherence_raw, coherence, accum_div, distrib_div

### Signal System (Long-Only, WIP)

**Entry:** PSZ crosses -0.25 upward (bearish momentum fading toward zero)
- EOD system: signal on day 0 (close), entry at day 1 (next day close)
- No regime hard gate (downtrend entries perform equally well — validated)
- No minimum soft filters required (CWC and gradient_shape filters are inverted — OFF performs better)
- Soft filters tracked for analysis: `rdv >= 0.8`, `mcs_composite > -0.20`, `cwc > 0.10`, `gradient_shape` favorable

**Exit (priority order, first to fire wins):**
1. Hard stop: P&L < -2.0 × ATR% at entry
2. Trailing: CWVAP rising → trail at VA Low; fallback → 2.0 ATR below peak close (activates after 1.0 ATR gain)
3. Delivery deterioration: `cdvl < -0.5` AND `rdv < 0.6` for 2 consecutive bars
4. Coherence breakdown: `cwc < -0.20`
5. MCS collapse: `mcs_composite < -0.50` AND slope < -0.03
6. PSZ reversal: PSZ crosses back below -0.25 (backstop)
7. PSZ momentum failure: if PSZ peaked below 0.20 during trade and drops 0.10 from peak (after 3+ bars)
8. No time decay (removed — was non-factor)

**Backtest Results (NIFTY 50, ~7 years, new baseline):**
- 3260 trades, 40.6% win rate, +1.45% avg P&L, 2.64x payoff ratio
- Avg winner: +8.07%, avg loser: -3.06%, avg duration: 15.6 bars
- Exit breakdown: psz_reversal 41% (+3.28%), trail_atr 28% (+3.85%), psz_momentum_fail 16% (-2.23%), hard_stop 12% (-6.11%)

**Key findings:**
- Short signals (PSZ↓0.25) are net-destructive (-1.13% avg) — long only
- Regime gate filters out profitable downtrend entries (+1.52% avg) — removed
- CWC and gradient_shape soft filters are inverted (OFF outperforms ON) — not used as gates
- RDV has marginal positive edge (+1.62% ON vs +1.24% OFF) — tracked, not gated
- PSZ momentum failure exit catches trades before hard stop (-2.23% vs -6.11% avg loss)
- Trail ATR at 2.0x lets winners run; PSZ reversal is now the primary exit (41%)

### UI
- OHLC chart with CWVAP overlay and VA trendlines
- Configurable sub-panels (slopes, coherence, RDV, CWC, etc.)
- Sidebar with engine state metrics + watchlist management
- Symbol search

### API
- `/de/api/divergence-engine/{symbol}` — engine data endpoint
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
- `scripts/backtest_long_signals.py` — Long-only backtester with smart exits (parameterized via CLI)
- `scripts/mfe_psz_crossings.py` — MFE/MAE analysis for PSZ threshold crossings

### Infrastructure
- Docker: `Dockerfile` + `docker-compose.yml`
- Build/deploy: `scripts/build_lfm.sh`, `scripts/deploy_lfm.sh`, `scripts/stop_lfm.sh`
- Cache: Redis-based (`src/cache/`)

## What Was Removed (2026-03-15)
- Scoring system (signal_strength, demand/supply direction detection, factor weights)
- CEI module (cumulative evidence index, markers, assisters)
- Old backtester, signal quality report + UI, screener, prod pipeline
- CPOC, POC_spread, price_location, Bollinger-style CVAH/CVAL
- Settings UI (factor weight sliders), YAML config, config manager
- PSZ/RSZ/MCS delta columns, all ML/XGBoost experiments

## Key File Paths
- Engine orchestrator: `src/divergence_engine/engine.py`
- Config: hardcoded in engine.py (VA_PCT=0.70)
- Chart data: `src/divergence_engine/chart.py`
- API: `src/api/main.py`
- DB helper: `src/database.py`
- Cache: `src/cache/`

## Next
- **Earnings avoidance**: Bring in earnings announcement data to skip entries near earnings (BAJAJ-AUTO -14.96% on earnings day identified)
- **Entry quality**: 1-filter trades avg -0.07% — consider requiring min 2 filters, or find better entry filters
- **accum_div as entry filter**: Accumulation divergence present at entry may improve quality
- **MFE left on table**: Trail exits still leave 6.8% avg on table — investigate adaptive trail width by regime
- Build screener to surface live signals from the backtested system
- Target: 1:3 payoff ratio (currently 2.64x)
