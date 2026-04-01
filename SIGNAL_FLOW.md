# SavgolCTS Signal — Entry / Exit Flow

**Package**: `src/trading/signals/savgol_cts/`
**Last updated**: 2026-04-01

## Package Structure

```
savgol_cts/
  __init__.py          # re-exports SavgolCTSEntryConfig, SavgolCTSExitConfig, SavgolCTSSignal
  config.py            # per-path entry/exit config dataclasses + composites
  state.py             # SavgolCTSExitState bitfield helper
  scoring.py           # compute_intensity() — shared intensity scoring
  signal.py            # SavgolCTSSignal orchestrator (cooldown, ST exit, dispatch)
  entries/
    floor_touch.py     # Path 0: CTS+BT pinned at floor, PSZ deeply oversold
    floor_leave.py     # Path 1: CTS rises above floor after being pinned
    bt_cross.py        # Path 2: CTS crosses BT from below in oversold zone
    cwvap_reclaim.py   # Path 3: cts_slope crosses zero, close > CWVAP, PSZ > 0
    cwvap_cross.py     # Path 4: price crosses CWVAP from below + accel rising
    slope_bottom.py    # Path 5: cts_slope rising from P5 bottom in downtrend
    pdd_divergence.py  # Path 6: shallow CTS bottom coupled with deep PDD exhaustion
  exits/
    floor.py           # Floor/Floor-Leave/Floor-Touch exit path
    bt_cross.py        # BT-Cross exit path + PSZ stall helper
    cwvap_reclaim.py   # CWVAP Reclaim exit: bar-3 stop, PnL cap, CWVAP lost
    slope_bottom.py    # Slope Bottom exit: pure slope zero-cross cycle
    pdd_divergence.py  # PDD Divergence exit: pure trailing and CWVAP support
    psz_glide.py       # Shared PSZ glide exit (5-bar mean crossover)
    cwvap_guard.py     # CWVAP suppression / release logic (post-exit)
```

---

## Entry Flow

Seven entry paths evaluated in priority order.  First match wins.

