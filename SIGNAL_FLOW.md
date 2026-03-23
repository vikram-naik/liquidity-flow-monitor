# SavgolCTS Signal — Entry / Exit Flow

**Source**: `src/trading/signals/savgol_cts.py`
**Last updated**: 2026-03-23

---

## Entry Flow

Four independent entry paths evaluated in priority order. First match wins.

```
check_entry(row, prev_row, cfg, records, idx)
  |
  |-- [Guard] CTS is NaN? --> REJECT "Missing CTS data"
  |
  |-- PATH 1: CTS-floor-leave  (_check_floor_leave)
  |     |
  |     |-- [Guard] CTS or prev_CTS is NaN? --> REJECT
  |     |
  |     |-- [Guard] PSZ late entry gate
  |     |     psz_raw >= 0 OR psz_raw >= psz_min_threshold (-0.25)
  |     |     FAIL --> REJECT "PSZ late entry"
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
  |
  |-- PATH 2: PSZ Bend  (_check_psz_bend)
  |     |
  |     |-- [Guard] PSZ raw or prev PSZ is NaN? --> REJECT
  |     |
  |     |-- [Guard] CTS oversold zone
  |     |     cts > psz_bend_cts_oversold_threshold (-0.50)
  |     |     FAIL --> REJECT "CTS not in oversold zone"
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
  |     |
  |     |-- [Gate] bt_cross_enabled=False? --> REJECT
  |     |
  |     |-- [Guard] prev_cts, prev_bt, or bt is NaN? --> REJECT
  |     |
  |     |-- CONDITION A: CTS crosses BT from below
  |     |     prev_cts <= prev_bt AND cts > bt
  |     |     FAIL --> REJECT "No BT crossover"
  |     |
  |     |-- CONDITION B: BT above floor (refined guard)
  |     |     REJECT only if: bt <= cts_floor (-1.0) AND prev_bt > cts_floor
  |     |     i.e. BT just dropped to floor this bar (BT newly pinned)
  |     |     ALLOW if: prev_bt was also at floor (both pinned together —
  |     |       CTS leaving first is a genuine recovery signal)
  |     |
  |     |-- CONDITION C: Still in oversold territory
  |     |     cts <= bt_cross_oversold_threshold (-0.50)
  |     |     FAIL --> REJECT
  |     |
  |     |-- CONDITION D: PSZ late entry gate
  |     |     psz_raw >= 0 OR psz_raw >= psz_min_threshold (-0.25)
  |     |     FAIL --> REJECT "PSZ late entry"
  |     |
  |     +--> Compute intensity (60 base + coh/pdd/regime bonuses)
  |          Tag: "BT-cross"
  |          --> ENTRY SIGNAL
  |
  |-- PATH 4: PSZv Flat  (_check_pszv_flat)
        |
        |-- [Gate] pszv_flat_enabled=False? --> REJECT
        |
        |-- [Guard] PSZv or PSZ is NaN? --> REJECT
        |
        |-- CONDITION A: PSZv in flat range AND PSZ deeply oversold
        |     pszv_flat_v_min (0.0) <= psz_v <= pszv_flat_v_max (0.04)
        |     AND psz_raw <= pszv_flat_psz_max (-0.3)
        |     FAIL --> REJECT
        |
        |-- CONDITION B: Sufficient history
        |     idx >= pszv_flat_lookback (3)
        |     FAIL --> REJECT
        |
        |-- CONDITION C: CTS at floor
        |     cts <= cts_floor (-1.0)
        |     FAIL --> REJECT
        |
        |-- CONDITION D: PSZ pinned in lookback
        |     All bars in lookback: psz_raw <= pszv_flat_psz_max (-0.3)
        |     FAIL --> REJECT
        |
        +--> Compute intensity (60 base + coh/pdd/regime bonuses)
             Tag: "PSZv-flat"
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

Exit is branched by entry tag. `check_exit` dispatches to the appropriate
indicator logic, then applies the Stateful CWVAP Price Guard.

```
check_exit(row, prev_row, trade, peak_close, bars_held, state, ..., cfg, records, idx)
  |
  |-- trade.entry_tag == "CTS-floor-leave"  --> _exit_floor + _exit_psz_glide fallback
  |-- trade.entry_tag == "CTS-BT-floor"     --> _exit_floor + _exit_psz_glide fallback (legacy)
  |-- trade.entry_tag == "BT-cross"         --> _exit_bt_cross
  |-- otherwise                             --> _exit_psz
  |
  +-- Indicator result (res) feeds into Stateful CWVAP Price Guard (see below)
