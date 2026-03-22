# SavgolCTS Signal — Entry / Exit Flow

**Source**: `src/trading/signals/savgol_cts.py`
**Last updated**: 2026-03-22 (path 1 redesigned: floor-leave replaces floor-touch)

---

## Entry Flow

Three independent entry paths evaluated in priority order. First match wins.

```
check_entry(row, prev_row, cfg, records, idx)
  |
  |-- [Guard] CTS is NaN? --> REJECT "Missing CTS data"
  |
  |-- PATH 1: CTS-floor-leave  (_check_floor_leave)
  |     |
  |     |-- [Guard] CTS or prev_CTS is NaN? --> REJECT
  |     |
  |     |-- CONDITION A: CTS was at floor last bar
  |     |     prev_cts <= cts_floor + floor_touch_tolerance (-0.98)
  |     |     FAIL --> REJECT  (CTS was not pinned at floor)
  |     |
  |     |-- CONDITION B: CTS now above floor
  |     |     cts > cts_floor + floor_touch_tolerance (-0.98)
  |     |     FAIL --> REJECT  (still pinned, or this is the touch bar)
  |     |
  |     +-- ALL conditions met --> PASS
  |           Tag: "CTS-floor-leave"
  |           No BT constraint — BT-cross owns non-floor crossovers
  |           --> ENTRY SIGNAL
  |           (Empirical: NIFTY 500, 63.9% WR, +2.52% avg, 73.1% ceiling exits)
  |
  |-- PATH 2: PSZ Bend  (_check_psz_bend)
  |     |
  |     |-- [Guard] PSZ raw or prev PSZ is NaN? --> REJECT
  |     |
  |     +-- _is_psz_bending(records, idx, cfg)
  |           |
  |           |-- [Gate] psz_bend_enabled=False or no records? --> PASS (skip check)
  |           |
  |           |-- CONDITION A: PSZ raw at signal bar < 0
  |           |     (if PSZ already positive, we're entering late)
  |           |     FAIL --> REJECT
  |           |
  |           |-- CONDITION B: Upward turn at signal bar
  |           |     psz_v >= psz_bend_v_min_at_signal (0.02)
  |           |     AND psz_v > prev bar's psz_v (accelerating)
  |           |     FAIL --> REJECT
  |           |
  |           |-- CONDITION C: Quiescent base in lookback (8 bars before signal)
  |           |     Count bars where:
  |           |       |psz_v| < psz_bend_quiescence_threshold (0.05)
  |           |       AND psz_raw <= psz_cross_threshold (-0.25)
  |           |     Need >= psz_bend_quiescence_min_bars (5)
  |           |     FAIL --> REJECT
  |           |
  |           |-- CONDITION D: No plunge in lookback
  |           |     psz_v never dropped below psz_bend_v_floor (-0.05)
  |           |     FAIL --> REJECT (V-bounce "bump")
  |           |
  |           |-- CONDITION E: Fresh base (mean psz_v gate)
  |           |     mean(psz_v over lookback) < psz_bend_mean_v_max (0.010)
  |           |     FAIL --> REJECT (PSZ already drifting upward — stale move,
  |           |       not a fresh base breakout)
  |           |     (Empirical: NIFTY 50 2024+, mean_v >= 0.010 → WR <50%,
  |           |       avg PnL ~0%; mean_v >= 0.020 → 42% WR, -0.52% PnL)
  |           |
  |           +-- ALL conditions met --> PASS
  |                 |
  |                 +--> Compute intensity (60 base + coh/pdd/regime bonuses)
  |                      Tag: "PSZ"
  |                      --> ENTRY SIGNAL
  |
  |-- PATH 3: BT Crossover  (_check_bt_crossover)
        |
        |-- [Gate] bt_cross_enabled=False? --> REJECT
        |
        |-- [Guard] prev_cts, prev_bt, or bt is NaN? --> REJECT
        |
        |-- CONDITION A: CTS crosses BT from below
        |     prev_cts <= prev_bt AND cts > bt
        |     FAIL --> REJECT "No BT crossover"
        |
        |-- CONDITION B: BT above floor (refined guard)
        |     REJECT only if: bt <= cts_floor (-1.0) AND prev_bt > cts_floor
        |     i.e. BT just dropped to floor this bar (BT newly pinned)
        |     ALLOW if: prev_bt was also at floor (both pinned together —
        |       CTS leaving first is a genuine recovery signal)
        |     (CTS leaving floor while BT was above floor = false positive)
        |
        |-- CONDITION C: Still in oversold territory
        |     cts <= bt_cross_oversold_threshold (-0.50)
        |     FAIL --> REJECT
        |
        |-- CONDITION D: PSZ still negative
        |     psz_raw < 0
        |     FAIL --> REJECT "PSZ already positive, late entry"
        |
        |-- CONDITION E: BT depth gate
        |     bt <= bt_cross_bt_max (-0.71)
        |     FAIL --> REJECT "BT not deep enough"
        |     (Empirical: NIFTY 500, bt > -0.71 → 51% WR, ~0% median PnL)
        |
        +--> Compute intensity (60 base + coh/pdd/regime bonuses)
             Tag: "BT-cross"
             --> ENTRY SIGNAL
```