```
check_entry(row, prev_row, cfg, records, idx)       [signal.py]
  |
  |-- [Guard] Cooldown Active? (cfg.cooldown_enabled)
  |     bars_since_exit <= cfg.cooldown_bars
  |     AND last_exit_reason in cfg.cooldown_exit_reasons
  |     FAIL --> REJECT "Cooldown active", metadata: {"cooldown": True}
  |
  |-- [Guard] CTS is NaN? --> REJECT "Missing CTS data"
  |
  |-- PATH 0: Floor Touch                           [entries/floor_touch.py]
  |     |-- [Gate]  cfg.floor_touch.enabled?
  |     |-- [Guard] CTS or BT is NaN?
  |     |-- [Guard] CTS <= floor AND BT <= floor  (floor = cts_floor + floor_zone_tolerance)
  |     |-- [Guard] PSZ < cfg.floor_touch.psz_max (-0.25)
  |     |-- [Guard] CWVAP distance > cfg.cwvap_max_dist (-5.0%)
  |     |-- [Guard] DVWAP bear stack gate (cfg.dvwap_bear_stack_gate_enabled)
  |     +-- PASS --> EntryTag.CTS_FLOOR_TOUCH
  |
  |-- PATH 1: CTS-Floor-Leave                       [entries/floor_leave.py]
  |     |-- [Guard] PSZ late entry (psz_raw >= 0 or >= psz_min_threshold)
  |     |-- [Guard] prev_cts <= floor AND cts > floor  (floor-leave condition)
  |     |-- [Guard] Data complete (cwvap, close, psz_v)
  |     |-- [Guard] CWVAP trap floor (dist% <= cfg.floor_leave.cwvap_trap_hi)
  |     |-- [Guard] Vertical jump ceiling (cts > cfg.floor_leave.cts_max)
  |     |-- [Guard] Conviction gate (|psz_v| >= cfg.floor_leave.pszv_min)
  |     +-- PASS --> EntryTag.CTS_FLOOR_LEAVE
  |
  |-- PATH 2: BT-Cross                              [entries/bt_cross.py]
  |     |-- [Gate]  cfg.bt_cross.enabled?
  |     |-- [Guard] CTS crossed BT from below (prev_cts <= prev_bt, cts > bt)
  |     |-- [Guard] BT not newly dropped to floor
  |     |-- [Guard] CTS <= cfg.bt_cross.oversold_threshold (-0.50)
  |     |-- [Guard] PSZ late entry gate
  |     |-- [Guard] Flat psz_v gate (cfg.bt_cross.flat_gate_enabled)
  |     |-- [Guard] DVWAP bear stack gate
  |     +-- PASS --> EntryTag.BT_CROSS
  |
  |-- PATH 3: CWVAP Reclaim                          [entries/cwvap_reclaim.py]
  |     |-- [Gate]  cfg.cwvap_reclaim.enabled?
  |     |-- [Guard] cts_slope crosses zero from below (prev <= 0, now > 0)
  |     |-- [Guard] slope diff > 0.005 (minimum impulse)
  |     |-- [Guard] cts_accel >= cts_accel_threshold (dynamic)
  |     |-- [Guard] CTS > cfg.cwvap_reclaim.cts_min (0.0)
  |     |-- [Guard] CTS <= cfg.cwvap_reclaim.cts_max (0.85)
  |     |-- [Guard] close > CWVAP (price above institutional average)
  |     |-- [Guard] PSZ > cfg.cwvap_reclaim.psz_min (0.0)
  |     |-- [Guard] cwvap_dist% <= cfg.cwvap_reclaim.cwvap_dist_max (7.0%)
  |     |-- [Guard] close <= VA high (not above value area)
  |     |-- [Guard] ST guard: CTS < ST - st_guard_tolerance (-0.10)
  |     +-- PASS --> EntryTag.CWVAP_RECLAIM
  |
  |-- PATH 4: CWVAP Cross                               [entries/cwvap_cross.py]
  |     |-- [Gate]  cfg.cwvap_cross.enabled?
  |     |-- [Guard] open < CWVAP AND close > CWVAP (price crosses from below)
  |     |-- [Guard] cts_accel > cts_accel_threshold + accel_margin_min (0.01)
  |     |-- [Guard] PSZ > cfg.cwvap_cross.psz_min (-0.35)
  |     |-- [Guard] CTS > cfg.cwvap_cross.cts_min (0.0)
  |     |-- [Guard] CTS <= cfg.cwvap_cross.cts_max (0.85)
  |     |-- [Guard] ST guard: CTS < ST - st_guard_tolerance (-0.10)
  |     +-- PASS --> EntryTag.CWVAP_CROSS
  |
  |-- PATH 5: Slope Bottom                               [entries/slope_bottom.py]
  |     |-- [Gate]  cfg.slope_bottom.enabled?
  |     |-- [Guard] cts_slope <= slope_threshold (-0.10, P5 empirical bottom)
  |     |-- [Guard] cts_slope rising (slope > prev_slope)
  |     |-- [Guard] regime == "downtrend"
  |     |-- [Guard] slope_delta <= slope_delta_max (0.02, reject dead-cat bounces)
  |     |-- [Guard] cwvap_dist% >= cwvap_dist_min (-3.0%, not too far below)
  |     |-- [Guard] cwvap_dist% <= cwvap_dist_max (0.5%, must be below CWVAP)
  |     +-- PASS --> EntryTag.SLOPE_BOTTOM
  |
  |-- PATH 6: PDD Divergence                             [entries/pdd_divergence.py]
  |     |-- [Gate]  cfg.pdd_divergence.enabled?
  |     |-- [Guard] pdd_120 <= pdd_threshold (-2.0, deep institutional exhaustion)
  |     |-- [Guard] cts_slope in shallow range (-0.10 to -0.04)
  |     |-- [Guard] cts_slope rising (slope > prev_slope)
  |     |-- [Guard] cts <= cts_max (-0.70)
  |     |-- [Guard] regime in ["downtrend", "notrend"]
  |     |-- [Guard] slope_delta in range [0.002, 0.025]
  |     |-- [Guard] cts_accel > prev_accel and > threshold
  |     |-- [Guard] cwvap_dist% in range [-4.5%, 0.5%]
  |     +-- PASS --> EntryTag.PDD_DIVERGENCE
  |
  +-- No path matched --> REJECT (last meta from final path)
```

