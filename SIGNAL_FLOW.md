# SavgolCTS Signal — Entry / Exit Flow

**Package**: `src/trading/signals/savgol_cts/`
**Last updated**: 2026-04-05

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
    accel_cross.py     # Path 2: Triple-trend momentum cross with inst alignment
    institutional_floor.py # Path 3: Sustained PSZ recovery + Inst alignment
  exits/
    slope_bottom.py    # Slope Bottom exit: pure slope zero-cross cycle
    accel_cross.py     # Accel Cross exit: Two-Phase PSZ/CTS Glide (mirrors IF)
    institutional_floor.py # Inst-Floor exit: Two-Phase PSZ/CTS Glide
    cwvap_guard.py     # CWVAP suppression / release logic (post-exit)
```

---

## Entry Flow

Three entry paths evaluated in priority order. First match wins.

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
  |-- PATH 2: Accel Cross                                [entries/accel_cross.py]
  |     |-- [Gate]  cfg.accel_cross.enabled?
  |     |-- [Guard] Slope Inflection: cts_slope crosses above zero
  |     |-- [Guard] Accel Conviction: cts_accel rising AND > adaptive threshold
  |     |-- [Guard] Price Pivot: PSZ in (0.0, 0.2]
  |     |-- [Guard] Inst Guard: CTS in (0.0, 0.5]
  |     |-- [Guard] Inst Alignment: cwc_slope > 0
  |     |-- [Guard] Distance Guard: cwvap_dist% <= 8.0%
  |     |-- [Score] Study Score 20-29 (BOOM/TREND production gates)
  |     +-- PASS --> EntryTag.ACCEL
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

---

## Exit Flow

Exit is branched by entry tag. `check_exit` (in `signal.py`) dispatches to
the path-specific exit, then applies the CWVAP guard.

```
check_exit(row, prev_row, trade, ...)                [signal.py]
  |
  |-- tag == ACCEL
  |     --> check_exit_accel_cross()                 [exits/accel_cross.py]
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
      (skipped for hard exits, ACCEL, and INSTITUTIONAL_FLOOR)
```

### State Bitfield (`delivery_bad_count` repurposed — `state.py`)

```
bit 1 (& 0x02): psz_was_above      — (IF/ACCEL: repurposed as trailing_cts — Phase 2 active)
bit 3 (& 0x08): exit_suppressed   — An indicator exit was suppressed by CWVAP Rule A
bit 4 (& 0x10): suppressed_this_bar — Exit was suppressed on this specific bar
bit 5 (& 0x20): slope_crossed_zero — Phase 1 complete: cts_slope > 0 reached
bit 6 (& 0x40): price_above_cwvap — (IF/ACCEL: repurposed as psz_was_positive — PSZ crossed above zero)
```

---

### Exit: Accel Cross & Institutional Floor paths

Tags: `ACCEL`, `INSTITUTIONAL_FLOOR`

Two-Phase Glide — momentum cycling exit strategy.

```
check_exit_accel_cross / exit_institutional_floor
  |-- [Guard] Hard Stop: exit if PnL% <= -8.0%
  |-- [Override] PnL Cap: exit if PnL% >= 8.0% (active in both phases)
  |
  |-- Phase 2 active? (bit 1: psz_was_above)
  |     YES --> CTS trail: exit when CTS >= cts_sell_threshold
  |             --> ExitReason.ST_CROSS
  |
  |-- Phase 1: PSZ zero-cross cycle (bit 6: price_above_cwvap)
  |     |-- wait for PSZ to cross above exit_threshold (0.0)
  |     +-- PSZ drops back below exit_threshold:
  |           |-- CTS < cts_sell_threshold? → switch to Phase 2 (Institutional Floor holds)
  |           +-- CTS >= cts_sell_threshold? → exit now
  |                 --> ExitReason.PSZ_GLIDE
  +-- None --> HOLD
```

---

### CWVAP Guard (`exits/cwvap_guard.py`)

Applied **after** all exit paths. Never generates standalone exits — only
suppresses or releases exits proposed by indicator logic.

```
apply_cwvap_guard(row, trade, res, state, cfg, records, idx)
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

**Path 2 — Accel Cross (`cfg.accel_cross`):**

| Parameter | Default | Description |
|---|---|---|
| `enabled` | True | Enable/disable path |
| `cts_min` | 0.0 | Institutional floor lower bound |
| `score_min` | 20 | Minimum study score (BOOM/TREND) |
| `cwvap_dist_max`| 8.0 | Max distance from CWVAP (momentum cap) |

**Path 1 — Slope Bottom (`cfg.slope_bottom`):**

| Parameter | Default | Description |
|---|---|---|
| `enabled` | True | Enable/disable path |
| `slope_threshold` | -0.10 | cts_slope must be at or below |
| `cts_max` | -0.85 | Max CTS (require deep exhaustion) |

### Exit (`SavgolCTSExitConfig` — `config.py`)

**Accel Cross & Inst Floor (`cfg.accel_cross`, `cfg.institutional_floor`):**

| Parameter | Default | Description |
|---|---|---|
| `pnl_cap_pct` | 8.0 | Take profit cap (both phases) |
| `hard_stop_pct` | 8.0 | Max acceptable loss |
| `psz_peak_threshold`| 0.25 | PSZ must reach this to start cycle |
| `psz_exit_threshold`| 0.0 | PSZ drops below this → transition or exit |
