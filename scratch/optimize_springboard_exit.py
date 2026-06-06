import sys
import os
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend
from scratch.analyze_trends import parse_trends, clean_date_str

def check_springboard_custom(row, prev_row, records, idx, cap_thresh, cap_lookback, rp_max, cwc_slope_min, psz_v_min, spearman_min):
    if idx < cap_lookback:
        return False

    rp_63 = row.get("range_pos_63", np.nan)
    if np.isnan(rp_63) or rp_63 > rp_max:
        return False

    capitulated = False
    for k in range(max(0, idx - cap_lookback + 1), idx + 1):
        c = records[k].get("cts", np.nan)
        if not np.isnan(c) and c <= cap_thresh:
            capitulated = True
            break
            
    if not capitulated:
        return False

    cts = row.get("cts", np.nan)
    prev_cts = prev_row.get("cts", np.nan)
    if np.isnan(cts) or np.isnan(prev_cts):
        return False
        
    if cts <= prev_cts:
        return False
        
    if cts == -1.0:
        return False

    cwc_slope = row.get("cwc_slope", np.nan)
    if not np.isnan(cwc_slope) and cwc_slope <= cwc_slope_min:
        return False

    psz_v = row.get("psz_v", np.nan)
    if not np.isnan(psz_v) and psz_v <= psz_v_min:
        return False

    tps_5 = []
    for k in range(idx - 4, idx + 1):
        r = records[k]
        tp = (r.get("high", 0.0) + r.get("low", 0.0) + r.get("close", 0.0)) / 3.0
        tps_5.append(tp)
        
    if len(tps_5) >= 5:
        spearman_5 = evaluate_spearman_trend(tps_5)
        if spearman_5 <= spearman_min:
            return False
    else:
        return False

    return True

