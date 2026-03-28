# SavgolCTS Signal — Entry / Exit Flow

**Package**: `src/trading/signals/savgol_cts/`
**Last updated**: 2026-03-28

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
  exits/
    floor.py           # Floor/Floor-Leave/Floor-Touch exit path
    bt_cross.py        # BT-Cross exit path + PSZ stall helper
    psz_glide.py       # Shared PSZ glide exit (5-bar mean crossover)
    cwvap_guard.py     # CWVAP suppression / release logic (post-exit)
```

---

## Entry Flow

Three entry paths evaluated in priority order.  First match wins.

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
  +-- No path matched --> REJECT (last meta from final path)
```

### Intensity Scoring (shared — `scoring.py`)

```
Base:       60
Coherence:  +0..15  (lower coh = more divergence = better)
PDD_120:    +0..15  (closer to zero = less exhaustion = better)
Regime:     +10 downtrend, +5 notrend, +0 uptrend
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
  |-- [Optional] ST exit (cfg.st_exit_enabled, default False)
  |
  +-- apply_cwvap_guard()                            [exits/cwvap_guard.py]
```

### State Bitfield (`delivery_bad_count` repurposed — `state.py`)

```
bit 0 (& 0x01): cts_rose          — CTS has risen above -1.0 during this trade
bit 1 (& 0x02): psz_was_above     — PSZ crossed above glide threshold
bit 2 (& 0x04): cts_above_bt      — CTS has been above BT during this trade
bit 3 (& 0x08): exit_suppressed   — An indicator exit was suppressed by CWVAP Rule A
bit 4 (& 0x10): suppressed_this_bar — Exit was suppressed on this specific bar
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
  |-- [Gate] cts_rose? (must have risen before any exit fires)
  |-- EXIT: Floor hit — cts <= -1.0 AND bars_held >= floor_hit_min_bars
  |-- SUPPRESS: Floor zone — cts and bt both within floor_tolerance
  |-- EXIT: Ceiling-leave
  |-- EXIT: PSZ glide (psz_was_above, via exit_psz_glide)
  |-- EXIT: BT hit — bt above floor_zone AND cts <= bt
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
| `cooldown_exit_reasons` | SUPPRESSED_EXIT, FLOOR_HIT, BT_HIT | All paths |

**Path 0 — Floor Touch (`cfg.floor_touch`):**

| Parameter | Default | Description |
|---|---|---|
| `enabled` | True | Enable/disable path |
| `psz_max` | -0.25 | PSZ must be below this |

**Path 1 — Floor Leave (`cfg.floor_leave`):**

| Parameter | Default | Description |
|---|---|---|
| `cts_max` | -0.6 | Vertical jump ceiling |
| `pszv_min` | 0.05 | Conviction gate: minimum |PSZV| |
| `cwvap_trap_hi` | -5.0 | Signal-day trap floor (% below CWVAP) |

**Path 2 — BT-Cross (`cfg.bt_cross`):**

| Parameter | Default | Description |
|---|---|---|
| `enabled` | True | Enable/disable path |
| `oversold_threshold` | -0.50 | CTS must be below this |
| `flat_gate_enabled` | True | Reject when psz_v flat for N bars |
| `flat_gate_threshold` | 0.02 | |psz_v| below this = flat |
| `flat_gate_lookback` | 3 | N consecutive flat bars to trigger gate |

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

**CWVAP Guard (`cfg.cwvap_guard`):**

| Parameter | Default | Description |
|---|---|---|
| `tolerance_pct` | 1.00 | Allowable % dip below CWVAP |
| `tolerance_bars` | 3 | Max bars below CWVAP within tolerance |

---

## EOD-Lag Execution Model

The `tag_signals()` method in `base.py` implements EOD-lag:
- Entry signal fires on bar `i`
- Trade opens on bar `i+1` (pending_entry mechanism)
- Exit checks begin from bar `i+2` onward