```

### State Bitfield (`delivery_bad_count` repurposed)

```
bit 0 (& 0x01): cts_rose          — CTS has risen above -1.0 during this trade
bit 1 (& 0x02): psz_was_above     — PSZ crossed above glide threshold
bit 2 (& 0x04): cts_above_bt      — CTS has been above BT during this trade
bit 3 (& 0x08): exit_suppressed   — An indicator exit was suppressed by CWVAP Rule A
```

---

### Exit Path: CTS-floor-leave entries  (`_exit_floor` + `_exit_psz_glide`)

Tags routed here: `"CTS-floor-leave"`, `"CTS-BT-floor"` (legacy)

```
_exit_floor(row, prev_row, trade, peak_close, bars_held, state, cfg, records, idx)
  |
  |-- [Guard] CTS is NaN? --> HOLD
  |
  |-- Update cts_rose: if cts > -1.0, set bit 0
  |-- Update cts_above_bt: if cts > bt, set bit 2
  |-- Update psz_was_above: if psz_raw >= bt_cross_psz_glide_threshold (0.30), set bit 1
  |
  |-- [NOTE] Safety exits (CTS hit floor, CTS hit BT) are currently DISABLED
  |     (commented out, lines 556-561). This means floor-leave trades can only
  |     exit via ceiling-leave or the PSZ glide / CWVAP guard fallback.
  |     Known issue: causes zombie trades when CTS never reaches ceiling.
  |
  |-- EXIT: CTS ceiling hit  (ceiling-leave flavor)
  |     prev_cts >= 0.98 AND cts < 0.98 --> EXIT "CTS ceiling hit"
  |
  +-- None of the above --> HOLD
        |
        +-- Fallback: _exit_psz_glide (called by check_exit if _exit_floor returned None)
              |
              |-- PSZ glide exit: 5-bar mean of psz_raw < 0.30 AND previous mean >= 0.30
              |     --> EXIT "PSZ glide exit (<0.3)"
              |
              +-- else --> HOLD (feeds into CWVAP guard)
```

---

### Exit Path: PSZ / PSZv-flat entries  (`_exit_psz`)

Tags routed here: `"PSZ"`, `"PSZv-flat"`, any unrecognised tag.

```
_exit_psz(row, prev_row, trade, peak_close, bars_held, state, cfg, records, idx)
  |
  |-- [Guard] CTS is NaN? --> HOLD
  |
  |-- Update cts_rose: if cts > -1.0, set bit 0
  |-- Update cts_above_bt: if cts > bt, set bit 2
  |-- Track psz_was_above: if psz_raw >= bt_cross_psz_glide_threshold (0.30), set bit 1
  |
  |-- EXIT: CTS hit floor  (DISABLED — commented out)
  |     cts_rose=1 AND cts <= -1.0 --> EXIT "CTS hit floor"
  |
  |-- EXIT: CTS ceiling hit  (ceiling-leave flavor)
  |     prev_cts >= 0.98 AND cts < 0.98 --> EXIT "CTS ceiling hit"
  |
  |-- EXIT: PSZ glide exit  (via _exit_psz_glide)
  |     psz_was_above=1 AND 5-bar mean of psz_raw crosses below 0.30 --> EXIT "PSZ glide exit"
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
  |-- PSZ Stall Check  (_is_psz_stalled)
  |     [Gate] psz_stall_enabled=False? --> skip
  |     At exactly bar N (psz_stall_check_bar=2) after entry:
  |       if psz_v <= 0 --> EXIT "PSZ stall"
  |
  |-- [Gate] cts_rose=0? --> HOLD
  |
  |-- EXIT: CTS hit floor
  |     cts <= -1.0 --> EXIT "CTS hit floor"
  |
  |-- SUPPRESS: Floor zone
  |     cts <= (-1.0 + floor_tolerance) AND bt <= (-1.0 + floor_tolerance)
  |     Both deeply oversold --> HOLD (room to revive)
  |
  |-- EXIT: CTS ceiling hit  (ceiling-leave flavor)
  |     prev_cts >= 0.98 AND cts < 0.98 --> EXIT "CTS ceiling hit"
  |
  |-- EXIT: PSZ glide exit  (via _exit_psz_glide)
  |     psz_was_above=1
  |     AND 5-bar mean of psz_raw < bt_cross_psz_glide_threshold (0.30)
  |     AND previous mean >= bt_cross_psz_glide_threshold
  |     --> EXIT "PSZ glide exit"
  |
  |-- EXIT: CTS hit BT (only if BT above floor zone)
  |     bt > (-1.0 + floor_tolerance) AND cts <= bt
  |     --> EXIT "CTS hit BT"
  |
  +-- None of the above --> HOLD
