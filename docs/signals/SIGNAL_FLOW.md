# SavgolCTS Signal — Entry / Exit Flow

**Package**: `src/trading/signals/savgol_cts/`
**Last updated**: 2026-07-09

## ⚠️ Mandatory Execution Model (EOD-Lag)
The LFM system operates on an **End-of-Day Lag (EOD-Lag)** model. All signal research and production logic MUST adhere to this:
1. **Signal Generation (Bar i)**: Indicators and guards are evaluated at the close of the trading day.
   - **Entries**: Entry signals create positions in the `proposed` state (held in the approval queue).
   - **Exits**: Exit signals transition open positions to the `proposed_exit` state (held in the approval queue).
2. **User Approval**:
   - Approving proposed entries promotes them to `pending_entry`.
   - Approving proposed exits promotes them to `pending_exit`.
3. **Execution (Bar i+1)**: Orders are executed via the OrderExecutor.
   - Programmatic lag enforcement prevents execution of any `pending_entry` or `pending_exit` whose signal date is equal to the current execution date.
4. **Exit Evaluation (Bar i+2)**: Exit checks begin only after the trade has been open for at least one full bar.

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

- **22-Feature Set**: Expands the continuous feature set to 22 features by introducing 5 new lookback context features to handle falling knives, distribution traps, accumulation bases, and bottom rebounds:
  - `fas_slope`: Derivative of `fas` using Savitzky-Golay coefficients to capture the speed/velocity of range position movement.
  - `fas_slope_sum_5`: Rolling sum of `fas_slope` over 5 bars. Captures sustained downward momentum (falling knives) when deeply negative.
  - `fas_min_10`: Rolling minimum of `fas` over 10 bars. Identifies distribution traps (basing at the top) when it remains high.
  - `fas_max_10`: Rolling maximum of `fas` over 10 bars. Identifies accumulation bases when it remains low.
  - `fas_slope_change_3`: 3-bar difference in `fas_slope` (`diff(3)`). Captures acceleration of bottom rebound inflections.
- **Regime-Conditioned Model Partitioning**: Custom Bayesian entries route through two distinct sub-models to handle different market regimes:
  - **Accumulation Model**: Active when `regime` is `'downtrend'` or `'notrend'`. Automatically isolates bottom accumulation setups.
  - **Momentum Model**: Active when `regime` is `'uptrend'` or `'transition'`. Automatically handles breakout momentum signals.