### Intensity Scoring (shared by all entry paths)

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

Exit is branched by entry tag. `check_exit` dispatches to the appropriate path.

```
check_exit(row, prev_row, trade, peak_close, bars_held, state, ..., cfg, records, idx)
  |
  |-- trade.entry_tag == "CTS-floor-leave"  --> _exit_floor
  |-- trade.entry_tag == "CTS-BT-floor"     --> _exit_floor  (legacy tag)
  |-- trade.entry_tag == "BT-cross"         --> _exit_bt_cross
  |-- otherwise                             --> _exit_psz
```

### State Bitfield (`delivery_bad_count` repurposed)

```
bit 0 (& 0x01): cts_rose         — CTS has risen above -1.0 during this trade
bit 1 (& 0x02): psz_was_above   — PSZ crossed above glide threshold (BT-cross only)
bit 2 (& 0x04): cts_above_bt    — CTS has been above BT during this trade (floor + PSZ paths)
```

---

### Exit Path: CTS-floor-leave entries  (`_exit_floor`)

Tags routed here: `"CTS-floor-leave"`, `"CTS-BT-floor"` (legacy)

```
_exit_floor(row, prev_row, trade, peak_close, bars_held, state, cfg, records, idx)
  |  (handles CTS-floor-leave and legacy CTS-BT-floor)
  |
  |-- [Guard] CTS is NaN? --> HOLD
  |
  |-- Update cts_rose: if cts > -1.0, set bit 0
  |-- Update cts_above_bt: if cts > bt, set bit 2
  |
  |-- PSZ Stall Check  (_is_psz_stalled)
  |
  |-- [Gate] cts_rose=0? --> HOLD
  |
  |-- SUPPRESS: Floor zone  [same as PSZ path]
  |
  |-- SUPPRESS: Deeply negative PSZ  (psz_raw < -0.26)
  |
  |-- EXIT: CTS ceiling hit  (ceiling-leave flavor)
  |     prev_cts >= 0.98 AND cts < 0.98 --> EXIT "CTS ceiling hit"
  |     (tolerance mirrors floor_touch_tolerance = 0.02; fires on first bar
  |      CTS drops below ceiling, not while pinned at it)
  |
  |-- [Sell threshold intentionally omitted]
  |     Entering at the true floor gives CTS the full range to travel.
  |     Sell threshold fires prematurely on recovery — suppressing it
  |     lifts WR +2.9pp and avg PnL +1.06% (NIFTY 500).
  |
  |-- EXIT: CTS hit BT (only if BT above floor zone AND cts_above_bt=1)
  |     --> EXIT "CTS hit BT"
  |
  |-- EXIT: CTS hit floor
  |     cts <= -1.0 --> EXIT "CTS hit -1.0"
  |
  +-- None of the above --> HOLD
```

---

### Exit Path: PSZ entries  (`_exit_psz`)

