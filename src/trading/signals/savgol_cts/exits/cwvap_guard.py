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
from src.trading.signals.enums import ExitReason, EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def apply_cwvap_guard(
    row: dict, trade: Trade,
    res: str | None, state_val: int,
    cfg: SavgolCTSExitConfig,
    records: list[dict] | None, idx: int,
    tag: str = "",
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
        gc = cfg.cwvap_guard
        
        # Rule PREEMPT: Candlestick Rejection Guard (Only for specific tags for now)
        if getattr(gc, "candle_guard_enabled", False) and tag == EntryTag.FAS_BUY_CROSS.value:
            open_px = row.get("open", np.nan)
            high_px = row.get("high", np.nan)
            low_px = row.get("low", np.nan)
            volume = row.get("volume", np.nan)
            
            if not any(np.isnan(x) for x in [open_px, high_px, low_px, volume]):
                rng = high_px - low_px
                if rng > 0:
                    upper_wick = high_px - max(open_px, close)
                    upper_wick_pct = upper_wick / rng
                    ibs = (close - low_px) / rng
                    
                    # Calculate rolling average volume excluding current bar
                    avg_vol = np.nan
                    if records is not None and idx > 0:
                        vol_lb = getattr(gc, "vol_lookback", 20)
                        start_i = max(0, idx - vol_lb)
                        vols = [records[i].get("volume", np.nan) for i in range(start_i, idx)]
                        valid_vols = [v for v in vols if not np.isnan(v)]
                        if valid_vols:
                            avg_vol = sum(valid_vols) / len(valid_vols)
                            
                    is_high_volume = volume > (avg_vol * 1.5) if not np.isnan(avg_vol) else True
                    is_red_day = close < open_px
                    
                    if is_high_volume and is_red_day:
                        if upper_wick_pct > getattr(gc, "max_upper_wick_pct", 0.65):
                            return ExitReason.CANDLE_REJECTION, st.to_int()
                        if ibs < getattr(gc, "min_ibs_rejection", 0.15):
                            return ExitReason.CANDLE_REJECTION, st.to_int()
                            
                        if getattr(gc, "inside_bar_guard_enabled", True):
                            if records is not None and idx > 0:
                                prev_high = records[idx-1].get("high", np.nan)
                                prev_low = records[idx-1].get("low", np.nan)
                                if not any(np.isnan(x) for x in [prev_high, prev_low]):
                                    if high_px <= prev_high and low_px >= prev_low:
                                        # Check if volume is high enough based on inside bar mult
                                        ib_mult = getattr(gc, "inside_bar_vol_mult", 1.5)
                                        if not np.isnan(avg_vol) and volume > (avg_vol * ib_mult):
                                            return ExitReason.INSIDE_BAR_REJECTION, st.to_int()
                                        elif np.isnan(avg_vol):
                                            return ExitReason.INSIDE_BAR_REJECTION, st.to_int()

        # Rule PREEMPT 2: Structural Climax Guard (Range Exhaustion + Overextension)
        if getattr(gc, "climax_guard_enabled", True):
            rp63 = row.get("range_pos_63", np.nan)
            rp252 = row.get("range_pos_252", np.nan)
            fas = row.get("fas", np.nan)
            cwvap_dist = (close - cwvap) / cwvap * 100.0 if not np.isnan(cwvap) and cwvap > 0 else 0.0
            va_high = row.get("va_high", np.nan)
            
            if not any(np.isnan(x) for x in [rp63, rp252]):
                rp_thr = getattr(gc, "climax_rp_threshold", 0.95)
                dist_thr = getattr(gc, "climax_cwvap_dist", 10.0)
                fas_thr = getattr(gc, "climax_fas_threshold", 1.0)
                
                is_structural_top = (rp63 >= rp_thr) and (rp252 >= rp_thr)
                is_overextended = (cwvap_dist >= dist_thr) or (not np.isnan(fas) and fas >= fas_thr)
                
                if is_structural_top and is_overextended:
                    if not np.isnan(va_high) and close > va_high:
                        st.climax_hit_above_va = True
                        # Mark it and let it trail, skipping the immediate exit
                    else:
                        # Clear any suppression state and exit immediately
                        st.suppressed_this_bar = False
                        st.exit_suppressed = False
                        return ExitReason.STRUCTURAL_CLIMAX, st.to_int()

            # VA High Trail for marked climax exits
            if st.climax_hit_above_va and not np.isnan(va_high) and close < va_high:
                st.suppressed_this_bar = False
                st.exit_suppressed = False
                return ExitReason.STRUCTURAL_CLIMAX, st.to_int()

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
