import sqlite3
import sys
import logging
import argparse
import multiprocessing
from datetime import datetime
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.database import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def process_symbol(symbol: str) -> dict | None:
    try:
        # Optimize by restricting the signal tagging to the last 252 bars 
        # (sufficient to determine current entry/exit/in-trade state for screener)
        engine = DivergenceEngine(symbol)
        result = engine.run()
        df = result.ledger
        
        if df is None or len(df) < 2:
            return None
            
        last_row = df.iloc[-1]
        date_str = str(last_row.get("date", ""))[:10]
        price = float(last_row.get("close", 0.0))
        
        entry_indices = df.index[df['entry_signal'] > 0].tolist()
        exit_indices = df.index[df['exit_signal'] > 0].tolist()
        
        last_entry_idx = entry_indices[-1] if entry_indices else -1
        last_exit_idx = exit_indices[-1] if exit_indices else -1
        
        signal_type = None
        pnl = None
        entry_date = None
        entry_price = None
        bars_held = None
        mfe_pct = None
        mae_pct = None
        
        last_idx = len(df) - 1
        
        # New technical signals
        c_up = 1 if last_row.get('open', 0) < last_row.get('cwvap', 0) and last_row.get('close', 0) > last_row.get('cwvap', 0) else 0
        c_down = 1 if last_row.get('open', 0) > last_row.get('cwvap', 0) and last_row.get('close', 0) < last_row.get('cwvap', 0) else 0
        max_cts = 1 if last_row.get('cts', 0) == 1 else 0
        min_cts = 1 if last_row.get('cts', 0) == -1 else 0

        if last_entry_idx == last_idx:
            signal_type = "entry"
            entry_date = str(df.iloc[last_idx].get('date', ''))[:10]
            entry_price = price
            bars_held = 0
            mfe_pct = 0.0
            mae_pct = 0.0
        elif last_exit_idx == last_idx:
            signal_type = "exit"
            if last_entry_idx != -1 and last_entry_idx < last_exit_idx:
                entry_exec_idx = last_entry_idx + 1
                if entry_exec_idx <= last_exit_idx:
                    ep = float(df.iloc[entry_exec_idx]['close'])
                    entry_date = str(df.iloc[last_entry_idx].get('date', ''))[:10]
                    entry_price = ep
                    bars_held = last_exit_idx - last_entry_idx
                    if ep > 0:
                        pnl = round((price / ep - 1) * 100, 2)
                        highs = df.iloc[entry_exec_idx:last_exit_idx+1]['high']
                        lows = df.iloc[entry_exec_idx:last_exit_idx+1]['low']
                        mfe_pct = round((float(highs.max()) / ep - 1) * 100, 2)
                        mae_pct = round((float(lows.min()) / ep - 1) * 100, 2)
        elif last_entry_idx > last_exit_idx:
            signal_type = "in-trade"
            entry_exec_idx = last_entry_idx + 1
            if entry_exec_idx <= last_idx:
                ep = float(df.iloc[entry_exec_idx]['close'])
                entry_date = str(df.iloc[last_entry_idx].get('date', ''))[:10]
                entry_price = ep
                bars_held = last_idx - last_entry_idx
                if ep > 0:
                    pnl = round((price / ep - 1) * 100, 2)
                    highs = df.iloc[entry_exec_idx:last_idx+1]['high']
                    lows = df.iloc[entry_exec_idx:last_idx+1]['low']
                    mfe_pct = round((float(highs.max()) / ep - 1) * 100, 2)
                    mae_pct = round((float(lows.min()) / ep - 1) * 100, 2)
        
        # We store if it has any trade lifecycle signal OR any technical signal
        if signal_type or c_up or c_down or max_cts or min_cts:
            return {
                "symbol": symbol,
                "date": date_str,
                "price": price,
                "signal_type": signal_type or "none",
                "pnl": pnl,
                "entry_date": entry_date,
                "entry_price": entry_price,
                "bars_held": bars_held,
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
                "c_up": c_up,
                "c_down": c_down,
                "max_cts": max_cts,
                "min_cts": min_cts
            }
    except Exception as e:
        logger.warning(f"Failed processing {symbol}: {e}")
    return None

def main():
    parser = argparse.ArgumentParser(description="Global Market Screener")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only log errors")
    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.ERROR)
        import os
        sys.stdout = open(os.devnull, 'w')
    
    conn = sqlite3.connect(str(DB_PATH))
    # Clear old data (we only want the latest state)
    conn.execute("DELETE FROM screener_signals")
    
    # Get NIFTY 500 stocks
    row = conn.execute("SELECT id FROM watchlists WHERE name = 'NIFTY 500'").fetchone()
    if not row:
        logger.error("NIFTY 500 watchlist not found. Please ensure it is imported.")
        conn.close()
        sys.exit(1)
        
    symbols = [
        r[0] for r in conn.execute(
            "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY display_order",
            (row[0],)
        ).fetchall()
    ]
    cores = multiprocessing.cpu_count()
    logger.info(f"Scanning {len(symbols)} NIFTY 500 symbols using {cores} parallel workers...")
    
    signals_found = {"entry": 0, "exit": 0, "in-trade": 0, "none": 0}
    results = []

    # Parallelize DivergenceEngine calculations across CPU cores
    with ProcessPoolExecutor(max_workers=cores) as executor:
        futures = {executor.submit(process_symbol, sym): sym for sym in symbols}
        
        for i, future in enumerate(as_completed(futures)):
            if i > 0 and i % 50 == 0:
                logger.info(f"Processed {i}/{len(symbols)} symbols...")
                
            res = future.result()
            if res:
                results.append((
                    res["symbol"], res["date"], res["price"], res["signal_type"], res["pnl"],
                    res["entry_date"], res["entry_price"], res["bars_held"], res["mfe_pct"], res["mae_pct"],
                    res["c_up"], res["c_down"], res["max_cts"], res["min_cts"]
                ))
                signals_found[res["signal_type"]] += 1

    # Bulk insert for fast database write
    if results:
        conn.executemany(
            "INSERT INTO screener_signals (symbol, date, price, signal_type, pnl, entry_date, entry_price, bars_held, mfe_pct, mae_pct, c_up, c_down, max_cts, min_cts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            results
        )
        
    conn.commit()
    conn.close()
    
    logger.info(f"Screener complete. Entries: {signals_found['entry']}, Exits: {signals_found['exit']}, In-trade: {signals_found['in-trade']}, Tech-Only: {signals_found['none']}")

if __name__ == "__main__":
    main()