```
_exit_psz(row, prev_row, trade, peak_close, bars_held, state, cfg, records, idx)
  |
  |-- [Guard] CTS is NaN? --> HOLD
  |
  |-- Update cts_rose: if cts > -1.0, set bit 0
  |-- Update cts_above_bt: if cts > bt, set bit 2
  |
  |-- PSZ Stall Check  (_is_psz_stalled)
  |     [Gate] psz_stall_enabled=False? --> skip
  |     At exactly bar N (psz_stall_check_bar=2) after entry:
  |       if psz_v <= 0 --> EXIT "PSZ stall"
  |     (Empirical: psz_v<=0 at bar 2 = 42% WR, -2.2% PnL on N500)
  |
  |-- [Gate] cts_rose=0? --> HOLD (CTS hasn't left floor yet)
  |
  |-- SUPPRESS: Floor zone
  |     cts <= (-1.0 + floor_tolerance) AND bt <= (-1.0 + floor_tolerance)
  |     Both deeply oversold --> HOLD (room to revive)
  |
  |-- SUPPRESS: Deeply negative PSZ
  |     psz_raw < -0.26 --> HOLD
  |
  |-- EXIT: CTS ceiling hit  (ceiling-leave flavor)
  |     prev_cts >= 0.98 AND cts < 0.98 --> EXIT "CTS ceiling hit"
  |     (tolerance mirrors floor_touch_tolerance = 0.02)
  |
  |-- EXIT: Sell threshold cross-down
  |     prev_cts > (ST - st_crossover_tolerance)
  |     AND cts < 1.0
  |     AND cts < prev_cts (falling)
  |     AND cts < ST
  |     --> EXIT "CTS sell threshold hit"
  |
  |-- EXIT: CTS hit BT (only if BT above floor zone AND cts_above_bt=1)
  |     bt > (-1.0 + floor_tolerance) AND cts <= bt AND cts_above_bt
  |     --> EXIT "CTS hit BT"
  |     (cts_above_bt guard prevents premature exit when PSZ bend entry
  |      occurs with CTS already below BT — exit only arms once CTS has
  |      risen above BT at least once during the trade)
  |
  |-- EXIT: CTS hit floor
  |     cts <= -1.0 --> EXIT "CTS hit -1.0"
  |
  +-- None of the above --> HOLD
```

---

### Exit Path: BT-cross entries  (`_exit_bt_cross`)

```
_exit_bt_cross(row, prev_row, trade, peak_close, bars_held, state, cfg, records, idx)
  |
  |-- [Guard] CTS is NaN? --> HOLD
  |
  |-- Update cts_rose: if cts > -1.0, set bit 0
  |-- Track psz_was_above: if psz_raw >= bt_cross_psz_glide_threshold (0.30), set bit 1
  |
  |-- PSZ Stall Check  (_is_psz_stalled)  [same as PSZ path]
  |
  |-- [Gate] cts_rose=0? --> HOLD
  |
  |-- SUPPRESS: Floor zone  [same as PSZ path]
  |     Both CTS and BT in floor zone --> HOLD
  |
  |-- EXIT: CTS ceiling hit  (ceiling-leave flavor)
  |     prev_cts >= 0.98 AND cts < 0.98 --> EXIT "CTS ceiling hit"
  |     (tolerance mirrors floor_touch_tolerance = 0.02; safety net: PSZ glide
  |      is the primary exit, but if PSZ never crosses the glide threshold
  |      psz_was_above stays 0 and glide never fires — ceiling catches full
  |      mean-reversion completions in that case)
  |
  |-- EXIT: PSZ glide exit
  |     psz_was_above=1
  |     AND psz_raw < bt_cross_psz_glide_threshold
  |     AND prev_psz_raw >= bt_cross_psz_glide_threshold
  |     --> EXIT "PSZ glide exit"
  |
  |-- EXIT: CTS hit BT (only if BT above floor zone)
  |     bt > (-1.0 + floor_tolerance) AND cts <= bt
  |     --> EXIT "CTS hit BT"
  |
  |-- EXIT: CTS hit floor
  |     cts <= -1.0 --> EXIT "CTS hit -1.0"
  |
  +-- None of the above --> HOLD
```

#### Notable: BT-cross exit path does NOT have:
- Sell threshold cross-down exit
- Deeply negative PSZ suppression

These are present in the PSZ exit path. BT-cross relies on PSZ glide as its
primary profit-taking mechanism; ceiling exit added as safety net for cases
where PSZ never crosses the glide threshold.

---

## Config Defaults

