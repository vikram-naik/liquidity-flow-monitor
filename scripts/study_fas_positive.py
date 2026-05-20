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
    print(f"Studying Positive FAS on {len(symbols)} symbols from {watchlist} starting {period_start}...")
    
    all_trades = []
    
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SavgolCTSSignal()
    
    threshold = float(get_user_setting("ml_guard_threshold", "1.55"))
    award = threshold * 0.3
    
    start_dt = pd.to_datetime(period_start)
    
    for symbol in symbols:
        try:
            engine = DivergenceEngine(symbol)
            res_engine = engine.run()
            df = res_engine.ledger
            if df.empty: continue
            
            res = signal.tag_signals(df, entry_cfg, exit_cfg)
            
            records = res.to_dict("records")
            for i in range(2, len(records)):
                if records[i]["entry_signal"] > 0:
                    if i + 1 >= len(records):
                        continue
                        
                    # 1. Evaluate Positive FAS
                    fas_val = records[i].get("fas", 0)
                    is_fas_positive = fas_val > 0
                    
                    # 2. Find matching trade to get PnL
                    entry_row = records[i+1]
                    entry_date = pd.to_datetime(entry_row["date"])
                    entry_price = entry_row["open"]
                    
                    pnl = None
                    duration = 0
                    mfe = 0
                    mae = 0
                    
                    if entry_date < start_dt:
                        continue

                    for k in range(i+1, len(records)):
                        close_k = records[k]["close"]
                        high_k = records[k]["high"]
                        low_k = records[k]["low"]
                        
                        trade_high = (high_k / entry_price - 1) * 100
                        trade_low = (low_k / entry_price - 1) * 100
                        mfe = max(mfe, trade_high)
                        mae = min(mae, trade_low)
                        
                        if records[k]["exit_signal"] > 0 or k == len(records)-1:
                            pnl = (records[k]["close"] / entry_price - 1) * 100
                            duration = k - (i+1)
                            break
                    
                    if pnl is not None:
                        score = records[i]["entry_signal"]
                        
                        sim_score = score
                        if is_fas_positive:
                            sim_score -= award
                            
                        all_trades.append({
                            "symbol": symbol,
                            "date": entry_date,
                            "is_fas_positive": is_fas_positive,
                            "fas": fas_val,
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
    
    # 1. Summary by Positive FAS
    summary = trades_df.groupby("is_fas_positive").agg(
        Trades=("pnl", "count"),
        Win_Rate=("pnl", lambda x: (x > 0).mean() * 100),
        Avg_PnL=("pnl", "mean"),
        Avg_MFE=("mfe", "mean"),
        Avg_MAE=("mae", "mean"),
        Avg_Duration=("duration", "mean")
    ).reset_index()
    
    print("\nSummary by Positive FAS:")
    print(tabulate(summary, headers="keys", tablefmt="github", floatfmt=".2f"))
    
    # 1.5 Individual Positive FAS Trades
    print("\nRejected Trades (Positive FAS & Score fell below threshold):")
    rejected_trades = trades_df[trades_df["is_fas_positive"] & ~trades_df["passes_penalized"]].copy()
    if not rejected_trades.empty:
        print(tabulate(rejected_trades[["symbol", "date", "fas", "pnl", "score", "sim_score"]], headers="keys", tablefmt="github", floatfmt=".2f"))
    else:
        print("No trades would be rejected.")
    
    # 1.6 Remaining Accepted Trades
    print("\nRemaining Accepted Trades (Negative/Zero FAS or survived penalty):")
    accepted_trades = trades_df[trades_df["passes_penalized"]].copy()
    if not accepted_trades.empty:
        print(tabulate(accepted_trades[["symbol", "date", "fas", "pnl", "score", "sim_score"]], headers="keys", tablefmt="github", floatfmt=".2f"))
    else:
        print("No trades remained.")

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
    
    print(f"\nImpact Analysis (Penalizing Positive FAS by {award:.2f} units, threshold {threshold:.2f}):")
    print(tabulate(impact_data, headers=["Metric", "Baseline (Before)", "Optimized (After)", "Delta"], tablefmt="github"))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--start", default="2024-01-01")
    args = parser.parse_args()
    
    run_study(args.watchlist, args.start)