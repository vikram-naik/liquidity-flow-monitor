# SavgolCTS Signal — Entry / Exit Flow

**Package**: `src/trading/signals/savgol_cts/`
**Last updated**: 2026-06-06

## ⚠️ Mandatory Execution Model (EOD-Lag)
The LFM system operates on an **End-of-Day Lag (EOD-Lag)** model. All signal research and production logic MUST adhere to this:
1. **Signal Generation (Bar i)**: Indicators and guards are evaluated at the close of the trading day.
2. **Execution (Bar i+1)**: The trade is entered at the close (or weighted open) of the following day.
3. **Exit Evaluation (Bar i+2)**: Exit checks begin only after the trade has been open for at least one full bar.

**Note**: Studies using "Bar i" close for entry will drastically overestimate performance by capturing same-day momentum that is unavailable in live execution.

## Global Market Screener
The system maintains a decoupled Screener module (`scripts/daily_screener.py`) specifically scoped to the `NIFTY 500` watchlist.
- Executes daily as the final step in `scripts/daily_sync.sh`.
- Drops previous state from the `screener_signals` database table and recalculates current exact state (`entry`, `in-trade`, `exit`) for all 500 symbols using the `SavgolCTS` logic.
- **Optimization:** The screener utilizes `ProcessPoolExecutor` to parallelize `DivergenceEngine` calculations across all CPU cores and bounds the signal tagging to the last 180 bars (`signal_lookback=180`), reducing execution time to under 1 second when fully cached.
- **Trade Metrics:** Calculates and persists deep trade metrics including `entry_date`, `entry_price`, `bars_held`, `mfe_pct` (Max Favorable Excursion), and `mae_pct` (Max Adverse Excursion).
- "Post-exit" symbols are automatically omitted from the fresh database write on `T+1`, ensuring only actionable or actively managed trades are surfaced to the UI (`/de/screener`).

## Package Structure

```
savgol_cts/
  __init__.py          # re-exports SavgolCTSEntryConfig, SavgolCTSExitConfig, SavgolCTSSignal
  config.py            # Entry/Exit path configs (Universal Cross, Flow Momentum, Coherent Pullback, etc.)
  state.py             # SavgolCTSExitState bitfield helper
  signal.py            # SavgolCTSSignal orchestrator (cooldown, dual-mode dispatch)
  symbol_configs.py    # Dynamic JSON-based Bayesian Weight (BW) config loader
  bw_configs/          # Per-symbol JSON Bayesian Weight configuration files
  entries/             # Entry path modules (6 paths)
  exits/               # Exit path modules (Universal Cross, CWVAP Guard, Anchor Shock)
```

---

## Dual-Mode Entry Architecture

The system operates in **dual mode** based on whether a symbol has Bayesian Weight (BW) optimization:

### Mode 1: Custom Bayesian Path (Symbols WITH BW configs)
For symbols with optimized BW configs (stored as JSON in `bw_configs/`), the entry is routed exclusively through the **Custom Bayesian** path. This path uses per-symbol optimized feature weights and thresholds derived from Bayesian Weight Optimization (BWO).

### Mode 2: Legacy Paths (Symbols WITHOUT BW configs)
For symbols without BW optimization, the entry cascades through 5 legacy paths in priority order:

```
check_entry(row, prev_row, cfg, records, idx)       [signal.py]
  |
  |-- [Guard] Cooldown Active? (cfg.cooldown_enabled)
  |     FAIL --> REJECT "Cooldown active"
  |
  |-- [Check] Custom Bayesian BW config available?
  |     YES --> Route exclusively to Custom Bayesian path
  |     NO  --> Fall through to legacy paths below
  |
  |-- PATH 0: Universal Cross                         [entries/universal_cross.py]
  |     |-- [Gate] Structural inflection (PRT/FAS/CTS/Accel crosses)
  |     |-- [Score] Bayesian scoring with feature bins & weights
  |     +-- PASS --> EntryTag.UNIVERSAL_CROSS
  |
  |-- PATH 1: Secular Trend Pullback                   [entries/trend_pullback.py]
  |     +-- PASS --> EntryTag.TREND_PULLBACK
  |
  |-- PATH 2: Flow Momentum                            [entries/flow_momentum.py]
  |     +-- PASS --> EntryTag.FLOW_MOMENTUM
  |
  |-- PATH 3: Coherent Pullback                        [entries/coherent_pullback.py]
  |     +-- PASS --> EntryTag.COHERENT_PULLBACK
  |
  |-- PATH 4: Anchor Shock Pullback                    [entries/anchor_shock_pullback.py]
  |     +-- PASS --> EntryTag.ANCHOR_SHOCK_PULLBACK
  |
  +-- No path matched --> REJECT
```

