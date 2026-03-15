# Backtest Handoff — CEI Signal Backtester

## Status
**Long-only CEI backtester is functional** at `scripts/backtest_cei.py`.
Includes CEI threshold exit (primary), watchlist support, conditional exit logic,
and VA entry filters.

**Completed (2026-03-15):** **CEI threshold exit** — `cei_exit_threshold: 0.08`
is now the primary exit gate. Signal_Exit only fires when `|CEI| >= 0.08`,
requiring strong counter-evidence before closing a position. NIFTY 50 result:
**+443,188 P&L** (4× baseline, 2× old MinHold+VA filters). Old filters
(min_hold_bars, va_primary_only) retained as opt-in secondary filters but
disabled by default.

---

## Architecture

### Execution Model (EOD System)
End-of-Day system for Indian equities (NSE). Signals arrive after market close.

```
Signal at EOD close → Limit buy order at close price
  → Next bar: check if low <= limit price → fill
  → Every EOD: update CWVAP trailing stop (bracket order)
  → Next bar: check if OHLC triggers stop → exit
```

### Walk-Forward Loop (5 Phases per Bar)
```
Phase 1: Check CWVAP trailing stop (intraday via OHLC) — ACTIVE ONLY ABOVE VA
         - Gap down through stop → fill at open (slippage)
         - Intraday breach → fill at stop level
         - ALWAYS fires regardless of min_hold (safety net)
Phase 2: Try filling pending entry order (limit buy from previous EOD)
         - low <= limit → fill at limit price
         - --chase flag → fill at open if limit misses
         - Stores bar_counter as entry_bar_idx on Trade for min hold tracking
Phase 3: EOD — Update Exit Strategy for next session
         - Above VA: Trailing CWVAP stop enabled for intraday check tomorrow
         - Inside VA: Intraday stop disabled
Phase 4: EOD — Check Exit/Entry signals (WITH EXIT FILTERS)
         - Supply/Supply_Assister signals checked against two filters:
           Filter A: min_hold_bars — suppress Signal_Exit if bars_held < N
           Filter B: va_primary_only — inside VA, only primary Supply exits
         - Check for new entry signals (skip if --exclude-va and price in VA)
Phase 5: Record equity + daily log (cash + unrealised position value)
```

### Data Flow
```
DivergenceEngine(ticker) → engine.run() → EngineResult
  → result.ledger (DataFrame with 70+ columns including cei_signal, cwvap, OHLC)
  → Walk forward from start_date through ledger rows
```

### Key Classes
- `BacktestConfig` — all configurable parameters including exit filters
- `Trade` — open long position (entry_date, entry_price, quantity, signal, entry_bar_idx)
- `ClosedTrade` — completed round-trip with exit info, P&L calculation
- `PendingOrder` — limit buy waiting to fill on next bar
- `Backtester` — main engine with cash/position tracking, order management, daily log

---

## Entry Logic — CWVAP Zone Rules

Entry rules are CWVAP-zone-aware (no `--entry-mode` flag):

| Price Zone | Demand | Demand_Assister |
|------------|--------|-----------------|
| **Above CWVAP** | Allowed | Blocked |
| **Below CWVAP** | Allowed | Allowed |

Below CWVAP, whichever signal comes first triggers entry.
Above CWVAP, only primary Demand signals can open — assisters are filtered out
because they tend to be whipsaws when price is already above institutional cost basis.

---

## Configuration

```python
@dataclass
class BacktestConfig:
    symbol: str
    start_date: str
    end_date: str | None = None
    capital: float = 100_000.0

    # Trailing stop
    stop_cwvap_pct: float = -2.0   # cwvap * 0.98
    stop_entry_pct: float = -3.0   # entry * 0.97 (floor)

    # Execution
    chase_if_limit_misses: bool = False
    exclude_va: bool = False

    # Exit filters
    cei_exit_threshold: float = 0.08  # |CEI| must exceed this for Signal_Exit (0 = disabled)
    min_hold_bars: int = 0            # suppress Signal_Exit for N bars (0 = disabled)
    va_primary_only: bool = False     # inside VA, only primary Supply can exit
```

### CLI
```bash
python scripts/backtest_cei.py RELIANCE --start 2024-01-01 --chase
python scripts/backtest_cei.py --watchlist "NIFTY 50" --start 2024-01-01 --chase
python scripts/backtest_cei.py RELIANCE --start 2024-01-01 --chase --cei-exit 0  # disable CEI threshold (baseline)
python scripts/backtest_cei.py RELIANCE --start 2024-01-01 --chase --min-hold 5 --va-primary  # old filters
python scripts/backtest_cei.py INFY --start 2024-01-01 --capital 200000 --export
```

