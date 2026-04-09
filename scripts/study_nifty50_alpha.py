import sqlite3
import sys
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.base import Trade

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

def get_watchlist_symbols(name: str) -> list[str]:
    db = sqlite3.connect(str(DB_PATH))
    row = db.execute("SELECT id FROM watchlists WHERE name = ?", (name,)).fetchone()
    if not row: return []
    symbols = [r[0] for r in db.execute("SELECT symbol FROM watchlist_items WHERE watchlist_id = ?",(row[0],)).fetchall()]
    db.close()
    return symbols

def get_market_data() -> pd.DataFrame:
    try:
        df = DivergenceEngine("NIFTY 50").run().ledger
        df['date'] = df['date'].astype(str).str[:10]
        return df[['date', 'price_slope_z']]
    except: return pd.DataFrame()

def simulate_trades(ticker: str, df: pd.DataFrame, mkt_df: pd.DataFrame, start_date: str):
    records = df.to_dict("records")
    n = len(records)
    trades = []
    in_trade = False
    trade = None
    pending_signal = None

    # Optimal Swing Parameters
    STOP_ATR = 3.5
    TRAIL_START_PNL = 10.0
    TRAIL_ATR = 2.0
    TIME_FAIL_BARS = 30
    TIME_FAIL_PNL = 0.0

    for i in range(60, n):
        row = records[i]
        dt = str(row['date'])[:10]
        
        # Exclude COVID volatility
        if "2020-02-01" <= dt <= "2021-06-30":
            continue
            
        if in_trade:
            close = row['close']
            atr = row['atr_20']
            
            pnl = (close / trade.entry_price - 1) * 100
            trade.mfe_pct = max(trade.mfe_pct, pnl)
            
            # Initial Stop
            if not hasattr(trade, 'stop'): 
                trade.stop = trade.entry_price - STOP_ATR * trade.atr_at_entry
                
            # Wide Trailing Stop
            if pnl > TRAIL_START_PNL: 
                trade.stop = max(trade.stop, close - TRAIL_ATR * atr)
            
            exit_reason = None
            if close < trade.stop: 
                exit_reason = "TRAILING_STOP" if pnl > 0 else "STOP_LOSS"
            elif (i - trade.entry_idx) >= TIME_FAIL_BARS and pnl < TIME_FAIL_PNL: 
                exit_reason = "TIME_FAIL" 
            
            if exit_reason:
                trade.exit_date = dt
                trade.exit_price = row['open'] # EOD-Lag exit execution
                trade.pnl_pct = round((trade.exit_price / trade.entry_price - 1) * 100, 2)
                trade.duration = i - trade.entry_idx
                trade.exit_reason = exit_reason
                trades.append(trade)
                in_trade = False
                trade = None
            continue

        if dt < start_date: continue
        
        if pending_signal:
            trade = Trade(symbol=ticker, entry_date=dt, entry_price=row['open'], entry_idx=i, atr_at_entry=row['atr_20'], soft_filters_passed=0, entry_tag="SWING")
            trade.signal_date = pending_signal
            trade.mfe_pct = 0
            trade.mae_pct = 0
            in_trade = True
            pending_signal = None
            continue

        # Lookback Features
        cts_accel = row.get('cts_accel', 0)
        cwc_slope = row.get('cwc_slope', 0)
        range_pos_63 = row.get('range_pos_63', 1.0)
        pdd_120 = row.get('pdd_120', 0)
        coherence = row.get('coherence', 0)
        
        mkt_row = mkt_df[mkt_df['date'] == dt]
        mkt_psz = mkt_row['price_slope_z'].values[0] if not mkt_row.empty else 0
        
        # High Payoff & WR Signal Rules for SWING
        cond_1 = range_pos_63 < 0.2
        cond_2 = pdd_120 > 0.0
        cond_3 = cts_accel > 0.05
        cond_4 = cwc_slope > 0.02
        cond_5 = mkt_psz > -0.5
        cond_6 = coherence > 0.5
        
        if cond_1 and cond_2 and cond_3 and cond_4 and cond_5 and cond_6:
            pending_signal = dt

    return trades

def main():
    symbols = get_watchlist_symbols("NIFTY 50")
    mkt_df = get_market_data()
    all_trades = []
    
    print("Simulating Swing Alpha trades on NIFTY 50...")
    for sym in symbols:
        try:
            res = DivergenceEngine(sym).run()
            all_trades.extend(simulate_trades(sym, res.ledger, mkt_df, "2016-01-01"))
        except: pass
        
    if not all_trades: 
        print("No trades found.")
        return
        
    df = pd.DataFrame([{"symbol": t.symbol, "entry_date": t.entry_date, "exit_date": t.exit_date, "pnl%": t.pnl_pct, "dur": t.duration, "reason": t.exit_reason, "mfe%": round(t.mfe_pct,2)} for t in all_trades])
    wins = df[df["pnl%"] > 0]
    losses = df[df["pnl%"] <= 0]
    
    wr = len(wins)/len(df)*100
    avg_win = wins["pnl%"].mean() if not wins.empty else 0
    avg_loss = abs(losses["pnl%"].mean()) if not losses.empty else 1
    payoff = avg_win/avg_loss if avg_loss > 0 else 0
    
    print(f"\nALPHA NIFTY 50 SWING | WR: {wr:.1f}% | Payoff: {payoff:.2f}x | Avg Pnl: {df['pnl%'].mean():.2f}% | Total Trades: {len(df)} | Avg Duration: {df['dur'].mean():.1f} bars")
    print("\n--- TOP TRADES ---")
    print(tabulate(df.sort_values("pnl%", ascending=False).head(10), headers="keys", tablefmt="simple", showindex=False))
    
    # Save output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_DIR / "nifty50_alpha_trades.csv", index=False)
    print(f"\nTrades exported to {OUTPUT_DIR / 'nifty50_alpha_trades.csv'}")

if __name__ == "__main__":
    main()
