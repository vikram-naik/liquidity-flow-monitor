# SavgolCTS Signal — Entry / Exit Flow

**Package**: `src/trading/signals/savgol_cts/`
**Last updated**: 2026-04-02

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
    structural_divergence.py # Path 2: Volume exhaustion + delivery divergence
  exits/
    slope_bottom.py    # Slope Bottom exit: pure slope zero-cross cycle
    structural_divergence.py # Struct-Div exit: Smart path (Time-Decay, PnL-Cap, Hard-Stop)
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
  |-- PATH 2: Structural Divergence                      [entries/structural_divergence.py]
  |     |-- [Gate]  cfg.structural_divergence.enabled?
  |     |-- [Guard] Exhaustion: price_slope_z <= -0.20 AND cts <= -0.50
  |     |-- [Guard] Divergence: Spread (rsz-psz) >= 0.35 OR accum_div > 0.04
  |     |-- [Guard] Inflection: cts_slope < 0 AND cts_accel > 0.0 (flattening)
  |     |-- [Guard] Anti-Capitulation: cwc <= 0.50 (reject unified dumping)
  |     |-- [Guard] Context: cwvap_dist% <= 0.5%
  |     +-- PASS --> EntryTag.STRUCTURAL_DIVERGENCE
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

- Structural Divergence (Volume Exhaustion):
  - Divergence Intensity (Spread): 0.35 to 0.60 (+0..25)
  - Accumulation Spike (accum_div): 0.04 to 0.08 (+0..25)
  - Exhaustion Depth (CTS): -0.50 to -1.0 (+0..20)

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
  |-- tag == STRUCTURAL_DIVERGENCE
  |     --> exit_structural_divergence()             [exits/structural_divergence.py]
  |
  |-- [Optional] ST exit (cfg.st_exit_enabled, default False)
  |
  +-- apply_cwvap_guard()                            [exits/cwvap_guard.py]
      (skipped for hard exits like PNL_CAP or HARD_STOP)
```

### State Bitfield (`delivery_bad_count` repurposed — `state.py`)

```
bit 3 (& 0x08): exit_suppressed   — An indicator exit was suppressed by CWVAP Rule A
bit 4 (& 0x10): suppressed_this_bar — Exit was suppressed on this specific bar
bit 5 (& 0x20): slope_crossed_zero — Phase 1 complete: cts_slope > 0 reached
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

### Exit: Structural Divergence path (`exits/structural_divergence.py`)

Tags: `STRUCTURAL_DIVERGENCE`

Smart exit path for counter-trend structural plays.

```
exit_structural_divergence(row, prev_row, trade, ...)
  |-- [Guard] Hard Stop: exit if PnL% <= -5.0%
  |-- [Take Profit] PnL Cap: exit if PnL% >= 6.0%
  |-- [Time Decay] Sideways Guard: exit if bars >= 8 AND PnL < 1%
  |-- [Cycle] Slope Cycle: Phase 1 (slope > 0) -> Phase 2 (slope < 0)
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

**Path 2 — Structural Divergence (`cfg.structural_divergence`):**

| Parameter | Default | Description |
|---|---|---|
| `enabled` | True | Enable/disable path |
| `psz_max` | -0.20 | Max price_slope_z |
| `cts_max` | -0.50 | Max CTS depth |
| `spread_min` | 0.35 | Min spread (rdv_slope_z - psz) |
| `accum_div_min` | 0.04 | Min accumulation divergence spike |
| `accel_min` | 0.0 | Min cts_accel (requires positive flattening) |
| `cwc_max` | 0.50 | Max Cross-Window Coherence (anti-capitulation) |

### Exit (`SavgolCTSExitConfig` — `config.py`)

**Structural Divergence exit (`cfg.structural_divergence`):**

| Parameter | Default | Description |
|---|---|---|
| `pnl_cap_pct` | 6.0 | Take profit cap |
| `time_decay_bars` | 8 | Exit if trade stalls |
| `time_decay_min_pnl`| 1.0 | Required PnL to hold past time decay |
| `hard_stop_pct` | 5.0 | Max acceptable loss |

---

## EOD-Lag Execution Model

The `tag_signals()` method in `base.py` implements EOD-lag:
- Entry signal fires on bar `i`
- Trade opens on bar `i+1` (pending_entry mechanism)
- Exit checks begin from bar `i+2` onward
