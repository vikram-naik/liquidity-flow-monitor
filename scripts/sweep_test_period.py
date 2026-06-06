#!/usr/bin/env python3
import sys
import os
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade, SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState
from src.trading.signals.savgol_cts.symbol_configs import get_symbol_entry_config, get_symbol_exit_config
from src.trading.signals.enums import ExitReason, EntryTag
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross
from src.trading.signals.savgol_cts.exits.cwvap_guard import apply_cwvap_guard
from src.database import DB_PATH
from scripts.test_reclaim_logic import get_watchlist_symbols

# Custom exit checker with peak_pnl threshold
def check_exit_sweep(
    row: dict,
    prev_row: dict,
    trade: Trade,
    peak_close: float,
    bars_held: int,
    delivery_bad_count: int,
    cwvap_values: list[float],
    cfg: SavgolCTSExitConfig,
    records: list[dict],
    idx: int,
    reclaim_mode: str,
    atr_mult: float,
    peak_pnl_threshold: float
) -> tuple[str | None, int]:
    
    st = SavgolCTSExitState.from_int(delivery_bad_count)
    tag = trade.entry_tag if trade is not None else ""
    close = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)
    va_high = row.get("va_high", np.nan)

    if not np.isnan(close) and not np.isnan(cwvap) and close > cwvap:
        st.price_above_cwvap = True

    # Reclaim check
    if st.exit_suppressed:
        prt = row.get("prt", np.nan)
        prt_st = row.get("prt_sell_threshold", np.nan)
        
        if reclaim_mode == "reclaim_prt":
            if not any(np.isnan(x) for x in [prt, prt_st]) and prt >= prt_st:
                st.exit_suppressed = False
                st.suppressed_this_bar = False
                delivery_bad_count = st.to_int()

    # 1. Run baseline path-specific exits (Universal Cross)
    uc_cfg = cfg.universal_cross
    exit_reason, state_val = exit_universal_cross(
        row, prev_row, trade, peak_close, bars_held, delivery_bad_count,
        uc_cfg, records, idx
    )
    st = SavgolCTSExitState.from_int(state_val)

    # 2. Run baseline CWVAP guard
    final_reason, st_val = apply_cwvap_guard(
        row, trade, exit_reason, st.to_int(),
        cfg, records, idx, tag
    )
    st = SavgolCTSExitState.from_int(st_val)

    # 3. Apply the ATR trailing stop (using current ATR, only if peak_pnl >= threshold)
    if final_reason is None and st.exit_suppressed:
        peak_pnl = (peak_close / trade.entry_price - 1.0) * 100.0
        if peak_pnl >= peak_pnl_threshold:
            atr = row.get("atr_20", np.nan)
            if not np.isnan(atr) and atr > 0 and close < (peak_close - atr_mult * atr):
                final_reason = f"ExitReason.SUPPRESSED_MOD_ATR_{atr_mult}"
                st.exit_suppressed = False
                st.suppressed_this_bar = False
                st_val = st.to_int()

    return final_reason, st_val

