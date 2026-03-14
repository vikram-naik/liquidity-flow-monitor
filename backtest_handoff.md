# Backtest Handoff — CEI Signal Backtester

## Status
**Long-only CEI backtester is functional** at `scripts/backtest_cei.py`.
Recent updates add watchlist support, conditional exit logic, and VA entry filters.

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
Phase 2: Try filling pending entry order (limit buy from previous EOD)
         - low <= limit → fill at limit price
         - --chase flag → fill at open if limit misses
Phase 3: EOD — Update Exit Strategy for next session
         - Above VA: Trailing CWVAP stop enabled for intraday check tomorrow
         - Inside VA: Intraday stop disabled; signals (Supply/Supply_Assister) trigger EOD exit
Phase 4: EOD — Check Exit/Entry signals
         - Inside VA: Check for signal-based exit
         - Check for new entry signals (skip if --exclude-va and price is in VA)
Phase 5: Record equity + daily log (cash + unrealised position value)
```

### Data Flow
```
DivergenceEngine(ticker) → engine.run() → EngineResult
  → result.ledger (DataFrame with 70+ columns including cei_signal, cwvap, OHLC)
  → Walk forward from start_date through ledger rows
```

### Key Classes
- `BacktestConfig` — all configurable parameters
- `Trade` — open long position (entry_date, entry_price, quantity, signal)
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
    exclude_va: bool = False             # skip entries within VA boundaries
```

### CLI
```bash
python scripts/backtest_cei.py RELIANCE --start 2024-01-01
python scripts/backtest_cei.py --watchlist "CORE" --start 2025-01-01 --export
python scripts/backtest_cei.py RELIANCE --start 2024-01-01 --exclude-va
python scripts/backtest_cei.py INFY --start 2024-01-01 --capital 200000 --export
```

---

## Exit Logic — CWVAP Trailing Stop

The **sole exit mechanism** is a trailing CWVAP-based stop-loss, modeled as a bracket/trigger order:

```python
def _compute_stop_level(self, cwvap: float, close: float) -> float:
    cwvap_stop = cwvap * (1.0 + self.cfg.stop_cwvap_pct / 100.0)  # e.g. cwvap * 0.98

    if self.active_trade is not None and cwvap_stop >= close:
        # CWVAP stop above current price — use entry floor to avoid
        # immediate stop-out (entered below CWVAP scenario)
        entry_floor = self.active_trade.entry_price * (1.0 + self.cfg.stop_entry_pct / 100.0)
        return entry_floor

    return cwvap_stop
```

**Two levels:**
- `cwvap_stop`: Trails CWVAP upward as institutions accumulate. This is the core exit.
- `entry_floor`: Activated ONLY when `cwvap_stop >= close` (price below CWVAP — would trigger
  immediate stop-out). Once price rises above CWVAP stop, the trailing stop takes over.

**Intraday stop check:**
```python
if open_ <= stop_level:    # gap down → fill at open (worst case slippage)
if low <= stop_level:      # intraday breach → fill at stop level
```

### Resolved: Entry Floor No Longer Prevents Trailing
The original `min(cwvap_stop, entry_floor)` permanently anchored the stop at `entry * 0.97`.
Fixed by conditioning on `cwvap_stop >= close` — the floor only activates when truly needed
(entering below CWVAP), and releases as soon as the trailing stop is safely below price.

---

## Output — Unified Daily Log

Single chronological output combining summary stats + daily walk-forward log.
Every bar shows: date, close, CWVAP, signal, action, entry/exit price, EOD stop level,
in-trade status, and unrealised P&L.

```
  Date       Close       CWVAP  Signal             Action      Entry       Exit  Reason      Stop EOD  InTrade  Unrl P&L
  2025-02-03  1,245.90  1,267.15  Demand_Assister  ORDER
  2025-02-04  1,285.20  1,276.24                   ENTRY      1,248.05                      1,250.72    YES    +2,972.00
  2025-02-06  1,281.55  1,275.31  Demand                                                    1,249.80    YES    +2,680.00
  2025-02-11  1,234.85  1,273.71                   EXIT                  1,244.76  CWVAP_Stop
```

Actions: `ORDER` (limit placed), `ENTRY` (filled), `EXIT` (stopped out), `EXIT+ENTRY` (same bar).
Realized P&L is now recorded in the daily log for all `EXIT` rows.

### Watchlist Batch Mode
- `--watchlist "Name"`: Iterates through all symbols in the specified DB watchlist.
- **Tabular Summary**: Prints a clean table per stock (Capital, Equity, P&L).
- **Aggregate Report**: Prints a grand total Net P&L at the end of the run.
- **Root `data/` Export**: All generated CSVs land in the root `data/` folder for organization.

---

## CEI Signal Types

| Signal | Source | When |
|--------|--------|------|
| `Demand` | `_generate_cei_signals()` | CEI crosses above zero (with cooldown) |
| `Supply` | `_generate_cei_signals()` | CEI crosses below zero (with cooldown, + CWVAP gate) |
| `Demand_Assister` | `_overlay_assister_signals()` | `cei_raw` crosses above `cei` (EMA) |
| `Supply_Assister` | `_overlay_assister_signals()` | `cei_raw` crosses below `cei` (EMA) |

The backtester uses only Demand and Demand_Assister (long only).

---

## Design Decisions Made

1. **Long only** — Short support removed.
2. **100% capital entry** — No partial allocation.
3. **CWVAP zone entry rules** — Demand anywhere, Assister only below CWVAP.
4. **No opposite-signal exits** — Exit purely on trailing stop, never on Supply signals.
5. **Limit order entry** — Signal at close → limit order at close price → fills if next bar low <= limit.
6. **Gap-down slippage** — If open gaps below stop, fills at open (realistic worst case).
7. **Force close at end** — Open position closed at last bar's close with "End_of_Period" reason.
8. **Chase mode** — Optional `--chase` fills at open if limit order misses.

---

## Test Results (2024-01-01 to 2026-03-13, --chase)

| Stock | Net P&L | Trades | Win Rate | PF | Avg Hold | Max DD |
|-------|---------|--------|----------|----|----------|--------|
| RELIANCE | +14.71% | 15 | 33.3% | 2.03 | 26d | 14.0% |
| HDFCBANK | -7.94% | 14 | 50.0% | 0.51 | 37d | 11.0% |
| INFY | -16.62% | 19 | 15.8% | 0.59 | 22d | 28.5% |
| TCS | -19.69% | 23 | 21.7% | 0.44 | 14d | 30.3% |

INFY and TCS are in secular downtrends over this period — negative results expected for long-only.

---

## Files

| File | Purpose |
|------|---------|
| `scripts/backtest_cei.py` | The backtester |
| `src/divergence_engine/engine.py` | Orchestrator — `DivergenceEngine(ticker).run()` |
| `src/divergence_engine/cei.py` | CEI computation + signal generation |
| `src/divergence_engine/config/default_rules.yaml` | All CEI config |

---

## Next Steps

1. **Multi-symbol batch backtest** — loop over NIFTY 500, aggregate statistics
2. **Parameter sensitivity analysis** — sweep stop_cwvap_pct (-1% to -5%), stop_entry_pct
3. **Add time-based exit** — optional max holding period as safety valve
4. **Risk metrics** — Sharpe ratio, Calmar ratio, monthly returns breakdown
5. **Equity curve charting** — matplotlib or export to web UI

---

## Environment
- Python: use `source venv/bin/activate` (system python3 lacks numpy)
- Run from project root: `python scripts/backtest_cei.py ...`
- Redis cache active — flush `de:*` keys if engine config changes
