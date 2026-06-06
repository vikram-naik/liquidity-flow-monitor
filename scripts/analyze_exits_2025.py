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
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.savgol_cts.symbol_configs import get_symbol_entry_config, get_symbol_exit_config
from src.database import DB_PATH

def get_watchlist_symbols(name: str) -> list[str]:
    db = sqlite3.connect(str(DB_PATH))
    row = db.execute("SELECT id FROM watchlists WHERE name = ?", (name,)).fetchone()
    if not row:
        avail = [r[0] for r in db.execute("SELECT name FROM watchlists ORDER BY name").fetchall()]
        db.close()
        print(f"Watchlist '{name}' not found. Available: {avail}")
        sys.exit(1)
    symbols = [
        r[0] for r in db.execute(
            "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY display_order",
            (row[0],),
        ).fetchall()
    ]
    db.close()
    return symbols

def simulate_trades_with_history(
    ticker: str, df: pd.DataFrame,
    entry_cfg: SavgolCTSEntryConfig, exit_cfg: SavgolCTSExitConfig, signal
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

    # We will track history of indicators during the trade
    trade_history = []

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
                "entry_idx": trade.entry_idx,
                "entry_price": trade.entry_price,
                "entry_tag": trade.entry_tag,
                "exit_date": str(row.get("date", ""))[:10],
                "exit_idx": i,
                "exit_price": round(exit_price, 2),
                "exit_reason": pending_exit_reason,
                "pnl_pct": round(pnl, 2),
                "mfe_pct": round(trade.mfe_pct, 2),
                "mae_pct": round(trade.mae_pct, 2),
                "duration": i - trade.entry_idx,
                "history": trade_history
            })
            
            in_trade = False
            trade = None
            delivery_bad_count = 0
            pending_exit_reason = None
            trade_history = []
            continue

        if in_trade:
            # Append today's info to trade history
            trade_history.append({
                "date": str(row.get("date", ""))[:10],
                "close": close,
                "cwvap": cw,
                "va_high": row.get("va_high", np.nan),
                "cts": row.get("cts", np.nan),
                "cts_st": row.get("cts_sell_threshold", np.nan),
                "prt": row.get("prt", np.nan),
                "prt_st": row.get("prt_sell_threshold", np.nan),
                "psz": row.get("price_slope_z", np.nan),
                "cwc": row.get("cwc", np.nan),
                "cwc_slope": row.get("cwc_slope", np.nan),
                "fas": row.get("fas", np.nan),
                "pnl_on_close": (close / trade.entry_price - 1) * 100.0
            })

            if close > peak_close:
                peak_close = close

            bars_held = i - trade.entry_idx
            mfe = max(trade.mfe_pct, (close / trade.entry_price - 1) * 100)
            mae_val = (close / trade.entry_price - 1) * 100
            mae = min(-trade.mae_pct, mae_val)
            trade.mfe_pct = mfe
            if -mae > trade.mae_pct:
                trade.mae_pct = -mae

            reason, delivery_bad_count = signal.check_exit(
                row, prev, trade, peak_close, bars_held,
                delivery_bad_count, cwvap_values, exit_cfg,
                records, i,
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
            
            # Start trade history
            trade_history = [{
                "date": str(row.get("date", ""))[:10],
                "close": close,
                "cwvap": cw,
                "va_high": row.get("va_high", np.nan),
                "cts": row.get("cts", np.nan),
                "cts_st": row.get("cts_sell_threshold", np.nan),
                "prt": row.get("prt", np.nan),
                "prt_st": row.get("prt_sell_threshold", np.nan),
                "psz": row.get("price_slope_z", np.nan),
                "cwc": row.get("cwc", np.nan),
                "cwc_slope": row.get("cwc_slope", np.nan),
                "fas": row.get("fas", np.nan),
                "pnl_on_close": 0.0
            }]

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
            "entry_idx": trade.entry_idx,
            "entry_price": trade.entry_price,
            "entry_tag": trade.entry_tag,
            "exit_date": str(last.get("date", ""))[:10],
            "exit_idx": n - 1,
            "exit_price": round(last.get("close", trade.entry_price), 2),
            "exit_reason": "End of Data (Open Trade)",
            "pnl_pct": round(pnl, 2),
            "mfe_pct": round(trade.mfe_pct, 2),
            "mae_pct": round(trade.mae_pct, 2),
            "duration": n - 1 - trade.entry_idx,
            "history": trade_history
        })

    return trades

