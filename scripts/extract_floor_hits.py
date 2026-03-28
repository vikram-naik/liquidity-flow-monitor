import sqlite3
import sys
import argparse
from pathlib import Path
import pandas as pd
import numpy as np
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade, SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import ExitReason, EntryTag

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"

TRAIN_START = "2019-01-01"
TRAIN_END = "2023-12-31"
TEST_START = "2024-01-01"
TEST_END = "2026-03-15"

def get_watchlist_symbols(name: str) -> list[str]:
    db = sqlite3.connect(str(DB_PATH))
    row = db.execute("SELECT id FROM watchlists WHERE name = ?", (name,)).fetchone()
    if not row:
        db.close()
        print(f"Watchlist '{name}' not found.")
        sys.exit(1)
    symbols = [
        r[0] for r in db.execute(
            "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY display_order",
            (row[0],),
        ).fetchall()
    ]
    db.close()
    return symbols

def simulate_trades(
    ticker: str, df: pd.DataFrame,
    entry_cfg, exit_cfg, signal
) -> list[dict]:
    records = df.to_dict("records")
    n = len(records)
    extracted_trades = []
    in_trade = False
    trade = None
    peak_close = 0.0
    delivery_bad_count = 0
    cwvap_values: list[float] = []
    pending_signal: dict | None = None
    pending_exit_reason: ExitReason | str | None = None

    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue

        cw = row.get("cwvap", np.nan)
        cwvap_values.append(cw)

        if pending_exit_reason is not None:
            open_price = row.get("open", np.nan)
            exit_price = open_price if not np.isnan(open_price) else close
            
            # Filter for CTS hit floor
            if pending_exit_reason == ExitReason.FLOOR_HIT or str(pending_exit_reason) == "CTS hit floor":
                extracted_trades.append({
                    "symbol": ticker,
                    "entry_type": trade.entry_tag,
                    "entry_date": trade.entry_date,
                    "exit_date": str(row.get("date", ""))[:10],
                    "pnl": round((exit_price / trade.entry_price - 1) * 100, 2),
                    "cwvap_dist": round(trade.entry_cwvap_dist, 4),
                    "cts_direction": getattr(trade, "entry_cts_direction", ""),
                    "cts_is_steep": getattr(trade, "entry_cts_is_steep", False),
                    "cts_at_entry": round(getattr(trade, "entry_cts", np.nan), 4),
                    "psz_at_entry": round(getattr(trade, "entry_psz", np.nan), 4),
                    "pszv_at_entry": round(getattr(trade, "entry_pszv", np.nan), 6)
                })
            
            in_trade = False
            trade = None
            pending_exit_reason = None
            continue

        if in_trade:
            if close > peak_close:
                peak_close = close
            bars_held = i - trade.entry_idx
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

            # Create a minimal Trade object for the signal logic
            trade = Trade(
                symbol=ticker,
                entry_date=str(row.get("date", ""))[:10],
                entry_price=close,
                entry_idx=i,
                atr_at_entry=row.get("atr_20", 1.0),
                soft_filters_passed=sig.get("soft_count", 0),
                entry_tag=sig.get("details", {}).get("entry_tag", ""),
            )
            # Use signal-day (t) CWVAP distance, not entry-day (t+1)
            trade.entry_cwvap_dist = sig.get("cwvap_dist", np.nan)
            trade.entry_cts_direction = sig.get("cts_direction", "")
            trade.entry_cts_is_steep = sig.get("cts_is_steep", False)
            trade.entry_cts = sig.get("cts", np.nan)
            trade.entry_psz = sig.get("psz", np.nan)
            trade.entry_pszv = sig.get("psz_v", np.nan)
            
            peak_close = close
            delivery_bad_count = 0
            in_trade = True

        else:
            qualifies, soft_count, fdetails = signal.check_entry(row, prev, entry_cfg, records, i)
            if qualifies:
                # Capture signal-day (t) features — trade opens next bar (t+1)
                sig_close = row.get("close", np.nan)
                sig_cwvap = row.get("cwvap", np.nan)
                sig_cwvap_dist = (
                    ((sig_close - sig_cwvap) / sig_cwvap * 100.0)
                    if not np.isnan(sig_cwvap) and not np.isnan(sig_close) and sig_cwvap > 0
                    else np.nan
                )
                pending_signal = {
                    "soft_count": soft_count,
                    "details": fdetails,
                    "cts_direction": row.get("cts_direction", ""),
                    "cts_is_steep": row.get("cts_is_steep", False),
                    "cts": row.get("cts", np.nan),
                    "psz": row.get("price_slope_z", np.nan),
                    "psz_v": row.get("psz_v", np.nan),
                    "cwvap_dist": sig_cwvap_dist,
                }

    return extracted_trades

def main():
    parser = argparse.ArgumentParser(description="Extract 'CTS hit floor' trades")
    parser.add_argument("--watchlist", default="NIFTY 500")
    args = parser.parse_args()

    symbols = get_watchlist_symbols(args.watchlist)
    print(f"Extracting 'CTS hit floor' trades for {args.watchlist} ({len(symbols)} symbols)")
    
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    all_extracted_trades = []

    # Process all symbols (combining Train and Test periods for a full extract)
    # The user request mentioned Train (2019-2023) and Test (2024-2026).
    # I'll just run from 2018-01-01 to ensure enough lookback for start of 2019.
    
    for i, sym in enumerate(symbols, 1):
        print(f"  [{i}/{len(symbols)}] {sym}...", end=" ", flush=True)
        try:
            # We run from TRAIN_START but load some extra history
            engine = DivergenceEngine(sym, start_date="2018-06-01", end_date=TEST_END)
            result = engine.run()
            trades = simulate_trades(sym, result.ledger, entry_cfg, exit_cfg, signal)
            
            # Filter for those actually within the requested TRAIN/TEST periods if needed
            # but usually it's better to just give all found in that range.
            all_extracted_trades.extend(trades)
            print(f"{len(trades)} matches")
        except Exception as e:
            print(f"SKIP - {e}")

    if all_extracted_trades:
        df_out = pd.DataFrame(all_extracted_trades)
        output_file = "floor_hit_trades.csv"
        df_out.to_csv(output_file, index=False)
        print(f"\nSaved {len(all_extracted_trades)} trades to {output_file}")
        
        # Simple summary
        avg_pnl = df_out["pnl"].mean()
        print(f"Average P&L for these trades: {avg_pnl:.2f}%")
    else:
        print("\nNo 'CTS hit floor' trades found.")

if __name__ == "__main__":
    main()
