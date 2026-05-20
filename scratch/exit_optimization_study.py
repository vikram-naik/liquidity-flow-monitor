#!/usr/bin/env python3
"""Universal Cross Exit Optimization Study.

Simulates and evaluates alternative trailing stops and adaptive exit models
on the Universal Cross entry signals during the TEST period.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade, SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag, ExitReason
from src.trading.signals.savgol_cts.ml_guard import MLGuard
from scripts.walk_forward import get_watchlist_symbols, today_str


def is_exit_reason(reason, expected_enum: ExitReason) -> bool:
    if reason is None:
        return False
    r_str = str(reason)
    return expected_enum.name in r_str or expected_enum.value in r_str


def apply_cwvap_guard_custom(
    row: dict, trade: Trade,
    res: str | None, state_val: int,
    cfg: SavgolCTSExitConfig,
    records: list[dict] | None, idx: int,
    tag: str = "",
    allow_prt_suppression: bool = False
) -> tuple[str | None, int]:
    from src.trading.signals.savgol_cts.state import SavgolCTSExitState
    from src.trading.signals.enums import ExitReason, EntryTag
    
    st = SavgolCTSExitState.from_int(state_val)
    st.suppressed_this_bar = False

    if res is not None:
        # Loss prevention rules bypass CWVAP suppression
        bypass_reasons = [ExitReason.CWVAP_LOST, ExitReason.GAP_DOWN_LOSS, ExitReason.HARD_STOP, ExitReason.PNL_CAP]
        if not allow_prt_suppression:
            bypass_reasons.append(ExitReason.PRT_ST_CROSS)
            
        # Match by name or value (robust comparison)
        is_bypass = False
        res_str = str(res)
        for br in bypass_reasons:
            if br.name in res_str or br.value in res_str:
                is_bypass = True
                break
                
        if is_bypass:
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
                    else:
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
                final_res = res if res else ExitReason.SUPPRESSED_EXIT
                return final_res, st.to_int()
        else:
            final_res = res if res else ExitReason.SUPPRESSED_EXIT
            return final_res, st.to_int()

    return res, st.to_int()


def simulate_trades_custom(
    ticker: str, df: pd.DataFrame,
    entry_cfg, exit_cfg, signal,
    model_name: str,
    **kwargs
) -> list[Trade]:
    records = df.to_dict("records")
    n = len(records)
    trades = []
    in_trade = False
    trade = None
    peak_close = 0.0
    delivery_bad_count = 0
    cwvap_values: list[float] = []
    pending_signal: dict | None = None
    pending_exit_reason: ExitReason | str | None = None
    
    stop_activated = False

    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue

        cw = row.get("cwvap", np.nan)
        cwvap_values.append(cw)

        # Execute pending exit at today's open (EOD-lag)
        if pending_exit_reason is not None:
            open_price = row.get("open", np.nan)
            exit_price = open_price if not np.isnan(open_price) else close
            trade.exit_date = str(row.get("date", ""))[:10]
            trade.exit_price = round(exit_price, 2)
            trade.exit_reason = pending_exit_reason
            trade.pnl_pct = round((exit_price / trade.entry_price - 1) * 100, 2)
            trade.duration = i - trade.entry_idx
            trade.mfe_pct = round(trade.mfe_pct, 2)
            trade.mae_pct = round(trade.mae_pct, 2)
            trades.append(trade)
            in_trade = False
            trade = None
            delivery_bad_count = 0
            pending_exit_reason = None
            continue

        if in_trade:
            if close > peak_close:
                peak_close = close

            bars_held = i - trade.entry_idx
            mfe = max(trade.mfe_pct, (close / trade.entry_price - 1) * 100)
            mae_val = (close / trade.entry_price - 1) * 100
            mae = min(-trade.mae_pct, mae_val)
            trade.mfe_pct = mfe
            if -mae > trade.mae_pct:
                trade.mae_pct = -mae

            pnl_pct = (close / trade.entry_price - 1) * 100.0

            # Current standard exit check
            if model_name in ["cwvap_guard_prt", "cwvap_guard_prt_no_cap"]:
                from src.trading.signals.savgol_cts.exits import exit_universal_cross
                from src.trading.signals.savgol_cts.state import SavgolCTSExitState
                
                st = SavgolCTSExitState.from_int(delivery_bad_count)
                if not np.isnan(close) and not np.isnan(cw) and close > cw:
                    st.price_above_cwvap = True
                    
                exit_reason, state_val = exit_universal_cross(
                    row, prev, trade, peak_close, bars_held, st.to_int(),
                    exit_cfg.universal_cross, records, i
                )
                
                # Suppress PnL cap if requested
                if model_name == "cwvap_guard_prt_no_cap":
                    if is_exit_reason(exit_reason, ExitReason.PNL_CAP):
                        exit_reason = None
                        
                reason, delivery_bad_count = apply_cwvap_guard_custom(
                    row, trade, exit_reason, state_val,
                    exit_cfg, records, i, tag=trade.entry_tag,
                    allow_prt_suppression=True
                )
                if reason:
                    reason = str(reason)
            else:
                reason, delivery_bad_count = signal.check_exit(
                    row, prev, trade, peak_close, bars_held,
                    delivery_bad_count, cwvap_values, exit_cfg,
                    records, i,
                )
            
            # --- MODEL OVERRIDES ---
            if model_name == "baseline":
                pass
                
            elif model_name == "no_cap":
                # Disable PnL Cap
                if is_exit_reason(reason, ExitReason.PNL_CAP):
                    reason = None
                    
            elif model_name == "atr_chandelier":
                if is_exit_reason(reason, ExitReason.PNL_CAP):
                    reason = None
                
                # ATR Chandelier Trail
                atr = row.get("atr_20", np.nan)
                k = kwargs.get("k", 2.5)
                activation_pct = kwargs.get("activation_pct", 5.0)
                
                if pnl_pct >= activation_pct:
                    stop_activated = True
                
                if stop_activated and not np.isnan(atr):
                    stop_price = peak_close - k * atr
                    if close <= stop_price:
                        reason = "ATR_CHANDELIER"
                        
            elif model_name == "breakout_low":
                if is_exit_reason(reason, ExitReason.PNL_CAP):
                    reason = None
                
                # Breakout Low-Trail (N-bar low)
                n_bars = kwargs.get("n_bars", 5)
                activation_pct = kwargs.get("activation_pct", 0.0)
                
                if pnl_pct >= activation_pct:
                    stop_activated = True
                
                if stop_activated:
                    start_low_idx = max(0, i - n_bars)
                    lows = [records[j].get("low", np.nan) for j in range(start_low_idx, i)]
                    lows = [l for l in lows if not np.isnan(l)]
                    if lows:
                        stop_price = min(lows)
                        if close <= stop_price:
                            reason = "BREAKOUT_LOW"
                            
            elif model_name == "adaptive_cap":
                # Adaptive PnL Cap based on ML score of signal day
                ml_score = trade.conviction_score
                adaptive_thr = 8.0
                if ml_score >= 93.0:
                    adaptive_thr = 16.0
                elif ml_score >= 90.0:
                    adaptive_thr = 12.0
                    
                if is_exit_reason(reason, ExitReason.PNL_CAP):
                    if pnl_pct < adaptive_thr:
                        reason = None
                else:
                    if pnl_pct >= adaptive_thr:
                        reason = ExitReason.PNL_CAP

            elif model_name in ["cwvap_guard_prt", "cwvap_guard_prt_no_cap"]:
                pass

            if reason:
                pending_exit_reason = reason

        elif pending_signal is not None:
            sig = pending_signal
            pending_signal = None
            atr = row.get("atr_20", 0)
            if atr <= 0 or np.isnan(atr):
                continue
            psz_now = row.get("price_slope_z", np.nan)
            
            # Compute ML Guard score on signal day (prev row)
            try:
                prob = MLGuard.get_instance().score_setup(prev)
                ml_score = prob * 100.0 if prob is not None else 0.0
            except Exception:
                ml_score = 0.0

            trade = Trade(
                symbol=ticker,
                entry_date=str(row.get("date", ""))[:10],
                entry_price=close,
                entry_idx=i,
                atr_at_entry=atr,
                conviction_score=ml_score,
                regime_at_entry=sig.get("details", {}).get("regime", "-"),
                entry_tag=sig.get("details", {}).get("entry_tag", ""),
                psz_at_entry=psz_now if not np.isnan(psz_now) else 0.0,
                psz_peak=psz_now if not np.isnan(psz_now) else 0.0,
            )
            peak_close = close
            delivery_bad_count = 0
            in_trade = True
            stop_activated = False

        else:
            qualifies, soft_count, fdetails = signal.check_entry(row, prev, entry_cfg, records, i)
            if qualifies:
                pending_signal = {"soft_count": soft_count, "details": fdetails}

    if in_trade and trade:
        last = records[-1]
        trade.exit_date = str(last.get("date", ""))[:10]
        trade.exit_price = last.get("close", trade.entry_price)
        trade.exit_reason = ExitReason.END_OF_DATA
        trade.pnl_pct = round((trade.exit_price / trade.entry_price - 1) * 100, 2)
        trade.duration = n - 1 - trade.entry_idx
        trade.mfe_pct = round(trade.mfe_pct, 2)
        trade.mae_pct = round(trade.mae_pct, 2)
        trades.append(trade)

    return trades


def run_model(symbols: list[str], start: str, end: str, entry_cfg, exit_cfg, signal, model_name: str, **kwargs) -> list[Trade]:
    all_trades = []
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym)
            result = engine.run()
            trades = simulate_trades_custom(sym, result.ledger, entry_cfg, exit_cfg, signal, model_name, **kwargs)
            # Filter trades to TEST period and EntryTag.UNIVERSAL_CROSS
            period_trades = [
                t for t in trades 
                if start <= str(t.entry_date) <= end and t.entry_tag == EntryTag.UNIVERSAL_CROSS.value
            ]
            all_trades.extend(period_trades)
        except Exception as e:
            print(f"  Error {sym}: {e}", file=sys.stderr)
    return all_trades


def summarize_model(trades: list[Trade]) -> dict:
    if not trades:
        return {
            "Count": 0, "WinRate%": 0.0, "AvgPnL%": 0.0, "MedianPnL%": 0.0,
            "PF": 0.0, "AvgMFE%": 0.0, "AvgMAE%": 0.0, "AvgBars": 0.0, "LeftOnTable%": 0.0
        }
    pnls = [t.pnl_pct for t in trades]
    mfes = [t.mfe_pct for t in trades]
    maes = [t.mae_pct for t in trades]
    durations = [t.duration for t in trades]
    winners = sum(1 for p in pnls if p > 0)
    
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p <= 0))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    
    left = [m - p for m, p in zip(mfes, pnls)]
    
    return {
        "Count": len(trades),
        "WinRate%": round(winners / len(trades) * 100, 2),
        "AvgPnL%": round(np.mean(pnls), 2),
        "MedianPnL%": round(np.median(pnls), 2),
        "PF": round(pf, 2),
        "AvgMFE%": round(np.mean(mfes), 2),
        "AvgMAE%": round(np.mean(maes), 2),
        "AvgBars": round(np.mean(durations), 1),
        "LeftOnTable%": round(np.mean(left), 2)
    }


def main():
    parser = argparse.ArgumentParser(description="Universal Cross Exit Optimization Study")
    parser.add_argument("--watchlist", default="NIFTY 50", help="Watchlist to simulate")
    parser.add_argument("--start-date", default="2024-01-01", help="Test start date")
    parser.add_argument("--end-date", default=today_str(), help="Test end date")
    args = parser.parse_args()

    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    symbols = get_watchlist_symbols(args.watchlist)

    print(f"Running Exit Optimization Study for watchlist '{args.watchlist}' on {len(symbols)} symbols...")
    print(f"Period: {args.start_date} to {args.end_date}\n")

    # 1. Baseline
    print("Simulating Model 1: Baseline (Production Exit Logic: 8% PnL Cap + PRT Trail)...")
    baseline_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "baseline")
    
    # 2. Pure PRT (No Cap)
    print("Simulating Model 2: Pure PRT Trail (No PnL Cap)...")
    nocap_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "no_cap")
    
    # 3. ATR Chandelier sweeps
    print("Simulating Model 3: ATR Chandelier Trail...")
    chand_25_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "atr_chandelier", k=2.5, activation_pct=5.0)
    chand_30_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "atr_chandelier", k=3.0, activation_pct=5.0)
    chand_35_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "atr_chandelier", k=3.5, activation_pct=5.0)
    
    # 4. Breakout Low-Trail sweeps
    print("Simulating Model 4: Breakout Low-Trail (N-bar low)...")
    low_3_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "breakout_low", n_bars=3, activation_pct=0.0)
    low_5_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "breakout_low", n_bars=5, activation_pct=0.0)
    low_7_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "breakout_low", n_bars=7, activation_pct=0.0)
    
    # 5. Adaptive Cap
    print("Simulating Model 5: Adaptive PnL Cap (8% / 12% / 16% based on ML Score)...")
    adaptive_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "adaptive_cap")

    # 6. CWVAP Guard on PRT (8% Cap)
    print("Simulating Model 6: CWVAP Guard on PRT (8% PnL Cap + PRT Trail with CWVAP Suppression)...")
    cwvap_guard_prt_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "cwvap_guard_prt")

    # 7. CWVAP Guard on PRT (No Cap)
    print("Simulating Model 7: CWVAP Guard on PRT (No PnL Cap + PRT Trail with CWVAP Suppression)...")
    cwvap_guard_prt_nocap_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "cwvap_guard_prt_no_cap")

    # Summaries
    models = {
        "Baseline (8% Cap)": baseline_trades,
        "No PnL Cap (Pure PRT)": nocap_trades,
        "ATR Chandelier (K=2.5)": chand_25_trades,
        "ATR Chandelier (K=3.0)": chand_30_trades,
        "ATR Chandelier (K=3.5)": chand_35_trades,
        "Breakout Low (N=3)": low_3_trades,
        "Breakout Low (N=5)": low_5_trades,
        "Breakout Low (N=7)": low_7_trades,
        "Adaptive Cap (8/12/16%)": adaptive_trades,
        "CWVAP Guard PRT (8% Cap)": cwvap_guard_prt_trades,
        "CWVAP Guard PRT (No Cap)": cwvap_guard_prt_nocap_trades
    }

    results = []
    for name, trades in models.items():
        summary = summarize_model(trades)
        summary["Model Name"] = name
        results.append(summary)

    df_results = pd.DataFrame(results)[[
        "Model Name", "Count", "WinRate%", "AvgPnL%", "MedianPnL%", "PF", "AvgMFE%", "AvgMAE%", "AvgBars", "LeftOnTable%"
    ]]
    
    print("\n" + "=" * 80)
    print(f"COMPARATIVE PERFORMANCE MATRIX: '{args.watchlist}'")
    print("=" * 80)
    print(tabulate(df_results, headers="keys", showindex=False, tablefmt="grid", floatfmt=".2f"))
    print("\n")

    # Detailed trade-by-trade dump for baseline vs. selected models
    print("=" * 80)
    print("TRADE-BY-TRADE DETAIL MATRIX (BASELINE VS. NO PNL CAP VS. ADAPTIVE CAP)")
    print("=" * 80)
    
    # Match trades by symbol and entry_date
    trades_map = {}
    for t in baseline_trades:
        trades_map[(t.symbol, t.entry_date)] = {"baseline": t}
    for t in nocap_trades:
        if (t.symbol, t.entry_date) in trades_map:
            trades_map[(t.symbol, t.entry_date)]["no_cap"] = t
    for t in adaptive_trades:
        if (t.symbol, t.entry_date) in trades_map:
            trades_map[(t.symbol, t.entry_date)]["adaptive_cap"] = t
    for t in cwvap_guard_prt_nocap_trades:
        if (t.symbol, t.entry_date) in trades_map:
            trades_map[(t.symbol, t.entry_date)]["cwvap_guard_nocap"] = t
            
    trade_details = []
    for (sym, entry_date), m_dict in sorted(trades_map.items(), key=lambda x: x[0]):
        tb = m_dict.get("baseline")
        tnc = m_dict.get("no_cap")
        tad = m_dict.get("adaptive_cap")
        tcw = m_dict.get("cwvap_guard_nocap")
        
        trade_details.append({
            "Symbol": sym,
            "Entry Date": entry_date,
            "MFE%": f"{tb.mfe_pct:.2f}" if tb else "N/A",
            "Base PnL%": f"{tb.pnl_pct:+.2f}" if tb else "N/A",
            "Base Exit": (tb.exit_reason.value if hasattr(tb.exit_reason, "value") else str(tb.exit_reason)) if tb else "N/A",
            "NoCap PnL%": f"{tnc.pnl_pct:+.2f}" if tnc else "N/A",
            "NoCap Exit": (tnc.exit_reason.value if hasattr(tnc.exit_reason, "value") else str(tnc.exit_reason)) if tnc else "N/A",
            "CWGuard NoCap PnL%": f"{tcw.pnl_pct:+.2f}" if tcw else "N/A",
            "CWGuard NoCap Exit": (tcw.exit_reason.value if hasattr(tcw.exit_reason, "value") else str(tcw.exit_reason)) if tcw else "N/A"
        })
        
    df_trades = pd.DataFrame(trade_details)
    print(tabulate(df_trades, headers="keys", showindex=False, tablefmt="grid"))
    print("\n")


if __name__ == "__main__":
    main()
