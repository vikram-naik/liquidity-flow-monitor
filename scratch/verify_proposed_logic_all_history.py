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
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag, ExitReason
from scripts.walk_forward import simulate_trades, get_watchlist_symbols, today_str
from scratch.verify_proposed_logic import add_new_features, ModifiedSavgolCTSSignal

def main():
    watchlist = "NIFTY 50"
    start_date = "2019-01-01"  # Run across all history
    end_date = today_str()
    
    symbols = get_watchlist_symbols(watchlist)
    print(f"Running full historical simulation for {len(symbols)} symbols from {start_date} to {end_date}...")
    
    exit_cfg = SavgolCTSExitConfig()
    base_signal = SignalFactory.get_signal("savgol_cts")
    modified_signal = ModifiedSavgolCTSSignal(base_signal)
    
    all_trades = []
    failed = []
    
    # Run simulation
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            df = res.ledger.copy()
            df = add_new_features(df)
            
            # Simulate trades
            trades = simulate_trades(sym, df, None, exit_cfg, modified_signal)
            period_trades = [t for t in trades if start_date <= str(t.entry_date) <= end_date]
            all_trades.extend(period_trades)
        except Exception as e:
            failed.append((sym, str(e)))
            
    print(f"Simulation completed: {len(all_trades)} trades simulated, {len(failed)} failed.")
    
    # Process results into DataFrame
    records = []
    for t in all_trades:
        exit_reason_str = t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)
        records.append({
            "symbol": t.symbol,
            "entry_date": t.entry_date,
            "entry_price": t.entry_price,
            "exit_date": t.exit_date,
            "exit_price": t.exit_price,
            "pnl_pct": t.pnl_pct,
            "duration": t.duration,
            "entry_tag": t.entry_tag,
            "exit_reason": exit_reason_str
        })
        
    df_new = pd.DataFrame(records)
    df_new.to_csv("scratch/modified_trades_all_history.csv", index=False)
    print("Saved modified trades to scratch/modified_trades_all_history.csv")
    
    # Aggregate Metrics
    total_trades = len(df_new)
    if total_trades > 0:
        win_rate = (df_new['pnl_pct'] > 0).mean() * 100
        avg_pnl = df_new['pnl_pct'].mean()
        median_pnl = df_new['pnl_pct'].median()
        avg_duration = df_new['duration'].mean()
        
        gross_profits = df_new[df_new['pnl_pct'] > 0]['pnl_pct'].sum()
        gross_losses = abs(df_new[df_new['pnl_pct'] <= 0]['pnl_pct'].sum())
        profit_factor = gross_profits / gross_losses if gross_losses > 0 else float('inf')
        
        print("\n" + "="*50)
        print("    FULL HISTORICAL PERFORMANCE (SINCE 2019)")
        print("="*50)
        print(f"Total Trades:      {total_trades}")
        print(f"Win Rate:          {win_rate:.2f}%")
        print(f"Avg PnL:           {avg_pnl:+.2f}%")
        print(f"Median PnL:        {median_pnl:+.2f}%")
        print(f"Profit Factor:     {profit_factor:.2f}x")
        print(f"Avg Duration:      {avg_duration:.1f} bars")
        print("="*50)
        
        # Group by entry tag
        print("\n--- PERFORMANCE BY ENTRY TAG ---")
        tag_grouped = df_new.groupby("entry_tag").agg(
            count=("pnl_pct", "size"),
            avg_pnl=("pnl_pct", "mean"),
            win_rate=("pnl_pct", lambda x: (x > 0).mean() * 100),
            avg_duration=("duration", "mean")
        ).reset_index().sort_values(by="avg_pnl", ascending=False)
        print(tabulate(tag_grouped, headers='keys', tablefmt='psql', showindex=False))
    else:
        print("No trades generated.")

if __name__ == "__main__":
    main()
