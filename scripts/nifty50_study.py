"""
NIFTY 50 PSZ Recovery & Institutional Alignment Study
=====================================================

This script implements a high-conviction mean-reversion study targeting the "Institutional Floor" 
of NIFTY 50 stocks. It identifies trade setups using a 9-gate filtering process that combines 
price exhaustion (PSZ), institutional trend (CTS), and cohort alignment (CWC).

The 9-Gate Filtering Process:
-----------------------------
1. Sustained Exhaustion: Previous 3 bars (LB) must have PSZ <= -0.30 (Threshold).
2. Inflection Point: Signal bar PSZ must cross above (Threshold + Delta 0.01).
3. Price Displacement: Close price must be below CWVAP (ensures recovery vs mature trend).
4. Momentum Acceleration: PSZ Velocity (psz_v) must be strictly increasing over 3 bars (psz_v_lookback).
5. Velocity Delta: Each acceleration step must meet a minimum delta (0.01).
6. Institutional Dislocation: CTS must be <= adaptive cts_buy_threshold.
7. Institutional Improvement: CTS Slope must be accelerating (cts_slope > prev_cts_slope).
8. Contrarian Guard: CTS Slope must be negative (entering into a falling institutional trend).
9. Institutional Alignment: CWC Slope must be positive (delivery cohorts starting to align).

Exit Logic:
-----------
- Primary Target: Price crosses CWVAP, then exit when PSZ falls below 0.20.
- Profit Cap: Optional PnL % cap (default None).

Empirical Results (NIFTY 50 Full History):
------------------------------------------
- Win Rate: 72% - 76%
- Median PnL: 1.56% - 2.30%
- Average PnL: 1.32% - 2.86%
- Institutional Confirmation: Winners show significantly higher CWC (0.74) vs Losers (0.49).
"""

import sqlite3
import pandas as pd
import numpy as np
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from src.divergence_engine.engine import DivergenceEngine

DB_PATH = Path("liquidity_monitor.db")

def get_watchlist_symbols(name: str) -> list[str]:
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return []
    db = sqlite3.connect(str(DB_PATH))
    row = db.execute("SELECT id FROM watchlists WHERE name = ?", (name,)).fetchone()
    if not row:
        avail = [r[0] for r in db.execute("SELECT name FROM watchlists ORDER BY name").fetchall()]
        db.close()
        print(f"Watchlist '{name}' not found. Available: {avail}")
        return []
    symbols = [
        r[0] for r in db.execute(
            "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY display_order",
            (row[0],),
        ).fetchall()
    ]
    db.close()
    return symbols

