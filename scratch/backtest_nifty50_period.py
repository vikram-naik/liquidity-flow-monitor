import sqlite3
import sys
import pandas as pd
from pathlib import Path
from datetime import datetime

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from scripts.walk_forward import simulate_trades, get_watchlist_symbols, today_str

def get_taken_trades():
    conn = sqlite3.connect("liquidity_monitor.db")
    df = pd.read_sql_query(
        "SELECT symbol, entry_date FROM trading_positions WHERE entry_date >= '2025-12-01'",
        conn
    )
    conn.close()
    # Return set of (symbol, entry_date)
    return set(zip(df['symbol'], df['entry_date']))

def main():
    watchlist = "NIFTY 50"
    start_date = "2025-12-01"
    end_date = today_str()
    
    symbols = get_watchlist_symbols(watchlist)
    print(f"Running backtest for {len(symbols)} symbols in {watchlist} from {start_date} to {end_date}...")
    
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    
    taken_trades = get_taken_trades()
    print(f"Loaded {len(taken_trades)} taken trades from DB.")
    
    simulated_trades = []
    failed = []
    
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            result = engine.run()
            trades = simulate_trades(sym, result.ledger, entry_cfg, exit_cfg, signal)
            # Filter trades to only those entered in the requested period
            period_trades = [t for t in trades if start_date <= str(t.entry_date) <= end_date]
            simulated_trades.extend(period_trades)
        except Exception as e:
            print(f"Failed {sym}: {repr(e)}")
            failed.append((sym, str(e)))
            
    print(f"Backtest completed: {len(simulated_trades)} simulated trades, {len(failed)} failed.")
    
    # Process trades
    records = []
    for t in simulated_trades:
        # Check if trade was actually taken
        is_taken = (t.symbol, t.entry_date) in taken_trades
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
            "exit_reason": exit_reason_str,
            "is_taken": is_taken
        })
        
    df = pd.DataFrame(records)
    df.to_csv("scratch/all_simulated_trades_20251201.csv", index=False)
    print("Saved all simulated trades to scratch/all_simulated_trades_20251201.csv")
    
    # Filter for missed trades
    missed_df = df[~df['is_taken']]
    missed_df.to_csv("scratch/missed_trades_20251201.csv", index=False)
    print(f"Saved {len(missed_df)} missed trades to scratch/missed_trades_20251201.csv")

if __name__ == "__main__":
    main()
