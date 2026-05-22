#!/usr/bin/env python3
"""CWC and CTS Exit Optimization Study.

Simulates and evaluates exit rules on the Universal Cross entry signals during
the TEST period (2024-01-01 to present) on NIFTY 50 watchlist.
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
    allow_prt_suppression: bool = False,
    cwc_slope_suppress_thr: float | None = None
) -> tuple[str | None, int]:
    """Custom CWVAP Guard that supports early release or bypass of suppression based on cwc_slope."""
    from src.trading.signals.savgol_cts.state import SavgolCTSExitState
    from src.trading.signals.enums import ExitReason
    
    st = SavgolCTSExitState.from_int(state_val)
    st.suppressed_this_bar = False

    if res is not None:
        # Loss prevention rules bypass CWVAP suppression
        bypass_reasons = [ExitReason.CWVAP_LOST, ExitReason.GAP_DOWN_LOSS, ExitReason.HARD_STOP, ExitReason.PNL_CAP]
        if not allow_prt_suppression:
            bypass_reasons.append(ExitReason.PRT_ST_CROSS)
            
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
    cwc_slope = row.get("cwc_slope", np.nan)

    if np.isnan(close) or np.isnan(cwvap):
        return res, st.to_int()

    # --- Above CWVAP ---
    if close > cwvap:
        # Early Release / Bypass Suppression based on cwc_slope
        if cwc_slope_suppress_thr is not None and not np.isnan(cwc_slope) and cwc_slope < cwc_slope_suppress_thr:
            # If an exit was proposed, release it early!
            if res is not None or st.exit_suppressed:
                st.suppressed_this_bar = False
                st.exit_suppressed = False
                final_res = res if res else "CWC_SLOPE_EARLY_RELEASE"
                return final_res, st.to_int()
            # If no exit was proposed, we could also force an early exit
            # but usually we want to release a suppressed exit or check if it signifies an exit.
            
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
    # Standard release if below CWVAP
    if st.exit_suppressed:
        st.suppressed_this_bar = False
        st.exit_suppressed = False
        final_res = res if res else ExitReason.CWVAP_LOST
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
    
    # Disable ATR Chandelier for this exercise
    exit_cfg.universal_cross.chandelier_stop_enabled = False

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

            # -------------------------------------------------------------
            # Extract features for exit evaluation
            # -------------------------------------------------------------
            prt = row.get("prt", np.nan)
            prev_prt = prev.get("prt", np.nan)
            prt_st = row.get("prt_sell_threshold", np.nan)
            prev_prt_st = prev.get("prt_sell_threshold", np.nan)
            
            cts = row.get("cts", np.nan)
            prev_cts = prev.get("cts", np.nan)
            cts_st = row.get("cts_sell_threshold", np.nan)
            prev_cts_st = prev.get("cts_sell_threshold", np.nan)
            
            cwc = row.get("cwc", np.nan)
            prev_cwc = prev.get("cwc", np.nan)
            
            cwc_slope = row.get("cwc_slope", np.nan)
            prev_cwc_slope = prev.get("cwc_slope", np.nan)

            # Determine path-specific exit trigger based on candidate model
            triggered_exit = None
            
            # 1. Baseline Exit: PRT crossing PRT ST down
            is_prt_cross = (not np.isnan(prt) and not np.isnan(prev_prt) and 
                            not np.isnan(prt_st) and not np.isnan(prev_prt_st) and
                            prev_prt >= prev_prt_st and prt < prt_st)
            
            # 2. CTS Cross ST: CTS crossing CTS ST down
            is_cts_cross = (not np.isnan(cts) and not np.isnan(prev_cts) and
                            not np.isnan(cts_st) and not np.isnan(prev_cts_st) and
                            prev_cts >= prev_cts_st and cts < cts_st)
                            
            # 3. CWC Slope negative: cwc_slope < 0
            is_cwc_slope_neg = (not np.isnan(cwc_slope) and cwc_slope < 0.0)
            
            # 4. CWC negative: cwc < 0
            is_cwc_neg = (not np.isnan(cwc) and cwc < 0.0)
            
            # Determine path-specific exit reason
            if model_name == "baseline":
                if is_prt_cross:
                    triggered_exit = ExitReason.PRT_ST_CROSS
            elif model_name == "cts_st_cross":
                if is_cts_cross:
                    triggered_exit = ExitReason.ST_CROSS
            elif model_name == "cwc_slope_neg":
                if is_cwc_slope_neg:
                    triggered_exit = "CWC_SLOPE_NEG"
            elif model_name == "cwc_neg":
                if is_cwc_neg:
                    triggered_exit = "CWC_NEG"
            elif model_name == "prt_or_cts_st":
                if is_prt_cross:
                    triggered_exit = ExitReason.PRT_ST_CROSS
                elif is_cts_cross:
                    triggered_exit = ExitReason.ST_CROSS
            elif model_name == "cwc_slope_and_cwc_neg":
                if is_cwc_slope_neg and is_cwc_neg:
                    triggered_exit = "CWC_SLOPE_AND_NEG"
            elif model_name in ["cwc_slope_guard_0.0", "cwc_slope_guard_-0.01", "cwc_slope_guard_-0.02"]:
                # Normal baseline exit trigger (PRT cross)
                if is_prt_cross:
                    triggered_exit = ExitReason.PRT_ST_CROSS
            elif model_name == "optimized_combo":
                # Combined rule: PRT ST cross or CTS ST cross
                if is_prt_cross:
                    triggered_exit = ExitReason.PRT_ST_CROSS
                elif is_cts_cross:
                    triggered_exit = ExitReason.ST_CROSS

            # Hard stop (always active)
            if exit_cfg.universal_cross.hard_stop_enabled and pnl_pct <= -exit_cfg.universal_cross.hard_stop_pct:
                triggered_exit = ExitReason.HARD_STOP

            # Apply CWVAP Guard with custom suppression release settings
            cwc_slope_thr = None
            if model_name == "cwc_slope_guard_0.0":
                cwc_slope_thr = 0.0
            elif model_name == "cwc_slope_guard_-0.01":
                cwc_slope_thr = -0.01
            elif model_name == "cwc_slope_guard_-0.02":
                cwc_slope_thr = -0.02
            elif model_name == "optimized_combo":
                cwc_slope_thr = -0.01  # Sweet spot early release

            from src.trading.signals.savgol_cts.state import SavgolCTSExitState
            st = SavgolCTSExitState.from_int(delivery_bad_count)
            if not np.isnan(close) and not np.isnan(cw) and close > cw:
                st.price_above_cwvap = True

            reason, delivery_bad_count = apply_cwvap_guard_custom(
                row, trade, triggered_exit, st.to_int(),
                exit_cfg, records, i, tag=trade.entry_tag,
                allow_prt_suppression=True,
                cwc_slope_suppress_thr=cwc_slope_thr
            )
            
            if reason:
                pending_exit_reason = str(reason)

        elif pending_signal is not None:
            sig = pending_signal
            pending_signal = None
            atr = row.get("atr_20", 0)
            if atr <= 0 or np.isnan(atr):
                continue
            psz_now = row.get("price_slope_z", np.nan)
            
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
    parser = argparse.ArgumentParser(description="Universal Cross Exit Optimization Study with CWC")
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

    # 1. Baseline: PRT Crossed ST down (Chandelier Disabled)
    print("Simulating Model 1: Baseline (PRT ST Crossover Only, Chandelier Disabled)...")
    baseline_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "baseline")
    
    # 2. CTS Crossed ST down
    print("Simulating Model 2: CTS Crossed ST down...")
    cts_st_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "cts_st_cross")
    
    # 3. CWC Slope Negative (< 0.0)
    print("Simulating Model 3: CWC Slope Negative (< 0.0)...")
    cwc_slope_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "cwc_slope_neg")
    
    # 4. CWC Negative (< 0.0)
    print("Simulating Model 4: CWC Negative (< 0.0)...")
    cwc_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "cwc_neg")
    
    # 5. Combined: PRT ST Cross OR CTS ST Cross
    print("Simulating Model 5: PRT ST Crossover OR CTS ST Crossover...")
    prt_or_cts_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "prt_or_cts_st")
    
    # 6. Combined: CWC Slope AND CWC Negative
    print("Simulating Model 6: CWC Slope AND CWC Negative...")
    cwc_slope_and_cwc_neg_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "cwc_slope_and_cwc_neg")
    
    # 7. CWVAP Guard CWC Slope early release (Sweep: 0.0, -0.01, -0.02)
    print("Simulating Model 7a: CWVAP Guard CWC Slope Early Release (cwc_slope < 0.0)...")
    cwc_slope_guard_00 = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "cwc_slope_guard_0.0")
    
    print("Simulating Model 7b: CWVAP Guard CWC Slope Early Release (cwc_slope < -0.01)...")
    cwc_slope_guard_01 = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "cwc_slope_guard_-0.01")
    
    print("Simulating Model 7c: CWVAP Guard CWC Slope Early Release (cwc_slope < -0.02)...")
    cwc_slope_guard_02 = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "cwc_slope_guard_-0.02")

    # 8. Optimized Combo: PRT ST OR CTS ST + CWVAP Guard early release on cwc_slope < -0.01
    print("Simulating Model 8: Optimized Combo (PRT ST OR CTS ST + CWC Slope < -0.01 Guard Early Release)...")
    optimized_combo_trades = run_model(symbols, args.start_date, args.end_date, entry_cfg, exit_cfg, signal, "optimized_combo")

    # Summaries
    models = {
        "Baseline (PRT ST)": baseline_trades,
        "CTS ST Cross": cts_st_trades,
        "CWC Slope < 0": cwc_slope_trades,
        "CWC < 0": cwc_trades,
        "PRT or CTS ST": prt_or_cts_trades,
        "CWC Slope & CWC < 0": cwc_slope_and_cwc_neg_trades,
        "CWGuard CWC_S < 0.0": cwc_slope_guard_00,
        "CWGuard CWC_S < -0.01": cwc_slope_guard_01,
        "CWGuard CWC_S < -0.02": cwc_slope_guard_02,
        "Optimized Combo": optimized_combo_trades
    }

    results = []
    for name, trs in models.items():
        summary = summarize_model(trs)
        summary["Model Name"] = name
        results.append(summary)

    df_results = pd.DataFrame(results)[[
        "Model Name", "Count", "WinRate%", "AvgPnL%", "MedianPnL%", "PF", "AvgMFE%", "AvgMAE%", "AvgBars", "LeftOnTable%"
    ]]
    
    print("\n" + "=" * 80)
    print(f"CWC & CTS COMPARATIVE PERFORMANCE MATRIX (TEST Period): '{args.watchlist}'")
    print("=" * 80)
    print(tabulate(df_results, headers="keys", showindex=False, tablefmt="grid", floatfmt=".2f"))
    print("\n")

    # Target specific setups of interest mentioned by the user
    target_stocks = ["POWERGRID", "COALINDIA", "KOTAKBANK", "MAXHEALTH", "HCLTECH"]
    
    print("=" * 80)
    print("TARGET SPECIFIC TRADES CASE STUDY COMPARISON")
    print("=" * 80)
    
    study_trades = []
    for name, trs in models.items():
        if name in ["Baseline (PRT ST)", "Optimized Combo", "CWGuard CWC_S < -0.01", "PRT or CTS ST"]:
            for t in trs:
                if t.symbol in target_stocks:
                    study_trades.append({
                        "Model": name,
                        "Symbol": t.symbol,
                        "Entry Date": t.entry_date,
                        "Exit Date": t.exit_date,
                        "MFE%": f"{t.mfe_pct:.2f}",
                        "MAE%": f"{t.mae_pct:.2f}",
                        "PnL%": f"{t.pnl_pct:+.2f}",
                        "Reason": (t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)),
                        "Bars": t.duration
                    })
                    
    df_study = pd.DataFrame(study_trades)
    if not df_study.empty:
        df_study = df_study.sort_values(by=["Symbol", "Entry Date", "Model"])
        print(tabulate(df_study, headers="keys", showindex=False, tablefmt="grid"))
    else:
        print("No target stock trades found in the simulation period.")
    print("\n")


if __name__ == "__main__":
    main()
