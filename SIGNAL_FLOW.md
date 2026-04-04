# SavgolCTS Signal — Entry / Exit Flow

**Package**: `src/trading/signals/savgol_cts/`
**Last updated**: 2026-04-04

## Package Structure

```
savgol_cts/
  __init__.py          # re-exports SavgolCTSEntryConfig, SavgolCTSExitConfig, SavgolCTSSignal
  config.py            # per-path entry/exit config dataclasses + composites
  state.py             # SavgolCTSExitState bitfield helper
  scoring.py           # compute_intensity() — shared intensity scoring
  signal.py            # SavgolCTSSignal orchestrator (cooldown, ST exit, dispatch)
  entries/
    slope_bottom.py    # Path 1: cts_slope rising from P5 bottom in downtrend
    institutional_floor.py # Path 3: Sustained PSZ recovery + Inst alignment
  exits/
    slope_bottom.py    # Slope Bottom exit: pure slope zero-cross cycle
    institutional_floor.py # Inst-Floor exit: CTS Trail Cap
    cwvap_guard.py     # CWVAP suppression / release logic (post-exit)
```

---

## Entry Flow

Two entry paths evaluated in priority order. First match wins.

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
  |-- PATH 1: Slope Bottom                               [entries/slope_bottom.py]
  |     |-- [Gate]  cfg.slope_bottom.enabled?
  |     |-- [Guard] cts_slope <= slope_threshold (-0.10, P5 empirical bottom)
  |     |-- [Guard] cts_slope rising (slope > prev_slope)
  |     |-- [Guard] regime in ["downtrend", "notrend"]
  |     |-- [Guard] slope_delta <= slope_delta_max (0.02, reject dead-cat bounces)
  |     |-- [Guard] cwvap_dist% in range [-3.0, 0.5]
  |     +-- PASS --> EntryTag.SLOPE_BOTTOM
  |
  |-- PATH 3: Institutional Floor                        [entries/institutional_floor.py]
  |     |-- [Gate]  cfg.institutional_floor.enabled?
  |     |-- [Guard] PSZ Sustained: psz <= -0.30 for 3 bars
  |     |-- [Guard] PSZ Inflection: psz >= -0.29 AND prev_psz <= -0.30
  |     |-- [Guard] Displacement: close < CWVAP
  |     |-- [Guard] Acceleration: psz_v strictly increasing 3-bar (delta 0.01)
  |     |-- [Guard] Inst Dislocation: cts <= cts_buy_threshold
  |     |-- [Guard] Inst Improvement: cts_slope > prev_cts_slope
  |     |-- [Guard] Accel Rising: cts_accel > prev_cts_accel (avoid fading pops)
  |     |-- [Guard] Contrarian: cts_slope < 0
  |     |-- [Guard] Alignment: cwc_slope > 0
  |     |-- [Score] Conviction >= 5 (multi-factor soft gate)
  |     +-- PASS --> EntryTag.INSTITUTIONAL_FLOOR
  |
  +-- No path matched --> REJECT (last meta from final path)
```

### Intensity Scoring (shared — `scoring.py`)

```
Base:       20
Regime:     +10 downtrend, +5 notrend, +0 uptrend

Path-Specific Scoring (Max 70 points):
- Slope Bottom (Mean Reversion):
  - Exhaustion Depth (CTS): -0.85 to -1.0 (+0..20)
  - Structural Dislocation (cwvap_dist): Deeper is better (+0..20)
  - Inflection Sharpness (slope_delta): Higher is better (+0..20)
  - PDD_120: Closer to zero is better (+0..10)

- Institutional Floor (PSZ Recovery):
  - PSZ Depth: -0.30 to -0.60 (+0..25)
  - Institutional Alignment (CWC Slope): 0.0 to 0.05 (+0..25)
  - Structural Dislocation (CWVAP Dist): Deeper is better (+0..20)

Range:      0 .. 100  (clamped)
Labels:     >= 80 "STRONG", >= 65 "good", else unlabelled
```

---

## Exit Flow

Exit is branched by entry tag. `check_exit` (in `signal.py`) dispatches to
the path-specific exit, then applies the CWVAP guard.

```
check_exit(row, prev_row, trade, ...)                [signal.py]
  |
  |-- tag == SLOPE_BOTTOM
  |     --> exit_slope_bottom()                      [exits/slope_bottom.py]
  |
  |-- tag == INSTITUTIONAL_FLOOR
  |     --> exit_institutional_floor()               [exits/institutional_floor.py]
  |
  |-- [Optional] ST exit (cfg.st_exit_enabled, default False)
  |
  +-- apply_cwvap_guard()                            [exits/cwvap_guard.py]
      (skipped for hard exits, SLOPE_BOTTOM, and INSTITUTIONAL_FLOOR)
