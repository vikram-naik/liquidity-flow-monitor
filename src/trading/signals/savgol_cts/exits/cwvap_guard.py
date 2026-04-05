"""CWVAP price guard — post-exit suppression and release logic.

Runs *after* all path-specific exit checkers.  While the trade is above
CWVAP with positive momentum (PSZ > 0 or CTS > 0), structural exits are
suppressed.  When momentum fades or price drops below the tolerance
window, the suppressed exit is released.

The ``delivery_bad_count`` bitfield tracks suppression state across bars.
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def apply_cwvap_guard(
    row: dict, trade: Trade,
    res: str | None, state_val: int,
    cfg: SavgolCTSExitConfig,
    records: list[dict] | None, idx: int,
) -> tuple[str | None, int]:
    """Apply CWVAP suppression / release to a proposed exit result.

    Args:
        res:   Exit reason proposed by path-specific logic (or None).
        state_val: Current bitfield state (from path-specific exit).

    Returns:
        (final_exit_reason_or_None, updated_state_int)
    """
    st = SavgolCTSExitState.from_int(state_val)

    # Clear per-bar flag from previous bar
    st.suppressed_this_bar = False

    # If a path proposed an exit, mark suppression state
    if res is not None:
        st.exit_suppressed = True
        st.suppressed_this_bar = True

    close = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)
    psz_raw = row.get("price_slope_z", np.nan)
    cts = row.get("cts", np.nan)

    if np.isnan(close) or np.isnan(cwvap):
        return res, st.to_int()

    # --- Above CWVAP ---
    if close > cwvap:
        # Rule A: Suppress exit while momentum positive above CWVAP.
        psz_strong = not np.isnan(psz_raw) and psz_raw > 0.00
        cts_strong = not np.isnan(cts) and cts > 0.00
        is_strong_momentum = psz_strong or cts_strong

        if is_strong_momentum:
            return None, st.to_int()

        # Rule B: Release a previously suppressed exit now that momentum faded
        if res is not None or st.exit_suppressed:
            st.suppressed_this_bar = False
            final_res = res if res else ExitReason.CWVAP_EXHAUSTION
            return final_res, st.to_int()

        st.suppressed_this_bar = False
        return None, st.to_int()

    # --- Below CWVAP ---
    if st.exit_suppressed:
        st.suppressed_this_bar = True
        gc = cfg.cwvap_guard

        if gc.tolerance_pct > 0.0 and gc.tolerance_bars > 0:
            dist_pct = (close - cwvap) / cwvap * 100.0
            if dist_pct >= -gc.tolerance_pct:
                # Count consecutive bars below CWVAP
                bars_below = 1  # current bar
                if records is not None and idx > 0:
                    for j in range(1, gc.tolerance_bars + 1):
                        check_idx = idx - j
                        if check_idx <= trade.entry_idx:
                            break
                        prev_close = records[check_idx].get("close", np.nan)
                        prev_cwvap = records[check_idx].get("cwvap", np.nan)
                        if not np.isnan(prev_close) and not np.isnan(prev_cwvap):
                            if prev_close <= prev_cwvap:
                                bars_below += 1
                            else:
                                break

                if bars_below > gc.tolerance_bars:
                    final_res = res if res else f"CWVAP time stop ({gc.tolerance_bars} bars)"
                    return final_res, st.to_int()
                else:
                    return None, st.to_int()  # suppress and give chance
            else:
                # Dropped below tolerance
                final_res = res if res else ExitReason.SUPPRESSED_EXIT
                return final_res, st.to_int()
        else:
            # Baseline: no tolerance enabled
            final_res = res if res else ExitReason.SUPPRESSED_EXIT
            return final_res, st.to_int()

    return res, st.to_int()