def run_study(watchlist_name: str = "NIFTY 50", lookback_bars: int = 3, psz_threshold: float = -0.30, delta: float = 0.01, psz_v_lookback: int = 3, psz_v_delta: float = 0.01, pnl_cap: float = None, start_date: str = None, end_date: str = None):
    print(f"\n--- Running Study: Watchlist={watchlist_name}, LB={lookback_bars}, TH={psz_threshold}, Delta={delta}, PSZ_V_LB={psz_v_lookback}, PSZ_V_Delta={psz_v_delta}, PNL_Cap={pnl_cap}, Dates={start_date or 'ALL'} to {end_date or 'ALL'} ---")
    
    symbols = get_watchlist_symbols(watchlist_name)
    if not symbols:
        # Fallback to some common symbols if "NIFTY 50" was requested but empty
        if watchlist_name == "NIFTY 50":
            symbols = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "BHARTIARTL", "ITC", "SBIN", "LICI", "L&T"]
            print(f"Using fallback symbols: {symbols}")
        else:
            print(f"No symbols found for watchlist '{watchlist_name}'.")
            return

    all_setups = []

    for symbol in symbols:
        print(f"Analyzing {symbol}...")
        try:
            engine = DivergenceEngine(ticker=symbol, start_date=start_date, end_date=end_date)
            result = engine.run()
            df = result.ledger
            
            if df.empty:
                continue
                
            records = df.to_dict("records")
            n = len(records)
            
            start_idx = max(lookback_bars, psz_v_lookback)
            i = start_idx
            while i < n - 1:
                row = records[i]
                psz_now = row.get("price_slope_z", 0)
                psz_prev = records[i-1].get("price_slope_z", 0)
                
                # ENTRY CONDITIONS:
                # 1. Signal bar crosses above (threshold + delta)
                is_inflection = psz_now >= (psz_threshold + delta) and psz_prev <= psz_threshold
                
                # 2. Previous N bars were ALL <= threshold (baseline exhaustion)
                was_deep = all(records[i-j].get("price_slope_z", 0) <= psz_threshold for j in range(1, lookback_bars + 1))
                
                # 3. Price must be below CWVAP
                is_below_cwvap = row.get("close", 0) < row.get("cwvap", 0)
                
                # 4. Velocity must be increasing over the psz_v_lookback period with minimum delta
                psz_v_vals = [records[i-j].get("psz_v", 0) for j in range(psz_v_lookback + 1)]
                is_accelerating = all((psz_v_vals[j] - psz_v_vals[j+1]) >= psz_v_delta for j in range(psz_v_lookback))

                # 6. CTS must be at or below buy threshold
                cts_now = row.get("cts", 0)
                cts_bt = row.get("cts_buy_threshold", 0)
                is_cts_buy = cts_now <= cts_bt

                # 7. CTS Slope must be accelerating (cts_slope > prev_cts_slope)
                cts_s_now = row.get("cts_slope", 0)
                cts_s_prev = records[i-1].get("cts_slope", 0)
                is_cts_accel = cts_s_now > cts_s_prev

                # 8. CTS Slope must be negative (contrarian guard)
                is_cts_neg = cts_s_now < 0

                # 9. CWC Slope must be rising (institutional alignment guard)
                cwc_s_now = row.get("cwc_slope", 0)
                is_cwc_rising = cwc_s_now > 0
                
                if is_inflection and was_deep and is_below_cwvap and is_accelerating and is_cts_buy and is_cts_accel and is_cts_neg and is_cwc_rising:
                    entry_idx = i + 1 
                    if entry_idx >= n:
                        break
                        
                    entry_bar = records[entry_idx]
                    entry_price = entry_bar.get("open") or entry_bar.get("close")
                    if not entry_price:
                        i += 1
                        continue
                                        # CYCLE TRACKING
                    has_crossed_cwvap = False
                    exit_idx = -1
                    max_price = entry_price
                    rdv_sz_start = row.get("rdv_slope_z", 0)
                    rdv_sz_post_rise = False
                    
                    # CWVAP distance on signal day
                    cwvap_signal = row.get("cwvap", entry_price)
                    cwvap_dist_signal = (row.get("close", entry_price) - cwvap_signal) / cwvap_signal * 100.0
                    psz_v_signal = row.get("psz_v", 0)
                    rsz_v_signal = row.get("rsz_v", 0)
                    cts_signal = row.get("cts", 0)
                    cts_buy_threshold_signal = row.get("cts_buy_threshold", 0)
                    cts_slope_signal = row.get("cts_slope", 0)
                    cwc_signal = row.get("cwc", 0)
                    cwc_slope_signal = row.get("cwc_slope", 0)
                    
                    for j in range(entry_idx + 1, n):
                        curr_bar = records[j]
                        curr_psz = curr_bar.get("price_slope_z", 0)
                        curr_close = curr_bar.get("close")
                        curr_cwvap = curr_bar.get("cwvap", 0)
                        curr_rdv_sz = curr_bar.get("rdv_slope_z", 0)
                        
                        if curr_close > max_price:
                            max_price = curr_close
                        
                        if curr_rdv_sz > rdv_sz_start + 0.1:
                            rdv_sz_post_rise = True
                            
                        # UNREALIZED PNL CHECK (CONFIGURABLE CAP)
                        if pnl_cap is not None:
                            unrealized_pnl = (curr_close / entry_price - 1) * 100
                            if unrealized_pnl >= pnl_cap:
                                exit_idx = j
                                break

                        if not has_crossed_cwvap:
                            if curr_close > curr_cwvap:
                                has_crossed_cwvap = True
                        
                        if has_crossed_cwvap:
                            if curr_psz < 0.20:
                                exit_idx = min(j + 1, n - 1)
                                break
                    
                    actual_exit_idx = exit_idx if exit_idx != -1 else n - 1
                    exit_bar = records[actual_exit_idx]
                    exit_price = exit_bar.get("open") or exit_bar.get("close")
                    
                    pnl_pct = (exit_price / entry_price - 1) * 100
                    mfe_pct = (max_price / entry_price - 1) * 100
                    
                    all_setups.append({
                        "symbol": symbol,
                        "entry_date": row.get("date"),
                        "exit_date": exit_bar.get("date"),
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "psz": psz_now,
                        "cwvap_dist": cwvap_dist_signal,
                        "psz_v": psz_v_signal,
                        "rsz_v": rsz_v_signal,
                        "cts": cts_signal,
                        "cts_buy_threshold": cts_buy_threshold_signal,
                        "cts_slope": cts_slope_signal,
                        "cwc": cwc_signal,
                        "cwc_slope": cwc_slope_signal,
                        "mfe_pct": mfe_pct,
                        "pnl_pct": pnl_pct,
                        "duration": actual_exit_idx - entry_idx,
                        "crossed_cwvap": has_crossed_cwvap,
                        "rdv_sz_post_rise": rdv_sz_post_rise
                    })
                    
                    i = actual_exit_idx
                else:
                    i += 1

        except Exception as e:
            print(f"Error processing {symbol}: {e}")

    if not all_setups:
        print("No setups found for this configuration.")
        return

    results_df = pd.DataFrame(all_setups)
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    safe_name = watchlist_name.lower().replace(" ", "_")
    results_df.to_csv(output_dir / f"{safe_name}_psz_study_lb{lookback_bars}_th{abs(psz_threshold)}.csv", index=False)
    
    print(f"\n--- Statistics (LB={lookback_bars}, TH={psz_threshold}) ---")
    print(f"Total Cycles: {len(results_df)}")
    print(f"Avg PnL %: {results_df['pnl_pct'].mean():.2f}%")
    print(f"Median PnL %: {results_df['pnl_pct'].median():.2f}%")
    print(f"Win Rate: {(results_df['pnl_pct'] > 0).mean()*100:.2f}%")
    
    # CWC Correlation
    winners = results_df[results_df["pnl_pct"] > 0]
    losers = results_df[results_df["pnl_pct"] <= 0]
    
    print(f"\n--- CWC Correlation Analysis ---")
    if not winners.empty:
        print(f"Avg CWC (Winners): {winners['cwc'].mean():.3f}")
        print(f"Avg CWC Slope (Winners): {winners['cwc_slope'].mean():.4f}")
    if not losers.empty:
        print(f"Avg CWC (Losers): {losers['cwc'].mean():.3f}")
        print(f"Avg CWC Slope (Losers): {losers['cwc_slope'].mean():.4f}")

    # Hypothetical Filter: CWC > 0.1 (low positive threshold)
    cwc_thresh = 0.1
    filtered = results_df[results_df["cwc"] > cwc_thresh]
    if not filtered.empty:
        print(f"\n--- Statistics with CWC > {cwc_thresh} Filter ---")
        print(f"Total Cycles: {len(filtered)} (Filtered out {len(results_df) - len(filtered)})")
        print(f"Win Rate: {(filtered['pnl_pct'] > 0).mean()*100:.2f}%")
        print(f"Avg PnL %: {filtered['pnl_pct'].mean():.2f}%")
        print(f"Median PnL %: {filtered['pnl_pct'].median():.2f}%")
    
    # Hypothetical Filter: CWC Slope > 0 (rising coherence)
    slope_filtered = results_df[results_df["cwc_slope"] > 0]
    if not slope_filtered.empty:
        print(f"\n--- Statistics with CWC Slope > 0 Filter ---")
        print(f"Total Cycles: {len(slope_filtered)} (Filtered out {len(results_df) - len(slope_filtered)})")
        print(f"Win Rate: {(slope_filtered['pnl_pct'] > 0).mean()*100:.2f}%")
        print(f"Avg PnL %: {slope_filtered['pnl_pct'].mean():.2f}%")
        print(f"Median PnL %: {slope_filtered['pnl_pct'].median():.2f}%")

    print("\nTop Performing Cycles:")
    print(results_df.sort_values("pnl_pct", ascending=False).head(5)[["symbol", "entry_date", "pnl_pct", "psz", "cts", "duration"]])

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Watchlist PSZ Study CLI")
    parser.add_argument("--watchlist", type=str, default="NIFTY 50", help="Watchlist name to scan")
    parser.add_argument("--lookback", type=int, default=3, help="Number of baseline bars below threshold")
    parser.add_argument("--threshold", type=float, default=-0.30, help="PSZ threshold for deep trough")
    parser.add_argument("--delta", type=float, default=0.01, help="Delta above threshold to trigger signal bar")
    parser.add_argument("--psz-v-lookback", type=int, default=3, help="Number of bars psz_v must be increasing")
    parser.add_argument("--psz-v-delta", type=float, default=0.01, help="Minimum acceleration delta for psz_v")
    parser.add_argument("--pnl-cap", type=float, default=None, help="PNL % cap for exit (None to disable)")
    parser.add_argument("--start-date", type=str, default=None, help="Start date (YYYY-MM-DD), default None for all data")
    parser.add_argument("--end-date", type=str, default=None, help="End date (YYYY-MM-DD), default None for all data")
    
    args = parser.parse_args()
    run_study(
        watchlist_name=args.watchlist,
        lookback_bars=args.lookback, 
        psz_threshold=args.threshold, 
        delta=args.delta, 
        psz_v_lookback=args.psz_v_lookback,
        psz_v_delta=args.psz_v_delta,
        pnl_cap=args.pnl_cap,
        start_date=args.start_date,
        end_date=args.end_date
    )
