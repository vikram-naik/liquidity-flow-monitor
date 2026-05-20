import argparse
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.signal import SavgolCTSSignal
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.savgol_cts.entries.utils import is_flattish_line_adaptive
from src.database import DB_PATH, get_user_setting
import sqlite3

def get_watchlist_symbols(name: str) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    query = """
        SELECT i.symbol 
        FROM watchlist_items i 
        JOIN watchlists w ON i.watchlist_id = w.id 
        WHERE w.name = ?
    """
    df = pd.read_sql(query, conn, params=(name,))
    conn.close()
    return df["symbol"].tolist()

def run_study(watchlist="NIFTY 50", period_start="2024-01-01"):
    symbols = get_watchlist_symbols(watchlist)
    print(f"Studying FAS Flatness on {len(symbols)} symbols from {watchlist} starting {period_start}...")
    
    all_trades = []
    
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SavgolCTSSignal()
    
    # We'll need the threshold and award to simulate rejection impact
    threshold = float(get_user_setting("ml_guard_threshold", "1.55"))
    award = threshold * 0.3
    
    # Convert period_start to timestamp for safe comparison
    start_dt = pd.to_datetime(period_start)
    
    for symbol in symbols:
        try:
            # print(f"  Processing {symbol}...")
            engine = DivergenceEngine(symbol)
            res_engine = engine.run()
            df = res_engine.ledger
            if df.empty: continue
            
            # Run standard simulation to get baseline trades
            res = signal.tag_signals(df, entry_cfg, exit_cfg)
            
            records = res.to_dict("records")
            for i in range(2, len(records)):
                if records[i]["entry_signal"] > 0:
                    # Signal bar is i. Evaluation uses records[i], records[i-1], etc.
                    
                    if i + 1 >= len(records):
                        continue
                        
                    # 1. Evaluate FAS Flatness
                    f1 = records[i-2].get("fas", 0)
                    f2 = records[i-1].get("fas", 0)
                    f3 = records[i].get("fas", 0)
                    fas_lookback = [records[j].get("fas", 0) for j in range(max(0, i-30), i + 1)]
                    fas_flat_res = is_flattish_line_adaptive(f1, f2, f3, lookback_window_data=fas_lookback, sensitivity=0.05)
                    is_fas_flat = fas_flat_res["is_valid"]
                    
                    # 2. Find matching trade to get PnL
                    # Entry is i+1. Search forward for exit.
                    entry_row = records[i+1]
                    entry_date = pd.to_datetime(entry_row["date"])
                    entry_price = entry_row["open"]
                    
                    pnl = None
                    duration = 0
                    mfe = 0
                    mae = 0
                    
                    if entry_date < start_dt:
                        continue

                    # Search for the exit reason/signal starting from i+2
                    for k in range(i+1, len(records)):
                        close_k = records[k]["close"]
                        high_k = records[k]["high"]
                        low_k = records[k]["low"]
                        
                        # Track MFE/MAE
                        trade_high = (high_k / entry_price - 1) * 100
                        trade_low = (low_k / entry_price - 1) * 100
                        mfe = max(mfe, trade_high)
                        mae = min(mae, trade_low)
                        
                        if records[k]["exit_signal"] > 0 or k == len(records)-1:
                            pnl = (records[k]["close"] / entry_price - 1) * 100
                            duration = k - (i+1)
                            break
                    
                    # Store trade info
                    if pnl is not None:
                        score = records[i]["entry_signal"]
                        
                        # Simulated Score if penalized
                        sim_score = score
                        if is_fas_flat:
                            sim_score -= award
                            
                        all_trades.append({
                            "symbol": symbol,
                            "date": entry_date,
                            "is_fas_flat": is_fas_flat,
                            "pnl": pnl,
                            "mfe": mfe,
                            "mae": mae,
                            "duration": duration,
                            "score": score,
                            "sim_score": sim_score,
                            "passes_penalized": sim_score >= threshold
                        })

                        
        except Exception as e:
            print(f"Error processing {symbol}: {e}")

    if not all_trades:
        print("No trades found in the study period.")
        return

    trades_df = pd.DataFrame(all_trades)
    
    # 1. Summary by FAS Flatness
    summary = trades_df.groupby("is_fas_flat").agg(
        Trades=("pnl", "count"),
        Win_Rate=("pnl", lambda x: (x > 0).mean() * 100),
        Avg_PnL=("pnl", "mean"),
        Avg_MFE=("mfe", "mean"),
        Avg_MAE=("mae", "mean"),
        Avg_Duration=("duration", "mean")
    ).reset_index()
    
    print("\nSummary by FAS Flatness:")
    print(tabulate(summary, headers="keys", tablefmt="github", floatfmt=".2f"))
    
    # 1.5 Individual FAS Flat Trades
    print("\nIndividual FAS Flat Trades:")
    fas_flat_trades = trades_df[trades_df["is_fas_flat"]].copy()
    if not fas_flat_trades.empty:
        print(tabulate(fas_flat_trades[["symbol", "date", "pnl", "score", "sim_score", "passes_penalized"]], headers="keys", tablefmt="github", floatfmt=".2f"))
    else:
        print("No FAS flat trades found.")
    # If we penalize FAS Flat trades by 'award' unit, some might fall below threshold.
    # We need to know the 'raw' score or current score to simulate this.
    # For now, let's assume ALL FAS Flat trades are rejected to see the maximum impact.
    
    baseline = trades_df
    optimized = trades_df[trades_df["passes_penalized"]]
    
    def get_metrics(df):
        if df.empty: return [0]*7
        trades = len(df)
        wr = (df["pnl"] > 0).mean() * 100
        avg_pnl = df["pnl"].mean()
        pf = df[df["pnl"] > 0]["pnl"].sum() / abs(df[df["pnl"] < 0]["pnl"].sum()) if any(df["pnl"] < 0) else np.inf
        exp = (wr/100 * df[df["pnl"] > 0]["pnl"].mean() if any(df["pnl"] > 0) else 0) + \
              ((1-wr/100) * df[df["pnl"] < 0]["pnl"].mean() if any(df["pnl"] < 0) else 0)
        return [trades, wr, avg_pnl, pf, exp, df["mfe"].mean(), df["duration"].mean()]

    b_m = get_metrics(baseline)
    o_m = get_metrics(optimized)
    
    impact_data = [
        ["Trades", b_m[0], o_m[0], f"{(o_m[0]/b_m[0]-1)*100:+.1f}%"],
        ["Win Rate (%)", f"{b_m[1]:.1f}%", f"{o_m[1]:.1f}%", f"{o_m[1]-b_m[1]:+.1f}%"],
        ["Avg P&L (%)", f"{b_m[2]:.2f}%", f"{o_m[2]:.2f}%", f"{o_m[2]-b_m[2]:+.2f}%"],
        ["Prof. Factor", f"{b_m[3]:.2f}", f"{o_m[3]:.2f}", f"{o_m[3]-b_m[3]:+.2f}"],
        ["Expectancy (%)", f"{b_m[4]:.2f}%", f"{o_m[4]:.2f}%", f"{o_m[4]-b_m[4]:+.2f}%"],
        ["Avg MFE (%)", f"{b_m[5]:.2f}%", f"{o_m[5]:.2f}%", f"{o_m[5]-b_m[5]:+.2f}%"],
        ["Avg Duration", f"{b_m[6]:.1f}", f"{o_m[6]:.1f}", f"{o_m[6]-b_m[6]:+.1f}"]
    ]
    
    print(f"\nImpact Analysis (Penalizing FAS Flat by {award:.2f} units, threshold {threshold:.2f}):")
    print(tabulate(impact_data, headers=["Metric", "Baseline (Before)", "Optimized (After)", "Delta"], tablefmt="github"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--start", default="2024-01-01")
    args = parser.parse_args()
    
    run_study(args.watchlist, args.start)