```

---

### Stateful CWVAP Price Guard

Applied to **all exit paths** after the indicator logic returns. Uses the
`exit_suppressed` state bit to implement a glide mechanism: hold through
strong momentum above CWVAP, release when momentum fades.

```
check_exit — after indicator dispatch returns (res, state)
  |
  |-- If indicator proposed exit (res is not None): set exit_suppressed=1
  |
  |-- [Guard] cwvap_values empty or close is NaN? --> pass through res as-is
  |
  |-- close > CWVAP?
  |     |
  |     |-- Rule A: Strong momentum (PSZ > 0.00 OR CTS > 0.00)?
  |     |     --> SUPPRESS exit (return None) — hold while momentum strong
  |     |     (sets exit_suppressed=1 if indicator had proposed exit)
  |     |
  |     +-- Rule B: Momentum exhausted (PSZ <= 0 AND CTS <= 0)?
  |           |-- exit_suppressed=1 OR indicator proposed exit (res not None)?
  |           |     --> RELEASE: return res or "CWVAP momentum exhaustion"
  |           +-- else (no prior exit to release)
  |                 --> HOLD (no standalone exit — prevents false positives)
  |
  |-- close <= CWVAP?
  |     |-- exit_suppressed=1?
  |     |     --> RELEASE: return res or "Suppressed exit triggered (price barrier)"
  |     +-- else --> pass through res as-is
  |
  +-- Fallback: return res as-is
