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

TEST_END = "2026-03-26"

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
            
            # Filter for CTS-Floor-Leave entries
            if trade.entry_tag == EntryTag.CTS_FLOOR_LEAVE.value:
                extracted_trades.append({
                    "symbol": ticker,
                    "entry_type": trade.entry_tag,
                    "signal_date": getattr(trade, "signal_date", ""),
                    "entry_date": trade.entry_date,
                    "exit_date": str(row.get("date", ""))[:10],
                    "pnl": round((exit_price / trade.entry_price - 1) * 100, 2),
                    "signal_cwvap_dist": round(getattr(trade, "signal_cwvap_dist", np.nan), 4),
                    "entry_cwvap_dist": round(trade.entry_cwvap_dist, 4),
                    "cts_direction": getattr(trade, "entry_cts_direction", ""),
                    "cts_is_steep": getattr(trade, "entry_cts_is_steep", False),
                    "signal_cts": round(getattr(trade, "signal_cts", np.nan), 4),
                    "signal_psz": round(getattr(trade, "signal_psz", np.nan), 4),
                    "signal_pszv": round(getattr(trade, "signal_pszv", np.nan), 6),
                    "exit_reason": str(pending_exit_reason)
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
            cwvap_at_entry = row.get("cwvap", np.nan)
            entry_cwvap_dist = ((close - cwvap_at_entry) / cwvap_at_entry * 100.0) if not np.isnan(cwvap_at_entry) else np.nan
            
            # Create a minimal Trade object for the signal logic
            trade = Trade(
                symbol=ticker,
                entry_date=str(row.get("date", ""))[:10],
                entry_price=close,
                entry_idx=i,
                atr_at_entry=row.get("atr_20", 1.0),
                soft_filters_passed=sig.get("soft_count", 0),
                entry_tag=sig.get("details", {}).get("entry_tag", EntryTag.PSZ).value,
            )
            # Add custom attributes for extraction
            trade.signal_date = sig.get("date", "")
            trade.signal_cwvap_dist = sig.get("dist", np.nan)
            trade.entry_cwvap_dist = entry_cwvap_dist
            trade.entry_cts_direction = sig.get("cts_direction", "")
            trade.entry_cts_is_steep = sig.get("cts_is_steep", False)
            trade.signal_cts = sig.get("cts", np.nan)
            trade.signal_psz = sig.get("psz", np.nan)
            trade.signal_pszv = sig.get("psz_v", np.nan)
            
            peak_close = close
            delivery_bad_count = 0
            in_trade = True

        else:
            qualifies, soft_count, fdetails = signal.check_entry(row, prev, entry_cfg, records, i)
            if qualifies:
                # Capture Signal Bar Distance correctly
                s_cwvap = row.get("cwvap", np.nan)
                s_close = row.get("close", np.nan)
                s_dist = (s_close - s_cwvap) / s_cwvap * 100.0 if not np.isnan(s_cwvap) and s_cwvap > 0 else np.nan

                pending_signal = {
                    "date": str(row.get("date", ""))[:10],
                    "dist": s_dist,
                    "soft_count": soft_count, 
                    "details": fdetails,
                    "cts_direction": row.get("cts_direction", ""),
                    "cts_is_steep": row.get("cts_is_steep", False),
                    "cts": row.get("cts", np.nan),
                    "psz": row.get("price_slope_z", np.nan),
                    "psz_v": row.get("psz_v", np.nan)
                }

    return extracted_trades

def main():
    parser = argparse.ArgumentParser(description="Extract 'CTS-Floor-Leave' trades")
    parser.add_argument("--watchlist", default="NIFTY 500")
    parser.add_argument("--output", default="floor_leave_trades.csv")
    args = parser.parse_args()

    symbols = get_watchlist_symbols(args.watchlist)
    print(f"Extracting 'CTS-Floor-Leave' trades for {args.watchlist} ({len(symbols)} symbols)")
    
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    all_extracted_trades = []

    for i, sym in enumerate(symbols, 1):
        print(f"  [{i}/{len(symbols)}] {sym}...", end=" ", flush=True)
        try:
            engine = DivergenceEngine(sym, start_date="2018-06-01", end_date=TEST_END)
            result = engine.run()
            trades = simulate_trades(sym, result.ledger, entry_cfg, exit_cfg, signal)
            
            all_extracted_trades.extend(trades)
            print(f"{len(trades)} matches")
        except Exception as e:
            print(f"SKIP - {e}")

    if all_extracted_trades:
        df_out = pd.DataFrame(all_extracted_trades)
        df_out.to_csv(args.output, index=False)
        print(f"\nSaved {len(all_extracted_trades)} trades to {args.output}")
        
        # Simple summary
        avg_pnl = df_out["pnl"].mean()
        print(f"Average P&L for these trades: {avg_pnl:.2f}%")
        
        # Exit reason breakdown
        print("\nExit Reason Breakdown:")
        print(df_out["exit_reason"].value_counts())
    else:
        print("\nNo 'CTS-Floor-Leave' trades found.")

if __name__ == "__main__":
    main()
