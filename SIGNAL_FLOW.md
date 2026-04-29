# SavgolCTS Signal — Entry / Exit Flow

**Package**: `src/trading/signals/savgol_cts/`
**Last updated**: 2026-04-29

## Package Structure

```
savgol_cts/
  __init__.py          # re-exports SavgolCTSEntryConfig, SavgolCTSExitConfig, SavgolCTSSignal
  config.py            # per-path entry/exit config dataclasses + composites
  state.py             # SavgolCTSExitState bitfield helper
  scoring.py           # compute_intensity() — shared intensity scoring
  signal.py            # SavgolCTSSignal orchestrator (cooldown, ST exit, dispatch)
  entries/             # 7 active entry path implementations
  exits/               # 7 active exit path implementations
```

---

## Entry Flow

The `SavgolCTSSignal` orchestrates multiple entry paths, evaluated in priority order. First match wins.

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
  |-- PATH 12: CTS Floor Reversion                       [entries/cts_floor_reversion.py]
  |     |-- [Gate] CTS AND CTS Buy Threshold stuck at floor (<= -0.999) for 5 days.
  |     |-- [Guard] Day 6 must NOT have met the floor condition (only enter exactly on day 5).
  |     |-- [Guard] Momentum must be turning positive (psz_v_0 > psz_v_1).
  |     |-- [Guard] Structural exhaustion: price_slope_z <= -0.15.
  |     |-- [Score] Multi-factor Score >= 9.0 (Rewards deep PRT, violent PSZ_V; penalizes noise/flatness).
  |     +-- PASS --> EntryTag.CTS_FLOOR_REVERSION
  |
  |-- PATH 9: FAS Zero Cross                             [entries/fas_zero_cross.py]
  |     |-- [Gate] prev_fas < 0 AND fas >= 0
  |     |-- [Guard] Gap-Up Guard (true gap pct <= max)
  |     |-- [Guard] CTS Guard (cts <= cts_max)
  |     |-- [Guard] Structural Base (fas 5-bar delta > 0)
  |     |-- [Guard] Angle Gate (fas 3-bar delta > 0)
  |     |-- [Guard] Engine Dynamics (cts_accel not falling; > threshold if not flat)
  |     |-- [Guard] Momentum Guard (psz_v > psz_v_min)
  |     |-- [Guard] Secular Crash Guard (dist_high_252 >= -20%)
  |     |-- [Guard] PDD Guard (pdd >= pdd_min)
  |     |-- [Score] Multi-factor Score >= min_score (15.0)
  |     +-- PASS --> EntryTag.FAS_ZERO_CROSS
  |
  |-- PATH 10: FAS Floor Reversion                       [entries/fas_floor_reversion.py]
  |     |-- [Gate] FAS Deep Floor (fas < fas_max e.g. -1.15)
  |     |-- [Guard] PRT Slope Inflection (prt_slope > prt_slope_min)
  |     |-- [Guard] CTS Extreme Floor (cts <= cts_max e.g. -0.99)
  |     |-- [Guard] CTS Slope Guard (cts_slope < cts_slope_max e.g. 0.0)
  |     +-- PASS --> EntryTag.FAS_FLOOR_REVERSION
  |
  |-- PATH 11: FAS Buy Cross                             [entries/fas_buy_cross.py]
  |     |-- [Gate] FAS crosses above fas_buy_threshold
  |     |-- [Guard] CTS <= cts_max AND cts <= cts_buy_threshold
  |     |-- [Guard] CTS Accel not flat, 3-bar rising, and > threshold
  |     |-- [Guard] No True Gap Up
  |     |-- [Guard] Price slope and RSZ_V bounds
  |     |-- [Score] Multi-factor Score >= min_score (15.0)
  |     +-- PASS --> EntryTag.FAS_BUY_CROSS
  |
  |-- PATH 2: Accel Cross                                [entries/accel_cross.py]
  |     |-- [Gate] Slope Inflection (cts_slope crosses above zero)
  |     |-- [Guard] Accel Momentum (cts_accel rising AND > threshold)
  |     |-- [Guard] Price Pivot (psz in (0.0, 0.2])
  |     |-- [Guard] Inst Guard (cts in (0.0, 0.5])
  |     |-- [Guard] Distance Guard (cwvap_dist% <= 8.0%)
  |     |-- [Guard] Inst Alignment (cwc_slope > 0)
  |     |-- [Score] Multi-factor Score >= score_min (20) AND Boom/Trend constraints
  |     +-- PASS --> EntryTag.ACCEL
  |
  |-- PATH 3: Institutional Floor                        [entries/institutional_floor.py]
  |     |-- [Gate] PSZ Sustained (<= -0.30 for 3 bars) AND Inflection
  |     |-- [Guard] cwvap_dist <= max
  |     |-- [Guard] Acceleration (psz_v strictly increasing 3-bar)
  |     |-- [Guard] Inst Dislocation (cts <= cts_buy_threshold)
  |     |-- [Guard] Inst Improvement (cts_slope accelerating AND cts_accel rising)
  |     |-- [Guard] Contrarian (cts_slope < 0)
  |     |-- [Guard] Alignment (cwc_slope > 0)
  |     |-- [Score] Conviction Score >= min_score (5)
  |     +-- PASS --> EntryTag.INSTITUTIONAL_FLOOR
  |
  |-- PATH 6: Range Reversion                            [entries/range_reversion.py]
  |     |-- [Gate] Annual & Quarterly Oversold (RP_252 < max, RP_63 < max)
  |     |-- [Guard] Short-term inflecting (RP_10 <= prev_RP_10)
  |     |-- [Guard] Green candle
  |     |-- [Guard] Base formed (bars_at_base >= min)
  |     |-- [Guard] ATR-relative range tight
  |     |-- [Guard] Institutional capitulation (cts > max)
  |     |-- [Guard] Momentum velocity improving (psz_v > 0)
  |     +-- PASS --> EntryTag.RANGE_REVERSION
  |
  +-- No path matched --> REJECT (last meta from final path)