```

**Key design rule**: The CWVAP guard never generates standalone exits. It only
suppresses (Rule A) or releases (Rule B / below-CWVAP) exits that were
previously proposed by an indicator path. This prevents false positive exits
like BAJAJ-AUTO 2026-01-28 where CWVAP fired with no underlying indicator signal.

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
| `psz_bend_cts_oversold_threshold` | -0.50 | CTS must be at/below this for PSZ bend |
| `psz_bend_mean_v_max` | 0.010 | Max mean psz_v over lookback (rejects slow drifters) |
| `bt_cross_enabled` | True | Enable BT crossover path |
| `bt_cross_oversold_threshold` | -0.50 | CTS must be below this for BT-cross |
| `psz_min_threshold` | -0.25 | PSZ must be below this for floor-leave and BT-cross |
| `pszv_flat_enabled` | True | Enable PSZv flat path |
| `pszv_flat_v_min` | 0.0 | PSZv lower bound for flat range |
| `pszv_flat_v_max` | 0.04 | PSZv upper bound for flat range |
| `pszv_flat_psz_max` | -0.3 | PSZ must be at/below this for PSZv flat |
| `pszv_flat_lookback` | 3 | Bars of PSZ <= psz_max required |

### Exit (`SavgolCTSExitConfig`)

| Parameter | Default | Description |
|---|---|---|
| `st_crossover_tolerance` | 0.03 | Tolerance for sell threshold crossover detection |
| `floor_tolerance` | 0.10 | Zone around -1.0 considered "floor" for suppression |
| `ceiling_leave_tolerance` | 0.02 | Tolerance for ceiling-leave exit |
| `psz_stall_enabled` | False | Enable PSZ stall early exit |
| `psz_stall_check_bar` | 2 | Bar after entry to check psz_v |
| `bt_cross_psz_glide_threshold` | 0.30 | PSZ level for glide exit (BT-cross and floor-leave PSZ glide) |

---

## EOD-Lag Execution Model

The `tag_signals()` method in `base.py` implements EOD-lag:
- Entry signal fires on bar `i`
- Trade opens on bar `i+1` (pending_entry mechanism)
- Exit checks begin from bar `i+2` onward

This means the signal bar's data is used for entry evaluation, but the
trade's `entry_price` and `entry_idx` correspond to the next bar's close.

---

## Research Decisions

### BT-cross BT depth gate — REMOVED (2026-03-23)
- Old gate: `bt_cross_bt_max = -0.71` — rejected BT-cross when BT > -0.71.
- Restudy (NIFTY 500): trades with bt > -0.71 have 49.1% WR, +0.91 avg PnL,
  1.39 payoff — net positive. The `bt_cross_oversold_threshold = -0.50` already
  constrains BT-cross to the oversold zone; no trades exist with bt > -0.50.
- Gate removal: +854 trades, payoff improves 1.33 → 1.36.
- Study: `scripts/study_bt_cross_depth_gate.py`.

### CWVAP Price Guard Rule B — FIXED (2026-03-23)
- Bug: Rule B ("CWVAP momentum exhaustion") fired as a standalone exit when no
  indicator exit had been proposed or previously suppressed. This caused false
  exits (e.g. BAJAJ-AUTO 2026-01-28, exited 1 bar after entry at -0.6%).
- Fix: Rule B now only fires when `res is not None` (indicator exit this bar) or
  `exit_suppressed=True` (prior indicator exit held by Rule A). No standalone exits.
- Design rule: CWVAP guard modifies existing indicator exits, never creates new ones.

### CWVAP Rule A extended glide — REFINED (2026-03-23)
- Old Rule A: suppress exit when `close > CWVAP AND (PSZ > 0.15 OR CTS > 0.2)`.
- New Rule A: suppress exit when `close > CWVAP AND (PSZ > 0.00 OR CTS > 0.00)`.
- Rationale: The previous combination (0.15/0.2) was an arbitrary guess that yielded 62.6% WR and 3.25% Avg PnL. A comprehensive 36-combination grid search (`scripts/study_cwvap_guard_thresholds.py`) across NIFTY 500 revealed that dropping both thresholds to 0.00 maximizes Avg PnL (3.60%) and Payoff (1.17x) while only slightly lowering WR (61.9%), by allowing the trade maximum breathing room above the CWVAP barrier as long as momentum is technically positive.

### CTS-floor-leave lift magnitude gate — REJECTED (2026-03-23)
- Motivation: ASIANPAINT 2026-01-27, CTS blipped from -1.0 to -0.943 for one bar.
- NIFTY 500: dropped trades (CTS -0.98 to -0.95) are 66.2% WR, +1.56 avg —
  the best cohort. No clean cutoff exists. Gate counterproductive.
- Study: `scripts/study_floor_leave_lift_gate.py`.

### Floor-leave zombie trades — PENDING (2026-03-23)
- `_exit_floor` has safety exits (CTS hit floor, CTS hit BT) commented out.
  Only ceiling-leave exit is active. When CTS never reaches 0.98, the trade
  becomes a zombie with no exit path.
- NIFTY 500: 103 floor-leave zombies, avg open PnL = -6.4%.
- Re-enabling safety exits: zombies drop to 57 (recent/unresolved), PSZ glide
  WR 40.6% → 81.2%, ceiling WR 50.1% → 83.2%.
- PDD and recent-ceiling entry guards tested and found unhelpful once exit is fixed.
- Study: `scripts/study_floor_leave_zombie.py`. Decision pending.

### PSZ glide exit: raw vs smooth
- Switched from `psz_smooth` to `psz_raw` in `_exit_bt_cross`.
- Raw PSZ is more responsive — exits one day earlier when the glide fades,
  before the smooth catches up. NIFTY 50: PSZ glide WR 50% → 72.7%.

### CTS-floor-leave replaces CTS-BT-floor touch (2026-03-22)
- Old path 1 (touch): enter the bar CTS first hits -1.0, BT also required at floor.
  2080 trades, 67.7% WR, +3.56% avg (NIFTY 500, production system).
- New path 1 (leave): enter the bar CTS first rises above -0.98 after being pinned.
  No BT constraint. 7523 trades, 63.9% WR, +2.52% avg.
- System improvement: 61.1% WR +2.08% avg → 62.5% WR +2.34% avg.
- Study: `study_floor_leave.py`.

### PSZ glide exit for PSZ/PSZv-flat entries — ADOPTED (2026-03-23)
- `_exit_psz` had only ceiling-hit exit (floor-hit commented out). No mid-range
  exit existed, causing zombie trades when CTS oscillated without reaching extremes
  (e.g. DRREDDY 2026-01-22).
- Added PSZ glide exit to `_exit_psz`: track `psz_was_above`, exit when PSZ drops
  below glide threshold (0.30) after having been above it. Same mechanism as BT-cross.
- CTS-hit-floor re-enablement tested but rejected: 4.2% WR (PSZ), 6.3% WR (PSZv-flat).
- PSZ glide exit quality:
  - PSZ path: 64.0% WR, +2.24% avg, 12.3 bars (197 trades)
  - PSZv-flat: 70.9% WR, +2.31% avg, 15.8 bars (296 trades)
- Zombies reduced: PSZ 35→23, PSZv-flat 81→64.
- Study: `scripts/study_psz_entry_glide_exit.py`.

### PSZ glide VA exit — NOT adopted
- Study: suppress PSZ glide exit when close > va_high, hold until close < va_high.
- NIFTY 500 (n=1,263 suppressed): avg delta = +1.04% but only 32% improved.
  WR drops 95.6% → 91.1%. Original PSZ glide exit already times well.

---

## Backlog

### [EXIT] Re-enable _exit_floor safety exits
- See "Floor-leave zombie trades" above. Highest-leverage pending change.
- Uncommenting lines 556-561 in `savgol_cts.py` resolves most zombies.

### [ENTRY] Uptrend regime gate for floor paths — REJECTED
- 600 uptrend floor trades are 63.7% WR, +2.15% avg — comparable to system overall.
- Study: `study_floor_uptrend_gate.py`.

### [ENTRY] Positive PSZ gate for CTS-BT-floor-lift — REJECTED
- Study: `study_lift_psz_gate.py`.

### [EXIT] Time stop for floor paths — REJECTED
- Study: `study_floor_time_stop.py`. Every cap hurts.

### [ENTRY] PSZ_V gate on floor and BT-cross entries — REJECTED
- 71.2% of floor entries have psz_v <= 0 at signal. Gate counterproductive.