- **Minimum Probability Gate**: After passing the symbol-specific score threshold, entries are additionally rejected if `bayesian_score < 0` (equivalent to `conviction_score < 50`, i.e. the model's log-odds imply < 50% win probability). This is a global floor that applies regardless of threshold calibration and removes low-confidence entries that inflate trade count without adding expectancy.
- **Training**: `scripts/train_symbol_weights.py` (which supports both parallel and sequential execution, with parallel enabled by default) partitions historical candidate bars and independently optimizes weights/thresholds for both sub-models. To optimize sweeps, triggers and sub-model scores are precalculated once per config, and passed down as pre-converted list records to avoid redundant calculations and dict conversions. Redundant `copy.deepcopy` calls are minimized (reduced from 208 to 3 per sweep), resulting in an almost 2x speedup in optimization run times (~1 minute per symbol). Clean `tqdm` progress bars track optimization progress.
- **Configs**: Output JSON files with `accumulation` and `momentum` sub-blocks are stored in `bw_configs/` (configurable via `BW_CONFIGS_DIR` env var).
- **Loading**: `symbol_configs.py` dynamically parses, loads, and caches the partitioned JSON overrides at runtime.
- **Weekly Tuning & Parameter Suggestion**: Challenger-Champion promo loop verifies configurations against safety gates. Symbol BWO overrides (defined in `bwo_symbol_params.json`) are automatically suggested before sweeps by `scripts/suggest_bwo_params.py` based on volatility/ATR and setup frequency. Clean `tqdm` progress tracking handles batch execution across symbols.


---

## Exit Flow

All entry paths route through the standardized exit logic:

```
check_exit(row, prev_row, trade, ...)                [signal.py]
  |
  |-- Dispatch to exit_universal_cross()             [exits/universal_cross.py]
  |     |-- [Trigger] CTS crosses below ST trend (Suppressed on first bar held (bars_held <= 1) for momentum setups)
  |     |-- [Trigger] PRT slope turns negative
  |     |-- [Trigger] CTS near-miss rollover
  |
  |-- Path-specific: Anchor Shock Pullback exits     [exits/anchor_shock_pullback.py]
  |     |-- Hard stop (catastrophe shield)
  |     |-- Time decay max hold
  |
  +-- apply_cwvap_guard()                            [exits/cwvap_guard.py]
      (Applied after indicator exits, acts as a momentum gatekeeper)
        |-- [Check] Trend Reclaim Reset: Clears suppression if PRT >= PRT_ST
        |-- [Check] Raw CWVAP suppression / climax checks
        +-- [Check] Expert 5 Exit Suite (Activated above 10.0% peak close PnL):
              |-- [Exit] Regime-Aware ATR Trail (3.0*ATR in uptrend, 2.0*ATR normal)
              |-- [Exit] Regime-Aware Coherence Breach (CWC/CWC_slope/PSZ_V Z-score floors)
              +-- [Exit] Regime-Aware Parabolic Low-Break (0.3*ATR buffer in uptrend, 0 buffer normal)
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

1. **Trend Reclaim Reset**: If a trade is suppressed, but the stock re-accelerates and price range trend becomes bullish (`prt >= prt_sell_threshold`), the suppression is cleared to allow normal exits on subsequent inflections.
2. **Candle Rejection Guard (Currently Disabled)**: Preemptively exits on violent inside bars or long upper wicks at resistance.
3. **Structural Climax Guard**: Preemptively exits when price stretches to historical ceilings (`RP_63 > 0.95` AND `RP_252 > 0.95`) while dangerously overextended from VWAP (`CWVAP_Dist% > 10%` OR `FAS > 1.0`). If price is above `VA_High` when climax hits, it suppresses the exit and converts to a strict trailing stop based on the `VA_High` level.
4. **Momentum Suppression**: Suppresses normal cycle exits as long as structural momentum (PSZ > 0 or CTS > 0) is holding above the Custom VWAP.
5. **Expert 5 Exits (Regime-Aware Hybrid)**: Once a trade reaches a peak close profit of `>= 10.0%`, if it is suppressed by the guard, the system monitors it using three regime-aware exits to lock in gains:
   - **Regime-Aware Trailing Stop**: Trails peak close by `3.0 * ATR` in strong `uptrend` regimes to let winners run, and `2.0 * ATR` in normal/weaker regimes to lock in gains quickly.
   - **Regime-Aware Coherence Breach**: Exits if flow coherence degrades (`cwc < 0.25` and `cwc_slope < -0.04` in normal regimes, or `cwc < 0.10` and `cwc_slope < -0.06` in strong `uptrend` regimes to prevent premature shakeouts).
   - **Regime-Aware Parabolic Low-Break**: Exits if overextended and price closes below the previous day's low. In strong `uptrend` regimes, it requires a volatility buffer of `0.30 * ATR` to filter out minor noise. Additionally, Option 2A (Coherence-based State-Dependent Suppression) is active in strong uptrends: the exit is suppressed if structural coherence is high (CWC >= 0.35 OR CWC slope >= -0.02) to avoid premature shakeouts.

## Indicator Exits (`exits/universal_cross.py`)

The strategy features multiple indicator-based exits inside the Universal Cross module, including:
1. **CTS Near-Miss Rollover**: Triggered when the CTS momentum indicator gets very close to the target sell threshold but stalls (`cts_st - cts <= 0.10`) and subsequently rolls over (`cts < prev_cts`). **Following Option D optimization, this exit triggers immediately on the first bar of rollover (default `cts_near_miss_rollover_level = 1.10`) to preserve profits and avoid catastrophic pullbacks.**
