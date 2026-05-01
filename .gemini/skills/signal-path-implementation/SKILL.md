---
name: signal-path-implementation
description: Repo-specific checklist and procedure for implementing a new signal entry/exit path into the core SavgolCTS package. Use this when a signal prototype from a study script is ready for permanent migration into the trading system.
---

# Signal Path Implementation Checklist

This skill provides the comprehensive procedure for migrating a validated signal study into the core LFM system.

## 1. Global Enums
Add a new strongly typed entry tag to `src/trading/signals/enums.py`.
- **Enum Class:** `EntryTag`
- **Format:** `<NAME> = "SavgolCTS <name-friendly>"`
- **Example:** `CTS_FLOOR_REVERSION = "SavgolCTS CTS-Floor-Reversion"`

## 2. Configuration Classes
Update `src/trading/signals/savgol_cts/config.py` to include parameters for the new path.
- **Entry Config:** Define a `@dataclass` for `<Name>EntryConfig`. Include all thresholds used in your study (e.g., `min_score`, `psz_z_max`).
- **Exit Config:** Define a `@dataclass` for `<Name>ExitConfig`. Include standard trailing and stop parameters (e.g., `hard_stop_pct`).
- **Wire into Composites:** Inject these into `SavgolCTSEntryConfig` and `SavgolCTSExitConfig` as fields with `default_factory`.

## 3. Implement Entry/Exit Logic
Create two new modules in the `savgol_cts` package:
- **Entry:** `src/trading/signals/savgol_cts/entries/<name>.py`
  - Implement `check_<name>(row, prev_row, cfg, records, idx)`.
  - Use `ScoreTracker` from `telemetry.py` if implementing a scoring system.
- **Exit:** `src/trading/signals/savgol_cts/exits/<name>.py`
  - Implement `exit_<name>(row, prev_row, trade, peak_close, bars_held, state_val, cfg, records, idx)`.

## 4. Signal Orchestrator Wiring
Update `src/trading/signals/savgol_cts/signal.py` to recognize the new path.
- **Imports:** Import your new entry and exit functions.
- **`check_entry`:** Add your path checker to the prioritized sequence. (First match wins).
- **`check_exit`:** Add your path exit to the dispatch `elif` chain based on `tag`.
- **`bespoke_tags`:** Add your new `EntryTag.NAME.value` to the `bespoke_tags` list if it manages its own exit logic (prevents it being killed by generic exit rules).

## 5. Documentation
Update `SIGNAL_FLOW.md` to reflect the new architecture.
- **Entry Flow:** Document the new `PATH X` with its gates, guards, and scoring rules.
- **State Bitfield:** If you used new bits in `SavgolCTSExitState`, document their mapping.
- **Exit Flow:** Add the new dispatch target.

## 6. Verification
Final validation before committing.
- **Syntax:** Run `python -m py_compile` on all modified files.
- **Walk-Forward:** Execute `./venv/bin/python scripts/walk_forward.py --watchlist "NIFTY 50" --signal savgol_cts`.
- **Metrics Check:** Ensure the **TEST period** win rate and PnL align with your study script results.
- **Cache:** Flush the Redis cache (`./venv/bin/python scripts/flush_cache.py --all`) to ensure no stale calculations interfere.
