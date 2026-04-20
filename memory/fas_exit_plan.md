# FAS-Zero-Cross Persistent Dual-Exhaustion Implementation Plan

## 1. State Machine Upgrades (`src/trading/signals/savgol_cts/state.py`)
Extend the `SavgolCTSExitState` bitfield to include persistent latches:
*   **Bit 10 (`recovery_passed`)**: `True` once either `CTS > 0` or `FAS > 0` post-entry.
*   **Bit 11 (`cts_exhausted`)**: `True` once `CTS` fails (strict cross from above).
*   **Bit 12 (`fas_exhausted`)**: `True` once `FAS` fails (strict cross from above).

## 2. Exhaustion Definitions (Strict Crossovers)
Once `recovery_passed` is `True`, monitor:
*   **CTS Exhaustion**: 
    *   Zero Cross: `prev_cts > 0 AND cts <= 0`
    *   Threshold Cross: `prev_cts > prev_cts_st AND cts <= cts_st`
*   **FAS Exhaustion**:
    *   Floor Breach: `prev_fas >= -0.1 AND fas < -0.1`
    *   Climax Latch: `fas >= 1.0` (also triggers exhaustion latch).

## 3. The Climax Kill-Switch (Override)
*   **Condition**: `FAS >= 1.0` at any point after recovery.
*   **Action**: Exit immediately with `ExitReason.FAS_CLIMAX`.

## 4. Smart Alpha-Release (Contextual Guard)
*   **Condition**: Trade PnL > 2.5% AND (`cts_exhausted` OR `fas_exhausted`).
*   **Smart Trigger**: Exit if price surrenders structural support: `close < cwvap`.
*   **Exit Reason**: `ExitReason.ALPHA_RELEASE_EXIT`.

## 5. The Union Exit
*   **Final Trigger**: `cts_exhausted` AND `fas_exhausted` are both **True**.
*   **Exit Reason**: `ExitReason.DUAL_ENGINE_FAILURE`.

## 6. Orchestration (`src/trading/signals/savgol_cts/signal.py`)
*   Keep `EntryTag.FAS_ZERO_CROSS` in `bespoke_tags` to bypass standard `CWVAP Guard`.