---

## Exit Logic

### CWVAP Trailing Stop (always active)
```python
def _compute_stop_level(self, cwvap: float, close: float) -> float:
    cwvap_stop = cwvap * (1.0 + self.cfg.stop_cwvap_pct / 100.0)
    if self.active_trade is not None and cwvap_stop >= close:
        entry_floor = self.active_trade.entry_price * (1.0 + self.cfg.stop_entry_pct / 100.0)
        return entry_floor
    return cwvap_stop
```

**Two levels:**
- `cwvap_stop`: Trails CWVAP upward as institutions accumulate.
- `entry_floor`: Only when `cwvap_stop >= close` (price below CWVAP).

**Intraday check:** Ignores min_hold — safety net always fires.

### Signal_Exit Filters (Phase 4)
Three filters gate whether a Supply/Supply_Assister signal can trigger exit.
Evaluated in order; any filter can block the exit.

**Filter 0 — CEI Magnitude Gate (`cei_exit_threshold`, default 0.08):**
Signal_Exit only fires when `|CEI| >= threshold`. Requires strong directional
counter-evidence before exiting. Self-calibrating: choppy stocks with low
CEI amplitude never trigger, so the CWVAP stop catches breakdowns. Stocks
with genuine regime change push CEI past threshold, triggering appropriate exit.
Set to 0 to disable.

**Filter A — Minimum Hold Period (`min_hold_bars`, default 0 = disabled):**
After entry, Signal_Exit is suppressed for N bars. Prevents whipsaw from
rapid Demand→Supply oscillations in sideways markets. Redundant with CEI
threshold — retained as opt-in secondary filter.

**Filter B — VA Primary-Only Exit (`va_primary_only`, default false):**
Inside VA (`va_low <= close <= va_high`), only primary `Supply` signals can
exit — `Supply_Assister` is ignored. Outside VA, both can exit. Redundant
with CEI threshold — retained as opt-in secondary filter.

---

## Exit Strategy Investigation (2026-03-15) — KEY FINDINGS

### Problem
COALINDIA: 12/27 trades hold ≤5 days (7 trades 1-2 days), all Signal_Exit.
Rapid Demand→Supply oscillation inside VA bleeds capital through whipsaw.
Short-hold Signal_Exit trades alone cost -10,211 (39% of total losses).

### Signal_Exit Quality Analysis (NIFTY 50, 802 Signal_Exit trades)
- Exit correct at 5d: **48.6%** — worse than coin flip
- Exit correct at 10d: **46.0%** — price goes UP +0.54% avg after exit
- 94% of Signal_Exits are from Supply_Assister (752/802)
- Primary Supply exits: 50 trades, avg P&L -3.11% (trades already losing)
- Supply_Assister exits: 752 trades, avg P&L +0.36% (cutting winners)

### Per-trade context analysis
No stock-level characteristic predicts whether exit filters help or hurt
(all correlations < 0.2). VA width, CEI amplitude, regime mix, signal
density — none separate "filter helps" from "filter hurts" stocks.

Only CEI magnitude at exit (|CEI| quartile Q4) showed marginal predictive
value: 53.6% exit correct vs 46% baseline. Unrealised P&L, CWVAP distance,
hold duration — all near random.

### Exit Strategy Comparison (NIFTY 50, 2024-01-01 to 2026-03-13, --chase)

| Strategy | Total P&L | Trades | WR | AvgDD | AvgHold |
|---|---|---|---|---|---|
| Baseline (all Signal_Exit) | +105,712 | 877 | 44% | 22.9% | 20d |
| MinHold 5 + VA Primary | +227,792 | 390 | 41% | 28.2% | 86d |
| CEI threshold 0.03 | +166,399 | 626 | 47% | 25.2% | 36d |
| CEI threshold 0.05 | +239,336 | 497 | 47% | 26.7% | 53d |
| **CEI threshold 0.08** | **+443,188** | **322** | **50%** | **27.6%** | **99d** |
| No Signal_Exit (pure stop) | +424,795 | 129 | 55% | 28.4% | 336d |
| Primary Supply only | +225,670 | 304 | 44% | 28.6% | 113d |

### Key Insight
Signal_Exit is net-destructive. The more aggressively you exit on Supply
signals, the worse the performance. Removing Signal_Exit entirely
**quadruples** P&L. The CWVAP trailing stop already handles genuine
breakdowns — adding signal-based exits on top just cuts winners.

### CEI Threshold Exit (IMPLEMENTED — default)
Only allows Signal_Exit when `|CEI| >= 0.08` — requires strong directional
counter-evidence before exiting. Self-calibrating: choppy stocks with low
CEI amplitude never trigger, so the stop catches breakdowns. Stocks with
genuine regime change push CEI past threshold, triggering appropriate exit.