### Intensity Scoring (shared — `scoring.py`)

```
Base:       20
Regime:     +10 downtrend, +5 notrend, +0 uptrend
PDD_120:    +0..10 (closer to zero = less exhaustion = better, context dependent)

Path-Specific Scoring (Max 60-70 points):
- Slope Bottom (Mean Reversion):
  - Exhaustion Depth (CTS): -0.85 to -1.0 (+0..20)
  - Structural Dislocation (cwvap_dist): Deeper is better (+0..20)
  - Inflection Sharpness (slope_delta): Higher is better (+0..20)

- PDD Divergence (Shallow Bottom):
  - Shared scoring framework mapped similarly to slope_bottom metrics

- CWVAP Reclaim / Cross (Trend Continuation):
  - Tightness of Entry (cwvap_dist): Closer to 0 is better (+0..20)
  - Momentum Thrust (accel_margin): Higher is better (+0..20)
  - Price Velocity (psz): Higher is better (+0..20)
            ---------------------------------
Range:      0 .. 100  (clamped)

Labels:     >= 80 "STRONG", >= 65 "good", else unlabelled
```

---

## Exit Flow

Exit is branched by entry tag.  `check_exit` (in `signal.py`) dispatches to
the path-specific exit, then applies the CWVAP guard.

```
check_exit(row, prev_row, trade, ...)                [signal.py]
  |
  |-- tag in (FLOOR_LEAVE, BT_FLOOR, FLOOR_TOUCH)
  |     --> exit_floor()                             [exits/floor.py]
  |     --> if None: exit_psz_glide() fallback       [exits/psz_glide.py]
  |
  |-- tag == BT_CROSS
  |     --> exit_bt_cross()                          [exits/bt_cross.py]
  |
  |-- tag in (CWVAP_RECLAIM, CWVAP_CROSS)
  |     --> exit_cwvap_reclaim()                     [exits/cwvap_reclaim.py]
  |     (bypasses CWVAP guard — exit is CWVAP-based)
  |
  |-- tag == SLOPE_BOTTOM
  |     --> exit_slope_bottom()                      [exits/slope_bottom.py]
  |     (bypasses CWVAP guard — exit is slope-based)
  |
  |-- tag == PDD_DIVERGENCE
  |     --> exit_pdd_divergence()                    [exits/pdd_divergence.py]
  |     (bypasses CWVAP guard — exit is heavily CWVAP/Trailing based)
  |
  |-- [Optional] ST exit (cfg.st_exit_enabled, default False)
  |
  +-- apply_cwvap_guard()  (skipped for CWVAP_RECLAIM/CWVAP_CROSS/SLOPE_BOTTOM/PDD_DIVERGENCE)  [exits/cwvap_guard.py]
```

### State Bitfield (`delivery_bad_count` repurposed — `state.py`)

```
bit 0 (& 0x01): cts_rose          — CTS has risen above -1.0 during this trade
bit 1 (& 0x02): psz_was_above     — PSZ crossed above glide threshold
bit 2 (& 0x04): cts_above_bt      — CTS has been above BT during this trade
bit 3 (& 0x08): exit_suppressed   — An indicator exit was suppressed by CWVAP Rule A
bit 4 (& 0x10): suppressed_this_bar — Exit was suppressed on this specific bar
bit 5 (& 0x20): slope_went_negative — CWVAP Reclaim: cts_slope went <= 0 post-entry
                                      Slope Bottom: repurposed as slope_crossed_zero (slope > 0 reached)
```

---