### Entry (`SavgolCTSEntryConfig`)

| Parameter | Default | Description |
|---|---|---|
| `cts_floor` | -1.0 | CTS floor level |
| `floor_touch_tolerance` | 0.02 | CTS/BT <= floor+tol counts as "at floor" for path 1 |
| `psz_cross_threshold` | -0.25 | PSZ must be at/below this in quiescent base |
| `psz_bend_enabled` | True | Enable PSZ bend quality gate |
| `psz_bend_lookback` | 8 | Bars to look back for quiescent base |
| `psz_bend_quiescence_threshold` | 0.05 | \|psz_v\| must be below this to count as quiescent |
| `psz_bend_quiescence_min_bars` | 5 | Minimum quiescent bars required |
| `psz_bend_v_floor` | -0.05 | psz_v must not drop below this (rejects V-bounces) |
| `psz_bend_v_min_at_signal` | 0.02 | Minimum psz_v at signal bar (confirms upward turn) |
| `psz_bend_mean_v_max` | 0.010 | Max mean psz_v over lookback (rejects slow drifters) |
| `bt_cross_enabled` | True | Enable BT crossover path |
| `bt_cross_oversold_threshold` | -0.50 | CTS must be below this for BT-cross |
| `bt_cross_bt_max` | -0.71 | BT must be at/below this (rejects shallow oversold entries) |

### Exit (`SavgolCTSExitConfig`)

| Parameter | Default | Description |
|---|---|---|
| `st_crossover_tolerance` | 0.03 | Tolerance for sell threshold crossover detection |
| `floor_tolerance` | 0.10 | Zone around -1.0 considered "floor" for suppression |
| `psz_stall_enabled` | False | Enable PSZ stall early exit |
| `psz_stall_check_bar` | 2 | Bar after entry to check psz_v |
| `bt_cross_psz_glide_threshold` | 0.30 | PSZ level for glide exit (BT-cross only) |

---

## EOD-Lag Execution Model

The `tag_signals()` method in `base.py` implements EOD-lag:
- Entry signal fires on bar `i`
- Trade opens on bar `i+1` (pending_entry mechanism)
- Exit checks begin from bar `i+2` onward

This means the signal bar's data is used for entry evaluation, but the
trade's `entry_price` and `entry_idx` correspond to the next bar's close.

---

## Research Decisions (2026-03-22, NIFTY 500)

All empirical results are on NIFTY 500 (~500 symbols, 2019–2026).

### PSZ glide exit: raw vs smooth
- Switched from `psz_smooth` to `psz_raw` in `_exit_bt_cross`.
- Raw PSZ is more responsive — exits one day earlier when the glide fades,
  before the smooth catches up. NIFTY 50: PSZ glide WR 50% → 72.7%.

### BT-cross BT depth gate (`bt_cross_bt_max = -0.71`)
- Trades with bt > -0.71: 51% WR, ~0% median PnL (n=2,045 dropped).
- Gate lifts BT-cross WR 56.8% → 58.8%, avg +1.47% → +1.70%.
- Note: bt <= -0.50 is redundant (already implicit from CTS <= -0.50 + crossover).

### CTS-BT-floor as entry path 1
- Both CTS and BT first touch -1.0: 64.8% WR, +2.50% avg (no ST suppression).
- With sell threshold suppressed (`_exit_floor`): 67.7% WR, +3.56% avg, >10%
  winner rate 23% → 30%. Sell threshold fires prematurely on full-range recovery.
- Adding as 3rd path to as-is system: overall WR 59.3% → 60.5%, avg +1.68% → +1.82%.

### Sell threshold for floor entries
- Suppressed in `_exit_floor`. ST was cutting 55% of floor trades before ceiling.
- Exits for floor path: ceiling hit (65%), ST=0%, CTS hit BT (4%).

### Entry precedence (floor → PSZ → BT-cross)
- Only 5 contested bars across NIFTY 500 where PSZ bend and floor touch fire
  on the same bar. Floor-first is better on those 5 (avg +15.63% vs +7.00%).
- Precedence change to PSZ-first: statistically identical overall (+0.01% delta).
  Current order retained.

