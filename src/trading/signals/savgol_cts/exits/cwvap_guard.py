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


def _apply_cwvap_guard_raw(
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
        # Loss prevention rules bypass CWVAP suppression
        bypass_reasons = [ExitReason.CWVAP_LOST, ExitReason.GAP_DOWN_LOSS, ExitReason.HARD_STOP, ExitReason.PNL_CAP]
        
        # Crossover exits bypass CWVAP suppression if cts is below sell threshold (real crossovers),
        # but NOT when the climax trail is active — that state already committed to trailing until
        # price falls below va_high, and a single CTS tick below threshold shouldn't override it.
        cts = row.get("cts", np.nan)
        cts_st = row.get("cts_sell_threshold", np.nan)
        if not any(np.isnan(x) for x in [cts, cts_st]) and cts < cts_st:
            if not st.climax_hit_above_va:
                bypass_reasons.append(ExitReason.PRT_ST_CROSS)
                bypass_reasons.append(ExitReason.ST_CROSS)
            
        gc = cfg.cwvap_guard
        early_release_active = getattr(gc, "cwc_slope_early_release_enabled", True)
        if not early_release_active:
            if ExitReason.PRT_ST_CROSS not in bypass_reasons:
                bypass_reasons.append(ExitReason.PRT_ST_CROSS)
            if ExitReason.ST_CROSS not in bypass_reasons:
                bypass_reasons.append(ExitReason.ST_CROSS)
            
        if res in bypass_reasons:
            st.exit_suppressed = False
            st.suppressed_this_bar = False
            return res, st.to_int()
            
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
        if getattr(gc, "candle_guard_enabled", False) and tag == EntryTag.UNIVERSAL_CROSS.value:
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
                # Guard 1 — Intraday High Guard: if the bar's high touched above va_high, this is a
                # wick/pullback scenario (e.g. opened above VA High, sold off to close just below).
                # Do not release the trail on a wick — only exit when the entire bar is below va_high.
                high_px = row.get("high", np.nan)
                intraday_held = (
                    getattr(gc, "climax_va_intraday_guard_enabled", True)
                    and not np.isnan(high_px)
                    and high_px > va_high
                )

                # Guard 2 — CTS Peak Guard: if CTS is at its maximum value (1.0), momentum is still
                # fully intact. Keep trailing rather than exiting — we'd leave too much on the table.
                cts_maxed = not np.isnan(cts) and cts >= 1.0

                if intraday_held or cts_maxed:
                    pass  # suppress release — price still in play
                else:
                    st.suppressed_this_bar = False
                    st.exit_suppressed = False
                    return ExitReason.STRUCTURAL_CLIMAX, st.to_int()

        # Rule PREEMPT 3: CWC Slope Early Release
        if getattr(gc, "cwc_slope_early_release_enabled", True):
            cwc_slope = row.get("cwc_slope", np.nan)
            thr = getattr(gc, "cwc_slope_early_release_threshold", -0.01)
            if not np.isnan(cwc_slope) and cwc_slope < thr:
                # Disable early release if CTS is above or equal to the CTS sell threshold
                cts_st = row.get("cts_sell_threshold", np.nan)
                cts_above_st = not np.isnan(cts) and not np.isnan(cts_st) and cts >= cts_st
                if not cts_above_st:
                    if res is not None or st.exit_suppressed:
                        st.suppressed_this_bar = False
                        st.exit_suppressed = False
                        final_res = res if res else ExitReason.CWC_SLOPE_EARLY_RELEASE
                        return final_res, st.to_int()

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
    gc = cfg.cwvap_guard

    if getattr(gc, "hybrid_exit_enabled", False):
        pnl_pct = ((close - trade.entry_price) / trade.entry_price) * 100.0 if trade and trade.entry_price > 0 else 0.0
        if pnl_pct < 0.0:
            fas = row.get("fas", np.nan)
            psz_v = row.get("psz_v", np.nan)
            fas_neg = not np.isnan(fas) and fas < 0
            psz_v_neg = not np.isnan(psz_v) and psz_v < 0
            if fas_neg or psz_v_neg:
                st.suppressed_this_bar = False
                st.exit_suppressed = False
                return ExitReason.CWVAP_EXHAUSTION, st.to_int()

    if st.exit_suppressed:
        st.suppressed_this_bar = True

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


def apply_cwvap_guard(
    row: dict, trade: Trade,
    res: str | None, state_val: int,
    cfg: SavgolCTSExitConfig,
    records: list[dict] | None, idx: int,
    tag: str = "",
) -> tuple[str | None, int]:
    """Wrapper to apply VA High Breakout Trailing Guard to proposed exits."""
    gc = cfg.cwvap_guard
    st = SavgolCTSExitState.from_int(state_val)

    # 1. Trend Reclaim reset check (Reclaim PRT)
    if st.exit_suppressed and getattr(gc, "trend_reclaim_enabled", True):
        prt = row.get("prt", np.nan)
        prt_st = row.get("prt_sell_threshold", np.nan)
        if not any(np.isnan(x) for x in [prt, prt_st]) and prt >= prt_st:
            st.exit_suppressed = False
            st.suppressed_this_bar = False
            state_val = st.to_int()

    # 2. Run raw guard logic
    final_res, st_val = _apply_cwvap_guard_raw(row, trade, res, state_val, cfg, records, idx, tag)

    # 3. Apply VA High Breakout Suppression
    if final_res is not None:
        if getattr(gc, "va_high_breakout_suppression_enabled", True):
            # Define critical bypass reasons that should never be suppressed
            bypass = [
                ExitReason.CWVAP_LOST,
                ExitReason.GAP_DOWN_LOSS,
                ExitReason.HARD_STOP,
                ExitReason.PNL_CAP,
                ExitReason.STRUCTURAL_CLIMAX,
                ExitReason.CANDLE_REJECTION,
                ExitReason.INSIDE_BAR_REJECTION,
            ]
            if final_res not in bypass:
                close = row.get("close", np.nan)
                va_high = row.get("va_high", np.nan)
                if not np.isnan(close) and not np.isnan(va_high) and close > va_high:
                    st = SavgolCTSExitState.from_int(st_val)
                    st.exit_suppressed = True
                    st.suppressed_this_bar = True
                    return None, st.to_int()

    # 4. Apply Expert 5 enhancements (Regime-Aware hybrid)
    st = SavgolCTSExitState.from_int(st_val)
    if final_res is None and st.exit_suppressed and getattr(gc, "expert_exits_enabled", True):
        close = row.get("close", np.nan)
        if trade is not None and trade.entry_price > 0 and not np.isnan(close):
            # Compute peak close for the trade
            peak_close = trade.entry_price
            if records is not None:
                for j in range(trade.entry_idx, min(idx + 1, len(records))):
                    c_val = records[j].get("close", np.nan)
                    if not np.isnan(c_val) and c_val > peak_close:
                        peak_close = c_val
                        
            peak_pnl = (peak_close / trade.entry_price - 1.0) * 100.0
            
            if peak_pnl >= getattr(gc, "peak_pnl_trigger", 10.0):
                atr = row.get("atr_20", np.nan)
                regime = row.get("regime", "notrend")
                is_uptrend = (regime == "uptrend")
                
                # A. Regime-Aware Trailing Stop
                atr_mult = getattr(gc, "uptrend_atr_mult", 3.0) if is_uptrend else getattr(gc, "normal_atr_mult", 2.0)
                if not np.isnan(atr) and atr > 0 and close < (peak_close - atr_mult * atr):
                    st.exit_suppressed = False
                    st.suppressed_this_bar = False
                    reason = ExitReason.EXPERT5_ATR_TRAIL_3_0 if atr_mult == 3.0 else ExitReason.EXPERT5_ATR_TRAIL_2_0
                    return reason, st.to_int()
                
                # B. Regime-Aware / Coherence-Filtered Exhaustion
                cwc = row.get("cwc", np.nan)
                cwc_slope = row.get("cwc_slope", np.nan)
                psz_v = row.get("psz_v", np.nan)
                
                if not np.isnan(cwc) and not np.isnan(cwc_slope):
                    if is_uptrend:
                        cwc_min = getattr(gc, "uptrend_cwc_min", 0.10)
                        cwc_slope_min = getattr(gc, "uptrend_cwc_slope_min", -0.06)
                        if cwc < cwc_min and cwc_slope < cwc_slope_min:
                            st.exit_suppressed = False
                            st.suppressed_this_bar = False
                            return ExitReason.EXPERT5_UPTREND_COHERENCE_MELTDOWN, st.to_int()
                    else:
                        cwc_min = getattr(gc, "normal_cwc_min", 0.25)
                        cwc_slope_min = getattr(gc, "normal_cwc_slope_min", -0.04)
                        if cwc < cwc_min and cwc_slope < cwc_slope_min:
                            st.exit_suppressed = False
                            st.suppressed_this_bar = False
                            return ExitReason.EXPERT5_NORMAL_COHERENCE_BREACH, st.to_int()
                        elif not np.isnan(psz_v) and psz_v < getattr(gc, "normal_psz_v_min", -0.2):
                            st.exit_suppressed = False
                            st.suppressed_this_bar = False
                            return ExitReason.EXPERT5_NORMAL_MOMENTUM_MELTDOWN, st.to_int()
                
                # C. Regime-Aware Parabolic Exhaustion
                rp10 = row.get("range_pos_10", np.nan)
                prev_low = np.nan
                if records is not None and idx > 0:
                    prev_low = records[idx - 1].get("low", np.nan)
                    
                was_overextended = False
                overextended_thr = getattr(gc, "overextended_rp_threshold", 0.90)
                if records is not None:
                    for j in range(max(trade.entry_idx, idx - 3), idx + 1):
                        r = records[j]
                        if r.get("range_pos_10", 0.0) >= overextended_thr:
                            was_overextended = True
                            break
                            
                if was_overextended:
                    if is_uptrend:
                        buffer = getattr(gc, "uptrend_low_break_buffer_atr", 0.30) * atr if not np.isnan(atr) else 0.0
                        if not np.isnan(prev_low) and close < (prev_low - buffer):
                            cwc = row.get("cwc", np.nan)
                            cwc_slope = row.get("cwc_slope", np.nan)
                            cwc_min = getattr(gc, "parabolic_cwc_min", 0.35)
                            cwc_slope_min = getattr(gc, "parabolic_cwc_slope_min", -0.02)
                            
                            is_highly_coherent = (
                                (not np.isnan(cwc) and cwc >= cwc_min) or
                                (not np.isnan(cwc_slope) and cwc_slope >= cwc_slope_min)
                            )
                            
                            if is_highly_coherent:
                                st.exit_suppressed = True
                                st.suppressed_this_bar = True
                            else:
                                st.exit_suppressed = False
                                st.suppressed_this_bar = False
                                return ExitReason.EXPERT5_UPTREND_PARABOLIC_LOW_BREAK, st.to_int()
                    else:
                        if not np.isnan(prev_low) and close < prev_low:
                            st.exit_suppressed = False
                            st.suppressed_this_bar = False
                            return ExitReason.EXPERT5_NORMAL_PARABOLIC_LOW_BREAK, st.to_int()
                        elif not np.isnan(rp10) and rp10 < getattr(gc, "normal_rp_reversion", 0.70):
                            st.exit_suppressed = False
                            st.suppressed_this_bar = False
                            return ExitReason.EXPERT5_NORMAL_PARABOLIC_REVERSION, st.to_int()

    return final_res, st_val