### Exit: Floor path (`exits/floor.py`)

Tags: `CTS_FLOOR_LEAVE`, `CTS_BT_FLOOR`, `CTS_FLOOR_TOUCH`

```
exit_floor(row, prev_row, trade, peak_close, bars_held, state, cfg, records, idx)
  |-- Update cts_rose, cts_above_bt, psz_was_above
  |-- EXIT: Floor hit — cts_rose AND cts <= -1.0 AND bars_held >= floor_hit_min_bars
  |-- EXIT: BT hit — cts_above_bt AND cts <= bt AND bars_held >= bt_hit_min_bars
  |-- EXIT: Ceiling-leave — prev_cts >= ceiling AND cts < ceiling
  +-- None --> HOLD (then signal.py tries psz_glide fallback)
```

### Exit: BT-Cross path (`exits/bt_cross.py`)

```
exit_bt_cross(row, prev_row, trade, ...)
  |-- Update cts_rose, psz_was_above
  |-- PSZ stall check (cfg.bt_cross.psz_stall_enabled)
  |-- Bar-3 PnL stop (cfg.bt_cross.bar3_stop_enabled)
  |-- [Gate] cts_rose? (must have risen before any exit fires)
  |-- EXIT: Floor hit — cts <= -1.0 AND bars_held >= floor_hit_min_bars
  |-- SUPPRESS: Floor zone — cts and bt both within floor_tolerance
  |-- EXIT: Ceiling-leave
  |-- EXIT: PSZ glide (psz_was_above, via exit_psz_glide)
  |-- EXIT: BT hit — bt above floor_zone AND cts <= bt
  +-- None --> HOLD
```

### Exit: CWVAP Reclaim path (`exits/cwvap_reclaim.py`)

Tags: `CWVAP_RECLAIM`, `CWVAP_CROSS`

Five exit conditions in priority order for CWVAP Reclaim/Cross.
Bypasses the CWVAP guard entirely (exit logic is itself CWVAP-based).

```
exit_cwvap_reclaim(row, prev_row, trade, ...)
  |-- EXIT: Bar-3 PnL stop — bars_held == bar3_stop_bar AND PnL% < bar3_stop_threshold
  |         --> ExitReason.BAR3_STOP
  |         Empirically (NIFTY 500 TEST): fires ~24%, avg -4.1%, cuts early losers.
  |-- EXIT: Bar-5 breakeven gate — bars_held == bar5_stop_bar AND PnL% < bar5_stop_threshold
  |         --> ExitReason.BAR5_STOP
  |         Catches flat-drifter losers (66% save rate, +0.7x payoff lift).
  |-- EXIT: PnL cap — PnL% >= pnl_cap_pct (8.0%)
  |         --> ExitReason.PNL_CAP
  |         Empirically (NIFTY 500 TEST): fires ~37%, 100% WR, avg +10.3%.
  |-- EXIT: LH+LL trend break — confirmed lower-high + lower-low after peak
  |         --> ExitReason.LH_LL_BREAK
  |         Uses 2-bar pivot swing detection. After trade's peak close, if a
  |         confirmed swing high < previous swing high AND confirmed swing low
  |         < previous swing low, the uptrend structure is broken.
  |         Empirically (NIFTY 500 TEST): fires ~12.5%, 74.9% WR, avg +4.40%.
  |         90.7% save rate on would-be CWVAP Lost trades (+3.45% improvement).
  |-- EXIT: CWVAP lost — close < CWVAP - ATR*mult AND high < CWVAP
  |         --> ExitReason.CWVAP_LOST
  +-- None --> HOLD
```

### Exit: Slope Bottom path (`exits/slope_bottom.py`)

Tags: `SLOPE_BOTTOM`

Pure slope-based exit using the zero-cross cycle. Bypasses CWVAP guard entirely.

