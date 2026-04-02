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
    slope_bottom.py    # Path 1: cts_slope rising from P5 bottom in downtrend
  exits/
    slope_bottom.py    # Slope Bottom exit: pure slope zero-cross cycle
    cwvap_guard.py     # CWVAP suppression / release logic (post-exit)
```

---

## Entry Flow

One entry path evaluated.  First match wins.

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
  |     |-- [Guard] regime == "downtrend"
  |     |-- [Guard] slope_delta <= slope_delta_max (0.02, reject dead-cat bounces)
  |     |-- [Guard] cwvap_dist% >= cwvap_dist_min (-3.0%, not too far below)
  |     |-- [Guard] cwvap_dist% <= cwvap_dist_max (0.5%, must be below CWVAP)
  |     +-- PASS --> EntryTag.SLOPE_BOTTOM
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
  |-- tag == SLOPE_BOTTOM
  |     --> exit_slope_bottom()                      [exits/slope_bottom.py]
  |     (bypasses CWVAP guard — exit is slope-based)
  |
  |-- [Optional] ST exit (cfg.st_exit_enabled, default False)
  |
  +-- apply_cwvap_guard()  (skipped for SLOPE_BOTTOM)  [exits/cwvap_guard.py]
```

### State Bitfield (`delivery_bad_count` repurposed — `state.py`)

```
bit 3 (& 0x08): exit_suppressed   — An indicator exit was suppressed by CWVAP Rule A
bit 4 (& 0x10): suppressed_this_bar — Exit was suppressed on this specific bar
bit 5 (& 0x20): slope_crossed_zero — Slope Bottom: slope > 0 reached (Phase 1 complete)
```

---

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

Empirically (NIFTY 50 TEST 2024-2026): 32 trades, 71.9% WR, +4.45% avg, 3.51x payoff.
The strategy relies heavily on the 8% PnL cap to lock in mean-reversion profits before the cycle formally turns over.

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
| `cooldown_enabled` | True | All paths |
| `cooldown_bars` | 10 | All paths |
| `cooldown_exit_reasons` | SUPPRESSED_EXIT, BAR3_STOP, BAR5_STOP | All paths |

**Path 1 — Slope Bottom (`cfg.slope_bottom`):**

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
| `pure_bear_guard` | True | Reject entries on pure red, lower-close days (falling knife guard) |
| `shallow_guard_enabled` | True | Reject shallow inflections hugging CWVAP |
| `shallow_slope_min` | -0.12 | Threshold for 'shallow' slope inflection |
| `shallow_dist_max` | -1.0 | Requires price to be deeply exhausted if inflection is shallow |

### Exit (`SavgolCTSExitConfig` — `config.py`)

**Shared parameters:**

| Parameter | Default | Description |
|---|---|---|
| `st_exit_enabled` | False | Universal Sell Threshold toggle |
| `st_crossover_tolerance` | 0.03 | ST crossover detection tolerance |

**Slope Bottom exit (`cfg.slope_bottom`):**

| Parameter | Default | Description |
|---|---|---|
| `pnl_cap_enabled` | True | Take profit at PnL cap |
| `pnl_cap_pct` | 8.0 | Exit when PnL% >= this |
| `trail_enabled` | False | MFE-based trailing stop (disabled) |
| `trail_activation_pct` | 3.0 | Activate trail once running MFE >= 3% |
| `trail_lock_ratio` | 0.50 | Lock 50% of peak PnL as floor |

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