def main():
    watchlist_name = "NIFTY 50"
    start_date = "2025-12-01"
    
    symbols = get_watchlist_symbols(watchlist_name)
    signal = SignalFactory.get_signal("savgol_cts")
    
    all_trades = []
    
    print(f"Loading data and simulating trades for {len(symbols)} symbols in {watchlist_name} from {start_date}...")
    
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            result = engine.run()
            
            entry_cfg = get_symbol_entry_config(sym)
            exit_cfg = get_symbol_exit_config(sym)
            
            trades = simulate_trades_with_history(sym, result.ledger, entry_cfg, exit_cfg, signal)
            
            # Filter trades to only those entered on or after start_date
            period_trades = [t for t in trades if start_date <= t["entry_date"]]
            all_trades.extend(period_trades)
        except Exception as e:
            print(f"Failed {sym}: {repr(e)}")
            
    if not all_trades:
        print("No trades found in the specified period.")
        return
        
    print(f"\nSimulated {len(all_trades)} trades starting from {start_date}.\n")
    
    # Convert to DataFrame for easier analysis (excluding history column for basic stats)
    df_trades = pd.DataFrame([{
        "symbol": t["symbol"],
        "entry_tag": t["entry_tag"],
        "entry_date": t["entry_date"],
        "exit_date": t["exit_date"],
        "pnl_pct": t["pnl_pct"],
        "mfe_pct": t["mfe_pct"],
        "mae_pct": t["mae_pct"],
        "duration": t["duration"],
        "exit_reason": t["exit_reason"],
        "left_on_table": round(t["mfe_pct"] - t["pnl_pct"], 2)
    } for t in all_trades])
    
    # 1. Overall Stats
    total = len(df_trades)
    win_pct = (df_trades["pnl_pct"] > 0).mean() * 100
    avg_pnl = df_trades["pnl_pct"].mean()
    median_pnl = df_trades["pnl_pct"].median()
    avg_mfe = df_trades["mfe_pct"].mean()
    avg_mae = df_trades["mae_pct"].mean()
    avg_left = df_trades["left_on_table"].mean()
    avg_duration = df_trades["duration"].mean()
    
    print("=" * 60)
    print("                     OVERALL PERFORMANCE STATS")
    print("=" * 60)
    print(f"Total Trades:           {total}")
    print(f"Win Rate:               {win_pct:.2f}%")
    print(f"Avg P&L%:               {avg_pnl:+.2f}%")
    print(f"Median P&L%:            {median_pnl:+.2f}%")
    print(f"Avg MFE%:               {avg_mfe:.2f}%")
    print(f"Avg MAE%:               {avg_mae:.2f}%")
    print(f"Avg P&L Left on Table%: {avg_left:.2f}%")
    print(f"Avg Duration (bars):    {avg_duration:.1f}")
    print("=" * 60)
    
    # 2. Exit Reason Breakdown
    exit_breakdown = df_trades.groupby("exit_reason").agg(
        count=("pnl_pct", "count"),
        win_rate=("pnl_pct", lambda x: (x > 0).mean() * 100),
        avg_pnl=("pnl_pct", "mean"),
        avg_mfe=("mfe_pct", "mean"),
        avg_left=("left_on_table", "mean"),
        avg_dur=("duration", "mean")
    ).reset_index().sort_values(by="count", ascending=False)
    
    print("\n" + "=" * 80)
    print("                         EXIT REASON BREAKDOWN")
    print("=" * 80)
    print(tabulate(exit_breakdown, headers=["Exit Reason", "Count", "Win Rate%", "Avg P&L%", "Avg MFE%", "Avg Left%", "Avg Dur"], tablefmt="simple", floatfmt=".2f"))
    print("=" * 80)
    
    # 3. High Leakage Trades (Left on Table >= 4.0%)
    leakage_trades = df_trades[df_trades["left_on_table"] >= 4.0].copy()
    leakage_trades = leakage_trades.sort_values(by="left_on_table", ascending=False)
    
    print("\n" + "=" * 90)
    print("                 HIGH PROFIT LEAKAGE TRADES (Left on Table >= 4%)")
    print("=" * 90)
    if leakage_trades.empty:
        print("No leakage trades found.")
    else:
        print(tabulate(leakage_trades[["symbol", "entry_tag", "entry_date", "exit_date", "mfe_pct", "pnl_pct", "left_on_table", "duration", "exit_reason"]], 
                       headers=["Symbol", "Entry Tag", "Entry Date", "Exit Date", "MFE%", "PnL%", "Left on Table%", "Dur", "Exit Reason"], 
                       tablefmt="simple", floatfmt=".2f"))
    print("=" * 90)
    
    # Let's inspect all leakage trades in detail to see if we can optimize exits!
    if not leakage_trades.empty:
        print("\n" + "=" * 90)
        print("                  DETAILED ANALYSIS OF LEAKAGE TRADES")
        print("=" * 90)
        top_leaks = leakage_trades["symbol"].tolist()
        
        for trade_dict in all_trades:
            if trade_dict["symbol"] in top_leaks and (trade_dict["mfe_pct"] - trade_dict["pnl_pct"]) >= 4.0:
                print(f"\n--- Symbol: {trade_dict['symbol']} | Entry: {trade_dict['entry_date']} | Exit: {trade_dict['exit_date']} ---")
                print(f"Entry Tag: {trade_dict['entry_tag']} | Exit Reason: {trade_dict['exit_reason']}")
                print(f"PnL: {trade_dict['pnl_pct']}% | MFE: {trade_dict['mfe_pct']}% | Duration: {trade_dict['duration']} bars")
                
                # Print daily history
                hist_data = []
                for h in trade_dict["history"]:
                    hist_data.append([
                        h["date"],
                        f"{h['close']:.2f}",
                        f"{h['cwvap']:.2f}",
                        f"{h['va_high']:.2f}" if not np.isnan(h['va_high']) else "-",
                        f"{h['cts']:.2f}" if not np.isnan(h['cts']) else "-",
                        f"{h['cts_st']:.2f}" if not np.isnan(h['cts_st']) else "-",
                        f"{h['prt']:.2f}" if not np.isnan(h['prt']) else "-",
                        f"{h['prt_st']:.2f}" if not np.isnan(h['prt_st']) else "-",
                        f"{h['psz']:.2f}" if not np.isnan(h['psz']) else "-",
                        f"{h['cwc']:.2f}" if not np.isnan(h['cwc']) else "-",
                        f"{h['cwc_slope']:.2f}" if not np.isnan(h['cwc_slope']) else "-",
                        f"{h['fas']:.2f}" if not np.isnan(h['fas']) else "-",
                        f"{h['pnl_on_close']:+.2f}%"
                    ])
                print(tabulate(hist_data, headers=["Date", "Close", "CWVAP", "VA High", "CTS", "CTS_ST", "PRT", "PRT_ST", "PSZ", "CWC", "CwcSlope", "FAS", "PnL (Close)"], tablefmt="grid"))
                print("-" * 90)

if __name__ == "__main__":
    main()