```
exit_slope_bottom(row, prev_row, trade, ...)
  |-- Phase 1: wait for cts_slope to cross above zero
  |     (99.2% of entries cross zero within 30 bars empirically)
  |     Track via slope_went_negative bit (repurposed as slope_crossed_zero)
  |-- Phase 2: slope has been positive, exit when it drops back below zero
  |     --> ExitReason.SLOPE_CYCLE
  +-- None --> HOLD
```

Empirically (NIFTY 500 TEST): 214 trades via SLOPE_CYCLE, 49.5% WR, +2.27% avg.
Sweet spot is 16-20 bar holds: 57% WR, 3.37x payoff.

### Exit: PDD Divergence path (`exits/pdd_divergence.py`)

Tags: `PDD_DIVERGENCE`

Tailored to shallow bases and long trailing runners. Bypasses standard CWVAP guard.

```
exit_pdd_divergence(row, prev_row, trade, ...)
  |-- EXIT: Stop Loss Hit (-3.0% hard stop from entry)
  |-- STRUCTURAL: Slope Cycle Complete (slope > 0 drops to < 0)
  |     --> SUPPRESS if close >= CWVAP * 0.99
  |-- EXIT: Lost CWVAP Support (reclaimed CWVAP, but now close < CWVAP * 0.99)
  |-- EXIT: Deep MFE Trailing Stop (MFE > 15%, exit if drops 15% from peak)
  +-- None --> HOLD
```

### Exit: PSZ Glide (`exits/psz_glide.py`)

Shared helper.  Fires when 5-bar mean of PSZ drops below `psz_glide_threshold`
(0.30) after the mean was above it.  Falls back to 1-bar crossover if
insufficient history.

### CWVAP Guard (`exits/cwvap_guard.py`)

Applied **after** all exit paths.  Never generates standalone exits — only
suppresses or releases exits proposed by indicator logic.

```
apply_cwvap_guard(row, trade, res, state, cwvap_values, cfg, records, idx)
  |
  |-- close > CWVAP?
  |     |-- Rule A: PSZ > 0 OR CTS > 0 → SUPPRESS (hold while momentum strong)
  |     +-- Rule B: momentum faded + (exit_suppressed or res) → RELEASE
  |
  |-- close <= CWVAP AND exit_suppressed?
  |     |-- Within tolerance (dist% >= -tolerance_pct)?
  |     |     |-- bars_below > tolerance_bars → RELEASE (time stop)
  |     |     +-- else → SUPPRESS (give chance)
  |     +-- Below tolerance → RELEASE (SUPPRESSED_EXIT)
  |
  +-- else → pass through
```

---

## Config Reference

### Entry (`SavgolCTSEntryConfig` — `config.py`)

**Shared parameters:**

| Parameter | Default | Used by |
|---|---|---|
| `cts_floor` | -1.0 | Floor Touch, Floor Leave, BT-Cross |
| `floor_zone_tolerance` | 0.02 | Floor Touch, Floor Leave |
| `psz_min_threshold` | -0.25 | Floor Leave, BT-Cross |
| `cwvap_max_dist` | -5.0 | Floor Touch |
| `dvwap_bear_stack_gate_enabled` | True | Floor Touch, BT-Cross |
| `cooldown_enabled` | True | All paths |
| `cooldown_bars` | 10 | All paths |
| `cooldown_exit_reasons` | SUPPRESSED_EXIT, FLOOR_HIT, BT_HIT, BAR3_STOP | All paths |

**Path 0 — Floor Touch (`cfg.floor_touch`):**

| Parameter | Default | Description |
|---|---|---|
| `enabled` | False | Enable/disable path |
| `psz_max` | -0.25 | PSZ must be below this |

**Path 1 — Floor Leave (`cfg.floor_leave`):**

| Parameter | Default | Description |
|---|---|---|
| `enabled` | False | Enable/disable path |
| `cts_max` | -0.6 | Vertical jump ceiling |
| `pszv_min` | 0.05 | Conviction gate: minimum |PSZV| |
| `cwvap_trap_hi` | -5.0 | Signal-day trap floor (% below CWVAP) |

**Path 2 — BT-Cross (`cfg.bt_cross`):**