def run_springboard_backtest_with_exits(df, cap_thresh, cap_lookback, rp_max, cwc_slope_min, psz_v_min, spearman_min, 
                                       allow_prt_exit=True, allow_cts_exit=True, hard_stop_pct=8.0, max_hold=50):
    records = df.to_dict('records')
    n = len(records)
    trades = []
    in_trade = False
    entry_price = 0.0
    entry_idx = 0
    peak_close = 0.0
    
    for i in range(1, n):
        row = records[i]
        prev = records[i-1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue
            
        if in_trade:
            if close > peak_close:
                peak_close = close
            pnl_pct = (close / entry_price - 1.0) * 100.0
            
            # 1. Hard Stop
            if hard_stop_pct < 90.0 and pnl_pct <= -hard_stop_pct:
                trades.append(pnl_pct)
                in_trade = False
                continue
                
            # 2. Holding period limit
            if i - entry_idx >= max_hold:
                trades.append(pnl_pct)
                in_trade = False
                continue
                
            # 3. Exits based on signals
            cts = row.get("cts", 0.0)
            prev_cts = prev.get("cts", 0.0)
            cts_st = row.get("cts_sell_threshold", 0.0)
            prev_cts_st = prev.get("cts_sell_threshold", 0.0)
            
            prt = row.get("prt", 0.0)
            prev_prt = prev.get("prt", 0.0)
            prt_st = row.get("prt_sell_threshold", 0.0)
            prev_prt_st = prev.get("prt_sell_threshold", 0.0)
            
            exit_triggered = False
            
            # CTS Trail Exit
            if allow_cts_exit and prev_cts >= prev_cts_st and cts < cts_st:
                exit_triggered = True
                
            # PRT Trail Exit
            if allow_prt_exit and prev_prt >= prev_prt_st and prt < prt_st:
                if not (cts >= cts_st):
                    exit_triggered = True
                    
            if exit_triggered:
                trades.append(pnl_pct)
                in_trade = False
                
        else:
            qualifies = check_springboard_custom(
                row, prev, records, i,
                cap_thresh, cap_lookback, rp_max, cwc_slope_min, psz_v_min, spearman_min
            )
            if qualifies:
                if i + 1 < n:
                    next_row = records[i+1]
                    next_close = next_row.get("close", np.nan)
                    if not np.isnan(next_close):
                        in_trade = True
                        entry_price = next_close
                        entry_idx = i + 1
                        peak_close = next_close
                        
    return trades

def get_watchlist_symbols(name="NIFTY 50"):
    from src.database import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    symbols = [r[0] for r in conn.execute(
        "SELECT symbol FROM watchlist_items WHERE watchlist_id = (SELECT id FROM watchlists WHERE name = ?) ORDER BY display_order",
        (name,)
    ).fetchall()]
    conn.close()
    return symbols

def run_grid_for_period(cache, cap_thresh, cap_lookback, rp_max, cwc_slope_min, psz_v_min, spearman_min, 
                       start_date, end_date, label):
    results = []
    
    # We focus on the best candidates from the Train grid
    # PRT=False, CTS=False, HS in [8.0, 12.0, 99.0], Max Hold in [30, 40, 50]
    # And we also test with PRT=True or CTS=True for reference
    exit_grid = [
        # (allow_prt, allow_cts, hs_pct, max_hold)
        (False, False, 12.0, 50),
        (False, False, 99.0, 50),
        (False, False, 8.0, 50),
        (False, False, 12.0, 40),
        (False, False, 99.0, 40),
        (False, False, 8.0, 40),
        (True, False, 99.0, 50),
        (False, True, 99.0, 50),
    ]
    
    for allow_prt, allow_cts, hs_pct, max_hold in exit_grid:
        all_trades = []
        for sym, df in cache.items():
            df_bt = df[(df['date'] >= start_date) & (df['date'] <= end_date)].copy().reset_index(drop=True)
            if df_bt.empty:
                continue
            trades = run_springboard_backtest_with_exits(
                df_bt, cap_thresh, cap_lookback, rp_max, cwc_slope_min, psz_v_min, spearman_min,
                allow_prt_exit=allow_prt, allow_cts_exit=allow_cts, hard_stop_pct=hs_pct, max_hold=max_hold
            )
            all_trades.extend(trades)
            
        trade_count = len(all_trades)
        if trade_count > 0:
            win_rate = sum(1 for p in all_trades if p > 0) / trade_count * 100.0
            avg_pnl = np.mean(all_trades)
        else:
            win_rate, avg_pnl = 0.0, 0.0
            
        results.append({
            "PRT Exit": allow_prt,
            "CTS Exit": allow_cts,
            "HS%": hs_pct if hs_pct < 90 else "Disabled",
            "Max Hold": max_hold,
            "Trades": trade_count,
            "Win Rate": f"{win_rate:.1f}%",
            "Avg P&L%": avg_pnl
        })
        
    df_res = pd.DataFrame(results)
    df_res = df_res.sort_values(by="Avg P&L%", ascending=False)
    
    print("\n" + "="*80)
    print(f"EXIT GRID RESULTS FOR {label} ({start_date} to {end_date})")
    print("="*80)
    print(tabulate(df_res, headers="keys", tablefmt="grid", floatfmt=".3f"))

def main():
    symbols = get_watchlist_symbols("NIFTY 50")
    
    print("Loading stock data...")
    cache = {}
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            cache[sym] = res.ledger.copy()
        except Exception as e:
            pass
            
    print(f"Cached {len(cache)} symbols.")
    
    # SpringBoard entry parameters (Original)
    cap_thresh = -0.75
    cap_lookback = 10
    rp_max = 0.40
    cwc_slope_min = -0.05
    psz_v_min = -0.16
    spearman_min = -0.95
    
    # Run for Train
    run_grid_for_period(cache, cap_thresh, cap_lookback, rp_max, cwc_slope_min, psz_v_min, spearman_min, 
                        "2019-01-01", "2023-12-31", "TRAIN")
                        
    # Run for Test
    run_grid_for_period(cache, cap_thresh, cap_lookback, rp_max, cwc_slope_min, psz_v_min, spearman_min, 
                        "2024-01-01", "2026-06-05", "TEST")

if __name__ == "__main__":
    main()