```

### State Bitfield (`delivery_bad_count` repurposed — `state.py`)

```
bit 1 (& 0x02): psz_was_above      — (Inst-Floor: repurposed as trailing_cts — Phase 2 active)
bit 3 (& 0x08): exit_suppressed   — An indicator exit was suppressed by CWVAP Rule A
bit 4 (& 0x10): suppressed_this_bar — Exit was suppressed on this specific bar
bit 5 (& 0x20): slope_crossed_zero — Phase 1 complete: cts_slope > 0 reached
bit 6 (& 0x40): price_above_cwvap — Phase 1 complete: price reclaimed CWVAP
                 (Inst-Floor: repurposed as psz_was_positive — PSZ crossed above zero)
```

---

### Exit: Slope Bottom path (`exits/slope_bottom.py`)

Tags: `SLOPE_BOTTOM`

Pure slope-based exit using the zero-cross cycle. Bypasses CWVAP guard for PnL Cap.

```
exit_slope_bottom(row, prev_row, trade, ...)
  |-- Phase 1: wait for cts_slope to cross above zero (bit 5)
  |-- Phase 2: slope has been positive, exit when it drops back below zero
  |     --> ExitReason.SLOPE_CYCLE
  |-- [Override] PnL Cap: exit if PnL% >= 8.0%
  +-- None --> HOLD
```

### Exit: Institutional Floor path (`exits/institutional_floor.py`)

Tags: `INSTITUTIONAL_FLOOR`

CTS Trail Cap — two-phase exit combining PSZ momentum cycle with CTS
institutional recovery tracking.

```
exit_institutional_floor(row, prev_row, trade, ...)
  |-- [Guard] Hard Stop: exit if PnL% <= -8.0%
  |-- [Override] PnL Cap: exit if PnL% >= 8.0% (active in both phases)
  |
  |-- Phase 2 active? (bit 1: psz_was_above repurposed as trailing_cts)
  |     YES --> CTS trail: exit when CTS >= cts_sell_threshold
  |             --> ExitReason.ST_CROSS
  |
  |-- Phase 1: PSZ zero-cross cycle (bit 6: price_above_cwvap repurposed as psz_was_positive)
  |     |-- wait for PSZ to cross above zero (psz_was_positive = True)
  |     +-- PSZ drops back below zero:
  |           |-- CTS < cts_sell_threshold? → switch to Phase 2 (CTS has room)
  |           +-- CTS >= cts_sell_threshold? → exit now
  |                 --> ExitReason.PSZ_GLIDE
  +-- None --> HOLD
```


---

### CWVAP Guard (`exits/cwvap_guard.py`)

Applied **after** all exit paths. Never generates standalone exits — only
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

**Path 1 — Slope Bottom (`cfg.slope_bottom`):**

| Parameter | Default | Description |
|---|---|---|
| `enabled` | True | Enable/disable path |
| `slope_threshold` | -0.10 | cts_slope must be at or below |
| `slope_delta_min` | 0.002 | Min slope change (reject noise) |
| `cts_max` | -0.85 | Max CTS (require deep exhaustion) |

**Path 3 — Institutional Floor (`cfg.institutional_floor`):**

| Parameter | Default | Description |
|---|---|---|
| `enabled` | True | Enable/disable path |
| `psz_threshold` | -0.30 | Base PSZ exhaustion floor |
| `psz_delta` | 0.01 | Min recovery for signal bar |
| `psz_lookback` | 3 | Bars PSZ must be <= threshold |
| `psz_v_lookback`| 3 | Bars psz_v must be rising |
| `psz_v_delta` | 0.01 | Min velocity acceleration |
| `cwvap_dist_max`| 0.0 | Must be below CWVAP |
| `accel_rising_guard`| True | Reject if cts_accel is dropping |

### Exit (`SavgolCTSExitConfig` — `config.py`)

**Institutional Floor exit — CTS Trail Cap (`cfg.institutional_floor`):**

| Parameter | Default | Description |
|---|---|---|
| `pnl_cap_pct` | 8.0 | Take profit cap (both phases) |
| `hard_stop_pct` | 8.0 | Max acceptable loss |
| `psz_peak_threshold`| 0.25 | Phase 1: PSZ must reach this before exit check |
| `psz_exit_threshold`| 0.0 | Phase 1: PSZ drops below this → check CTS for Phase 2 |
| *(dynamic)* `cts_sell_threshold` | — | Phase 2: exit when CTS >= this (from ledger) |

---

## EOD-Lag Execution Model

The `tag_signals()` method in `base.py` implements EOD-lag:
- Entry signal fires on bar `i`
- Trade opens on bar `i+1` (pending_entry mechanism)
- Exit checks begin from bar `i+2` onward