### PSZ_V gate on floor and BT-cross entries — REJECTED
- CTS-BT-floor: 71.2% of entries have psz_v <= 0 at signal (stock still falling
  at the floor touch — structural, not noise). Dropped trades identical quality.
- BT-cross: dropped trades (psz_v <= 0) are better quality (59.7% WR, +1.97%)
  than kept (57.5% WR, +1.37%). Gate counterproductive.

### CTS-floor-leave replaces CTS-BT-floor touch (2026-03-22)
- Old path 1 (touch): enter the bar CTS first hits -1.0, BT also required at floor.
  2080 trades, 67.7% WR, +3.56% avg (NIFTY 500, production system).
- New path 1 (leave): enter the bar CTS first rises above -0.98 after being pinned.
  No BT constraint. 7523 trades, 63.9% WR, +2.52% avg.
- System improvement: 61.1% WR +2.08% avg → 62.5% WR +2.34% avg
  (+1.4pp WR, +0.26% avg, +22% volume, +0.45% median).
- Root gain: floor-leave absorbs 3364 BT-cross slots (58% WR, +1.53%) at better
  timing (lift bar vs BT crossover bar). Cleaner entry — one condition, no BT state.
- Trade-off: per-trade quality dips (67.7% → 63.9% WR) but absolute trade count
  triples and >10% winners go from 627 → 1774. System-level metrics all improve.
- Study: `study_floor_leave.py`.

### PSZ glide VA exit — NOT adopted
- Study: suppress PSZ glide exit when close > va_high, hold until close < va_high.
- NIFTY 500 (n=1,263 suppressed): avg delta = +1.04% but only 32% improved.
  WR drops 95.6% → 91.1%. Original PSZ glide exit already times well.

---

## Planned

### Path 4: CTS-floor — SUPERSEDED
- Superseded by the CTS-floor-leave redesign (path 1).
- The floor-leave condition (`prev_cts <= -0.98 AND cts > -0.98`) already captures
  CTS-alone floor exits (BT state irrelevant). No separate path needed.

---

## Backlog

### [ENTRY] Uptrend regime gate for floor paths — REJECTED
- Studied in `study_floor_uptrend_gate.py`. Gate is too blunt.
- 600 uptrend floor trades are 63.7% WR, +2.15% avg — comparable to system overall.
  90.5% exit at ceiling (+3.35% avg); only 9.5% hit BT (-9.29% avg).
  The big losers (ARE&M -37.6%, TATACOMM -37.2%) live in that 9.5% tail.
- Net effect of gating all uptrend entries: WR -0.1pp, avg -0.03%. Not worth it.
- Correct fix for the zombie tail is a time stop, not a regime gate (see below).

### [ENTRY] Positive PSZ gate for CTS-BT-floor-lift — REJECTED
- Studied in `study_lift_psz_gate.py`.
- Positive-PSZ lift trades are NOT the BT-hit source: 5.4% BT-hit vs 8.5% for psz<0 cohort.
  The big losers (GMDCLTD -27.1%, DBREALTY -17.0%) are outliers, not systemic.
- Positive PSZ means recovery started early → shorter holds (23.3 bars), smaller wins
  (+1.96% ceiling avg), but also fewer left-tail losses (<-5%: 14.7% vs 21.8% for psz<0).
  Safer cohort but capped upside.
- System-level effect: WR -0.2pp, avg +0.05%. Neutral — not worth the gate complexity.

### [EXIT] Time stop for floor paths — REJECTED
- Studied in `study_floor_time_stop.py`. Caps tested: 15, 30, 60, 90, 120 bars.
  Condition: exit only if currently losing at the cap bar.
- Every cap hurts. At cap=15: WR 66.9% → 50.4%, avg +3.00% → +1.80% (floor paths).
  At cap=120: only 58 trades stopped (avg -24.35%), net effect is -0.1pp WR / -0.03% avg.
- Root cause: floor paths hold long by design (avg 33.5 bars to ceiling exit). The time
  stop cuts trades that are temporarily negative but would recover. At cap=15, 673 ceiling
  exits are lost to time stops — those trades were negative at bar 15 but ceiling-bound.
- The zombie problem (ARE&M 268 bars, JSWENERGY 166 bars) is real but affects <60 trades
  at 120-bar cap. Not enough volume to justify the mechanism.