| Parameter | Default | Description |
|---|---|---|
| `enabled` | False | Enable/disable path |
| `oversold_threshold` | -0.50 | CTS must be below this |
| `flat_gate_enabled` | True | Reject when psz_v flat for N bars |
| `flat_gate_threshold` | 0.02 | |psz_v| below this = flat |
| `flat_gate_lookback` | 3 | N consecutive flat bars to trigger gate |

**Path 3 — CWVAP Reclaim (`cfg.cwvap_reclaim`):**

| Parameter | Default | Description |
|---|---|---|
| `enabled` | True | Enable/disable path |
| `psz_min` | 0.0 | Minimum PSZ at entry |
| `cts_min` | 0.0 | Minimum CTS at entry (reject negative CTS) |
| `cts_max` | 0.85 | Maximum CTS at entry (reject near-ceiling) |
| `cwvap_dist_max` | 7.0 | Max cwvap_dist% (reject extended entries) |
| `accel_margin_min` | 0.01 | Minimum accel above threshold |
| `st_guard_enabled` | True | Reject when CTS near/above sell threshold |
| `st_guard_tolerance` | -0.10 | Block if CTS >= ST - tolerance (negative = stricter) |

Plus hardcoded guards:
- `cts_accel >= cts_accel_threshold` (dynamic threshold from data)
- `close <= va_high` (reject when above value area)

**Path 4 — CWVAP Cross (`cfg.cwvap_cross`):**

| Parameter | Default | Description |
|---|---|---|
| `enabled` | False | Enable/disable path |
| `psz_min` | -0.35 | Minimum PSZ at entry |
| `cts_min` | 0.0 | Minimum CTS at entry |
| `cts_max` | 0.85 | Maximum CTS at entry (reject near-ceiling) |
| `accel_margin_min` | 0.01 | Minimum accel above threshold |
| `slope_min` | 0.0 | Reject when cts_slope too negative |
| `st_guard_enabled` | True | Reject when CTS near/above sell threshold |
| `st_guard_tolerance` | -0.10 | Block if CTS >= ST - tolerance (negative = stricter) |

**Path 5 — Slope Bottom (`cfg.slope_bottom`):**

| Parameter | Default | Description |
|---|---|---|
| `enabled` | True | Enable/disable path |
| `slope_threshold` | -0.10 | cts_slope must be at or below |
| `slope_delta_min` | 0.002 | Min slope change (reject noise) |
| `slope_delta_max` | 0.02 | Max slope change per bar (reject violent dead-cat bounces) |
| `cwvap_dist_min` | -3.0 | Min cwvap_dist% (reject when too far below CWVAP) |
| `cwvap_dist_max` | 0.5 | Max cwvap_dist% |
| `slope_exhaustion_min` | -0.22 | Floor for signal-day slope (avoid infinite falls) |
| `open_cwvap_guard` | True | Reject gap ups above CWVAP |
| `accel_rising_guard` | True | Reject dropping acceleration |
| `cts_max` | -0.85 | Max CTS (require deep exhaustion) |
| `pdd_guard` | True | Toggle PDD institutional exhaustion guard |
| `pdd_max` | 0.0 | Reject when pdd_120 > max (institutions still distributing) |

**Path 6 — PDD Divergence (`cfg.pdd_divergence`):**

| Parameter | Default | Description |
|---|---|---|
| `enabled` | True | Enable/disable path |
| `pdd_threshold` | -2.0 | Max pdd_120 allowed (requires deep institutional exhaustion) |
| `slope_threshold_min` | -0.10 | Lower bound of shallow slope range |
| `slope_threshold_max` | -0.04 | Upper bound of shallow slope range |
| `slope_delta_min` | 0.002 | Conviction gate (reject noise) |
| `slope_delta_max` | 0.025 | Reject violent spikes |
| `cts_max` | -0.70 | Require moderate exhaustion |
| `cwvap_dist_min` | -4.5 | Must not be too far below CWVAP (%) |
| `cwvap_dist_max` | 0.5 | Must not be too far above CWVAP (%) |