def simulate_trades_sweep(
    ticker: str, df: pd.DataFrame,
    entry_cfg, exit_cfg, signal, reclaim_mode: str,
    atr_mult: float, peak_pnl_threshold: float
) -> list[dict]:
    records = df.to_dict("records")
    n = len(records)
    trades = []
    in_trade = False
    trade = None
    peak_close = 0.0
    delivery_bad_count = 0
    cwvap_values: list[float] = []
    pending_signal: dict | None = None
    pending_exit_reason: str | None = None

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
            pnl = (exit_price / trade.entry_price - 1) * 100.0
            
            trades.append({
                "symbol": ticker,
                "entry_date": trade.entry_date,
                "entry_price": trade.entry_price,
                "exit_date": str(row.get("date", ""))[:10],
                "exit_price": round(exit_price, 2),
                "exit_reason": pending_exit_reason,
                "pnl_pct": round(pnl, 2),
                "mfe_pct": round(trade.mfe_pct, 2),
                "mae_pct": round(trade.mae_pct, 2),
                "duration": i - trade.entry_idx
            })
            
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

            if reclaim_mode == "baseline":
                reason, delivery_bad_count = signal.check_exit(
                    row, prev, trade, peak_close, bars_held,
                    delivery_bad_count, cwvap_values, exit_cfg,
                    records, i,
                )
            else:
                reason, delivery_bad_count = check_exit_sweep(
                    row, prev, trade, peak_close, bars_held,
                    delivery_bad_count, cwvap_values, exit_cfg,
                    records, i, reclaim_mode, atr_mult, peak_pnl_threshold
                )

            if reason:
                pending_exit_reason = reason

        elif pending_signal is not None:
            sig = pending_signal
            pending_signal = None
            atr = row.get("atr_20", 0)
            if atr <= 0 or np.isnan(atr):
                continue
            psz_now = row.get("price_slope_z", np.nan)
            trade = Trade(
                symbol=ticker,
                entry_date=str(row.get("date", ""))[:10],
                entry_price=close,
                entry_idx=i,
                atr_at_entry=atr,
                conviction_score=sig.get("details", {}).get("score", 0),
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
        pnl = (last.get("close", trade.entry_price) / trade.entry_price - 1) * 100.0
        trades.append({
            "symbol": ticker,
            "entry_date": trade.entry_date,
            "entry_price": trade.entry_price,
            "exit_date": str(last.get("date", ""))[:10],
            "exit_price": round(last.get("close", trade.entry_price), 2),
            "exit_reason": "End of Data (Open Trade)",
            "pnl_pct": round(pnl, 2),
            "mfe_pct": round(trade.mfe_pct, 2),
            "mae_pct": round(trade.mae_pct, 2),
            "duration": n - 1 - trade.entry_idx
        })

    return trades

def evaluate_sweep(symbols, start_date, end_date, reclaim_mode, atr_mult, peak_pnl_threshold):
    signal = SignalFactory.get_signal("savgol_cts")
    all_trades = []
    
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            result = engine.run()
            
            entry_cfg = get_symbol_entry_config(sym)
            exit_cfg = get_symbol_exit_config(sym)
            
            trades = simulate_trades_sweep(sym, result.ledger, entry_cfg, exit_cfg, signal, reclaim_mode, atr_mult, peak_pnl_threshold)
            period_trades = [t for t in trades if start_date <= t["entry_date"] <= end_date]
            all_trades.extend(period_trades)
        except Exception:
            pass
            
    if not all_trades:
        return {}
        
    df = pd.DataFrame(all_trades)
    total = len(df)
    winners = (df["pnl_pct"] > 0).sum()
    win_rate = winners / total * 100
    avg_pnl = df["pnl_pct"].mean()
    median_pnl = df["pnl_pct"].median()
    avg_left = (df["mfe_pct"] - df["pnl_pct"]).mean()
    
    gross_win = sum(t["pnl_pct"] for t in all_trades if t["pnl_pct"] > 0)
    gross_loss = abs(sum(t["pnl_pct"] for t in all_trades if t["pnl_pct"] <= 0))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
    
    return {
        "trades": total,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "median_pnl": median_pnl,
        "profit_factor": profit_factor,
        "avg_left": avg_left
    }

def main():
    watchlist_name = "NIFTY 50"
    symbols = get_watchlist_symbols(watchlist_name)
    
    test_start, test_end = "2024-01-01", "2026-06-06"
    
    # Grid search parameters
    atr_mults = [1.75, 2.0, 2.25, 2.5, 2.75, 3.0]
    peak_thresholds = [3.0, 4.0, 5.0, 6.0]
    
    print("Running baseline simulation for TEST period...")
    base_res = evaluate_sweep(symbols, test_start, test_end, "baseline", 2.0, 4.0)
    
    results = []
    print(f"Sweeping parameters over TEST period ({test_start} to {test_end})...")
    
    for thr in peak_thresholds:
        for mult in atr_mults:
            print(f"Evaluating: Peak PnL >= {thr}%, ATR Mult: {mult}...")
            res = evaluate_sweep(symbols, test_start, test_end, "reclaim_prt", mult, thr)
            if res:
                results.append({
                    "threshold": thr,
                    "multiplier": mult,
                    "trades": res["trades"],
                    "win_rate": res["win_rate"],
                    "avg_pnl": res["avg_pnl"],
                    "median_pnl": res["median_pnl"],
                    "profit_factor": res["profit_factor"],
                    "avg_left": res["avg_left"]
                })
                
    # Sort results by Profit Factor
    df_res = pd.DataFrame(results)
    df_res = df_res.sort_values(by="profit_factor", ascending=False)
    
    print("\n" + "=" * 100)
    print("                     GRID SEARCH COMPARISON ON TEST PERIOD (2024-2026)")
    print("=" * 100)
    print(f"Baseline: Trades={base_res['trades']}, Win Rate={base_res['win_rate']:.2f}%, Avg PnL={base_res['avg_pnl']:+.2f}%, Med PnL={base_res['median_pnl']:+.2f}%, Profit Factor={base_res['profit_factor']:.2f}, Avg Left={base_res['avg_left']:.2f}%\n")
    
    print(tabulate(df_res.head(15), headers="keys", tablefmt="simple", showindex=False, floatfmt=".2f"))
    print("=" * 100)

if __name__ == "__main__":
    main()