```

---

## Exit Flow

Exit checks are dispatched to path-specific modules based on the `trade.entry_tag`.

```
check_exit(row, prev_row, trade, ...)                [signal.py]
  |
  |-- Dispatch to path-specific exit (e.g., exit_cts_floor_reversion, exit_accel_cross, exit_institutional_floor, etc.)
  |
  |-- [Optional] ST exit (cfg.st_exit_enabled)
  |
  +-- apply_cwvap_guard()                            [exits/cwvap_guard.py]
      (Applied after indicator exits, skipped for bespoke path hard exits like BAR3_STOP)
```

### State Bitfield (`SavgolCTSExitState` — `state.py`)

The state is packed into a 18-bit integer, stored in `delivery_bad_count`.

| Bit | Mask   | Name                       | Description |
|---|---|---|---|
| 0 | 0x00001 | `cts_rose`                 | - |
| 1 | 0x00002 | `psz_was_above`            | Phase 2 active tracking (varies by path) |
| 2 | 0x00004 | `cts_above_bt`             | - |
| 3 | 0x00008 | `exit_suppressed`          | Exit suppressed by CWVAP rule |
| 4 | 0x00010 | `suppressed_this_bar`      | Suppressed on current bar |
| 5 | 0x00020 | `slope_crossed_zero`       | (Legacy) Track Phase 1 complete |
| 6 | 0x00040 | `price_above_cwvap`        | Price crossed above CWVAP |
| 7 | 0x00080 | `prt_exit_suppressed`      | (Legacy) PRT exit was suppressed |
| 8 | 0x00100 | `fas_crossed_zero`         | (Legacy) PRT/FAS crossed zero |
| 9 | 0x00200 | `extreme_bottom_extension` | (Legacy) Trade hit absolute floor |
|10 | 0x00400 | `recovery_passed`          | Indicator crossed above zero |
|11 | 0x00800 | `cts_exhausted`            | CTS exhausted post-recovery |
|12 | 0x01000 | `fas_exhausted`            | FAS exhausted post-recovery |
|13-| 0x1E000 | `cwf_count`                | High > CWVAP and Close < CWVAP (4 bits) |
|17 | 0x20000 | `climax_hit_above_va`      | Structural Climax hit while price > VA_High |

## Exit Logic Classes

The exit strategy relies heavily on path-specific logic located in `src/trading/signals/savgol_cts/exits/`. Paths like Accel Cross and Institutional Floor often implement multi-phase glides, PnL caps, trailing stops, and hard stops (usually 8%). Recent paths like FAS Zero Cross employ persistent Dual-Exhaustion Logic.

## CWVAP Guard (`exits/cwvap_guard.py`)

Applied **after** indicator-generated exit signals. It acts as a gatekeeper to either suppress or release exits based on momentum strength relative to CWVAP. It includes:

1. **Candle Rejection Guard**: Preemptively exits on violent inside bars or long upper wicks at resistance.
2. **Structural Climax Guard**: Preemptively exits when price stretches to historical ceilings (`RP_63 > 0.95` AND `RP_252 > 0.95`) while dangerously overextended from VWAP (`CWVAP_Dist% > 10%` OR `FAS > 1.0`). If price is above `VA_High` when climax hits, it suppresses the exit and converts to a strict trailing stop based on the `VA_High` level.
3. **Momentum Suppression**: Suppresses normal cycle exits as long as structural momentum (PSZ > 0 or CTS > 0) is holding above the Custom VWAP.