Best balance of P&L (+443K, 4× baseline), trade frequency (322), and
holding period (99d). Old MinHold+VA filters are redundant and disabled
by default (can be re-enabled via `--min-hold N` and `--va-primary`).

---

## Output — Unified Daily Log

Single chronological output combining summary stats + daily walk-forward log.

```
  Date       Close       CWVAP  Signal             Action      Entry       Exit  Reason      Stop EOD  InTrade  Unrl P&L
  2025-02-03  1,245.90  1,267.15  Demand_Assister  ORDER
  2025-02-04  1,285.20  1,276.24                   ENTRY      1,248.05                      1,250.72    YES    +2,972.00
  2025-02-06  1,281.55  1,275.31  Demand                                                    1,249.80    YES    +2,680.00
  2025-02-11  1,234.85  1,273.71                   EXIT                  1,244.76  CWVAP_Stop
```

Report header shows active exit filters.

### Watchlist Batch Mode
- `--watchlist "Name"`: Iterates through all symbols in the specified DB watchlist.
- **Tabular Summary**: Prints a clean table per stock (Capital, Equity, P&L).
- **Aggregate Report**: Prints a grand total Net P&L at the end of the run.

---

## CEI Signal Types

| Signal | Source | When |
|--------|--------|------|
| `Demand` | `_generate_cei_signals()` | CEI crosses above zero (with cooldown + min_crossing_gap) |
| `Supply` | `_generate_cei_signals()` | CEI crosses below zero (with cooldown + min_crossing_gap + CWVAP gate) |
| `Demand_Assister` | `_overlay_assister_signals()` | `cei_raw` crosses above `cei` (EMA) |
| `Supply_Assister` | `_overlay_assister_signals()` | `cei_raw` crosses below `cei` (EMA) |

The backtester uses only Demand and Demand_Assister (long only).

---

## Design Decisions Made

1. **Long only** — Short support removed.
2. **100% capital entry** — No partial allocation.
3. **CWVAP zone entry rules** — Demand anywhere, Assister only below CWVAP.
4. **Exit filters** — MinHold 5 + VA Primary-only (default ON).
5. **Limit order entry** — Signal at close → limit at close price → fills if next bar low <= limit.
6. **Gap-down slippage** — If open gaps below stop, fills at open (realistic worst case).
7. **Force close at end** — Open position closed at last bar's close with "End_of_Period" reason.
8. **Chase mode** — Optional `--chase` fills at open if limit order misses.

---

## Test Results — Current Defaults (CEI≥0.08, --chase)

### NIFTY 50 aggregate (2024-01-01 to 2026-03-13)

| Strategy | Total P&L | vs Baseline |
|---|---|---|
| Baseline (all Signal_Exit, no filters) | +105,712 | 1.0x |
| Old filters (MinHold 5 + VA Primary) | +227,792 | 2.2x |
| **CEI threshold 0.08 (current default)** | **+443,188** | **4.2x** |
| CEI 0.08 + MinHold 5 + VA Primary | +400,971 | 3.8x |
| No Signal_Exit (pure stop) | +424,795 | 4.0x |

CEI threshold alone outperforms all other strategies. Adding MinHold+VA on top
*reduces* P&L by ~42K — the secondary filters occasionally block correct exits.

---

## Files

| File | Purpose |
|------|---------|
| `scripts/backtest_cei.py` | The backtester (with exit filters) |
| `scripts/test_exit_filters.py` | Exit filter strategy test harness |
| `scripts/test_dual_ema_assister.py` | Dual-EMA assister experiment (concluded: not useful) |
| `src/divergence_engine/engine.py` | Orchestrator — `DivergenceEngine(ticker).run()` |
| `src/divergence_engine/cei.py` | CEI computation + signal generation |
| `src/divergence_engine/config/default_rules.yaml` | All CEI config |

---

## Next Steps

1. **Multi-symbol batch backtest** — NIFTY 500 with CEI threshold exit
2. **Parameter sensitivity** — sweep cei_exit_threshold (0.05-0.12),
   stop_cwvap_pct (-1% to -5%)
3. **Risk metrics** — Sharpe ratio, Calmar ratio, monthly returns breakdown
4. **Equity curve charting** — matplotlib or export to web UI
5. **Run 5 signal quality report** — with noise-reduced CEI config

---

## Environment
- Python: use `venv/bin/python3` (system python3 lacks numpy)
- Run from project root: `python scripts/backtest_cei.py ...`
- Redis cache active — flush `de:*` keys if engine config changes