### Exit (`SavgolCTSExitConfig` — `config.py`)

**Shared parameters:**

| Parameter | Default | Description |
|---|---|---|
| `ceiling_leave_tolerance` | 0.02 | Tolerance for ceiling-leave exit |
| `floor_hit_min_bars` | 5 | Grace period before floor exit fires |
| `bt_hit_min_bars` | 5 | Grace period before BT exit fires |
| `psz_glide_threshold` | 0.30 | PSZ level for glide exit |
| `st_exit_enabled` | False | Universal Sell Threshold toggle |
| `st_crossover_tolerance` | 0.03 | ST crossover detection tolerance |

**BT-Cross exit (`cfg.bt_cross`):**

| Parameter | Default | Description |
|---|---|---|
| `floor_tolerance` | 0.10 | Floor zone protection width |
| `psz_stall_enabled` | False | Early exit when PSZ stalls |
| `psz_stall_check_bar` | 2 | Bar to check for stall |
| `bar3_stop_enabled` | True | Bar-3 PnL stop |
| `bar3_stop_bar` | 3 | Bar at which to check |
| `bar3_stop_threshold` | -1.0 | Exit if PnL% below this |

**CWVAP Reclaim exit (`cfg.cwvap_reclaim`):**

| Parameter | Default | Description |
|---|---|---|
| `cwvap_lost_atr_mult` | 0.3 | ATR multiplier for CWVAP-lost tolerance |
| `bar3_stop_enabled` | False | Bar-3 PnL stop |
| `bar3_stop_bar` | 3 | Bar at which to check |
| `bar3_stop_threshold` | -2.0 | Exit if PnL% below this |
| `bar5_stop_enabled` | False | Bar-5 breakeven gate |
| `bar5_stop_bar` | 5 | Bar at which to check |
| `bar5_stop_threshold` | 0.0 | Exit if PnL% below this |
| `pnl_cap_enabled` | True | Take-profit PnL cap |
| `pnl_cap_pct` | 8.0 | Exit when PnL% >= this |
| `lh_ll_enabled` | False | LH+LL price structure trend break exit |
| `lh_ll_pivot_lookback` | 2 | Bars on each side to confirm a swing pivot |

**Slope Bottom exit (`cfg.slope_bottom`):**

| Parameter | Default | Description |
|---|---|---|
| `pnl_cap_enabled` | True | Take profit at PnL cap |
| `pnl_cap_pct` | 8.0 | Exit when PnL% >= this |
| `trail_enabled` | False | MFE-based trailing stop (disabled) |
| `trail_activation_pct` | 3.0 | Activate trail once running MFE >= 3% |
| `trail_lock_ratio` | 0.50 | Lock 50% of peak PnL as floor |

**PDD Divergence exit (`cfg.pdd_divergence`):**

| Parameter | Default | Description |
|---|---|---|
| `hard_stop_pct` | -3.0 | Percentage below entry to trigger Stop Loss |
| `cwvap_suppress_tolerance` | 0.99 | Suppress slope exit when close >= CWVAP * 0.99 |
| `cwvap_lost_tolerance` | 0.99 | Exit when close falls to CWVAP * 0.99 after reclaiming |
| `mfe_trail_activation_pct` | 15.0 | Peak MFE threshold required to activate trailing stop |
| `mfe_trail_lock_ratio` | 0.85 | Close exit triggered when price drops below 85% of peak |

**CWVAP Guard (`cfg.cwvap_guard`):**

| Parameter | Default | Description |
|---|---|---|
| `tolerance_pct` | 0.50 | Allowable % dip below CWVAP |
| `tolerance_bars` | 1 | Max bars below CWVAP within tolerance |

---

## EOD-Lag Execution Model

The `tag_signals()` method in `base.py` implements EOD-lag:
- Entry signal fires on bar `i`
- Trade opens on bar `i+1` (pending_entry mechanism)
- Exit checks begin from bar `i+2` onward