---

## Bayesian Weight Optimization (BWO)

The BWO system replaces the deprecated XGBoost ML Guard with a pure technical Bayesian scoring approach:

- **Training**: `scripts/train_symbol_weights_parallel.py` optimizes per-symbol feature weights using historical trade data.
- **Configs**: Output JSON files are stored in `bw_configs/` (configurable via `BW_CONFIGS_DIR` env var).
- **Loading**: `symbol_configs.py` dynamically loads and caches JSON BW overrides at runtime.
- **Scoring**: Entry paths use binned feature weights to compute a composite score against a per-symbol threshold.
- **Weekly Tuning**: Re-run BWO on symbols with existing BW configs to adapt to evolving market conditions.

---

## Exit Flow

All entry paths route through the standardized exit logic:

```
check_exit(row, prev_row, trade, ...)                [signal.py]
  |
  |-- Dispatch to exit_universal_cross()             [exits/universal_cross.py]
  |     |-- [Trigger] CTS crosses below ST trend
  |     |-- [Trigger] PRT slope turns negative
  |     |-- [Trigger] CTS near-miss rollover
  |
  |-- Path-specific: Anchor Shock Pullback exits     [exits/anchor_shock_pullback.py]
  |     |-- Hard stop (catastrophe shield)
  |     |-- Time decay max hold
  |
  +-- apply_cwvap_guard()                            [exits/cwvap_guard.py]
      (Applied after indicator exits, acts as a momentum gatekeeper)
```

### State Bitfield (`SavgolCTSExitState` — `state.py`)

The state is packed into a 20-bit integer, stored in `delivery_bad_count`.

| Bit | Mask    | Name                        | Description |
|---|---|---|---|
| 0 | 0x00001 | `cts_rose`                  | CTS momentum is rising |
| 1 | 0x00002 | `psz_was_above`             | PSZ was above zero at some point |
| 2 | 0x00004 | `cts_above_bt`              | CTS was above Buy Threshold |
| 3 | 0x00008 | `exit_suppressed`           | Exit suppressed by global CWVAP rule |
| 4 | 0x00010 | `suppressed_this_bar`       | Suppressed on current bar |
| 5 | 0x00020 | `slope_crossed_zero`        | Structural momentum crossed above zero |
| 6 | 0x00040 | `price_above_cwvap`         | Price has reclaimed CWVAP post-entry |
| 7 | 0x00080 | `exit_suppressed_ext`       | Path-specific exit was suppressed |
| 8 | 0x00100 | `fas_crossed_zero`          | FAS momentum crossed above zero |
| 9 | 0x00200 | `extreme_bottom`            | Trade hit absolute floor (grant 2x timeout) |
|13-| 0x1E000 | `cwf_count`                 | High > CWVAP and Close < CWVAP (4 bits) |
|17 | 0x20000 | `climax_hit_above_va`       | Structural Climax hit while price > VA_High |
|18 | 0x40000 | `cts_near_miss`             | CTS stalled just below ST (Bare-Touch) |
|19 | 0x80000 | `psz_second_cycle`          | PSZ went positive again during Phase 2 |

## CWVAP Guard (`exits/cwvap_guard.py`)

Applied **after** indicator-generated exit signals. It acts as a gatekeeper to either suppress or release exits based on momentum strength relative to CWVAP. It includes:

1. **Candle Rejection Guard (Currently Disabled)**: Preemptively exits on violent inside bars or long upper wicks at resistance.
2. **Structural Climax Guard**: Preemptively exits when price stretches to historical ceilings (`RP_63 > 0.95` AND `RP_252 > 0.95`) while dangerously overextended from VWAP (`CWVAP_Dist% > 10%` OR `FAS > 1.0`). If price is above `VA_High` when climax hits, it suppresses the exit and converts to a strict trailing stop based on the `VA_High` level.
3. **Momentum Suppression**: Suppresses normal cycle exits as long as structural momentum (PSZ > 0 or CTS > 0) is holding above the Custom VWAP.
