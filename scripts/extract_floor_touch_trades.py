"""Extract ALL Floor-Touch trades (winners and losers) with entry/exit features.

Uses the same tag_signals() simulation as the UI / backtest.
Purpose: analyze what differentiates winning vs losing Floor-Touch entries.
"""

import sqlite3
import sys
import argparse
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import ExitReason, EntryTag

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"


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


def extract_floor_touch_trades(
    ticker: str, df: pd.DataFrame,
    signal, entry_cfg, exit_cfg,
) -> list[dict]:
    """Extract all Floor-Touch trades from a ledger.

    EOD-lag model: signal fires on bar i, trade executes at bar i+1.
    All reported entry columns are from the signal day (bar i).
    """
    tagged = signal.tag_signals(df, entry_cfg, exit_cfg)
    records = tagged.to_dict("records")
    n = len(records)

    trades = []
    signal_idx: int | None = None

    for i in range(n):
        row = records[i]

        # Detect entry signal bar
        if row.get("entry_signal") and row.get("entry_signal") > 0:
            signal_idx = i

        # Detect exit signal bar
        if row.get("exit_signal") and signal_idx is not None:
            # Filter: only Floor-Touch entries
            entry_reason = str(records[signal_idx].get("entry_reason", ""))
            if "Floor-Touch" not in entry_reason:
                signal_idx = None
                continue

            exit_reason_raw = row.get("exit_reason", "")

            # EOD-lag: execution at signal_day + 1
            entry_exec_idx = signal_idx + 1
            exit_exec_idx = i + 1

            if entry_exec_idx >= n or exit_exec_idx >= n:
                signal_idx = None
                continue

            entry_price = records[entry_exec_idx].get("close", np.nan)
            exit_price = records[exit_exec_idx].get("close", np.nan)

            if np.isnan(entry_price) or np.isnan(exit_price) or entry_price <= 0:
                signal_idx = None
                continue

            sig_row = records[signal_idx]
            exit_sig_row = row

            sig_close = sig_row.get("close", np.nan)
            sig_cwvap = sig_row.get("cwvap", np.nan)
            cwvap_dist = (
                ((sig_close - sig_cwvap) / sig_cwvap * 100.0)
                if not np.isnan(sig_cwvap) and not np.isnan(sig_close) and sig_cwvap > 0
                else np.nan
            )

            pnl = round((exit_price / entry_price - 1) * 100, 2)

            # Peak MFE: scan from entry_exec to exit_exec
            mfe = 0.0
            mae = 0.0
            for j in range(entry_exec_idx, min(exit_exec_idx + 1, n)):
                c = records[j].get("close", np.nan)
                if np.isnan(c):
                    continue
                ret = (c / entry_price - 1) * 100
                if ret > mfe:
                    mfe = ret
                if ret < mae:
                    mae = ret

            bars_held = exit_exec_idx - entry_exec_idx

            trades.append({
                "symbol": ticker,
                "exit_reason": str(exit_reason_raw.value if hasattr(exit_reason_raw, "value") else exit_reason_raw),
                "signal_date": str(sig_row.get("date", ""))[:10],
                "entry_date": str(records[entry_exec_idx].get("date", ""))[:10],
                "exit_date": str(records[exit_exec_idx].get("date", ""))[:10],
                "pnl": pnl,
                "mfe": round(mfe, 2),
                "mae": round(mae, 2),
                "bars_held": bars_held,
                # Signal-day entry indicators
                "cts": round(sig_row.get("cts", np.nan), 4),
                "bt": round(sig_row.get("cts_buy_threshold", np.nan), 4),
                "psz": round(sig_row.get("price_slope_z", np.nan), 4),
                "psz_v": round(sig_row.get("psz_v", np.nan), 6),
                "cwvap_dist": round(cwvap_dist, 4),
                "coherence": round(sig_row.get("coherence", np.nan), 4),
                "pdd_120": round(sig_row.get("pdd_120", np.nan), 4),
                "regime": sig_row.get("regime", ""),
                "intensity": sig_row.get("entry_signal", 0),
                "dvwap_bear_stack": sig_row.get("dvwap_bear_stack", False),
                # Exit signal-day indicators
                "exit_cts": round(exit_sig_row.get("cts", np.nan), 4),
                "exit_bt": round(exit_sig_row.get("cts_buy_threshold", np.nan), 4),
                "exit_psz": round(exit_sig_row.get("price_slope_z", np.nan), 4),
            })

            signal_idx = None

    return trades


def main():
    parser = argparse.ArgumentParser(description="Extract all Floor-Touch trades")
    parser.add_argument("--watchlist", default="NIFTY 500")
    parser.add_argument("--output", default="floor_touch_all_trades.csv")
    args = parser.parse_args()

    symbols = get_watchlist_symbols(args.watchlist)
    print(f"Extracting Floor-Touch trades for {args.watchlist} ({len(symbols)} symbols)")

    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    all_trades = []

    for i, sym in enumerate(symbols, 1):
        print(f"  [{i}/{len(symbols)}] {sym}...", end=" ", flush=True)
        try:
            engine = DivergenceEngine(sym)
            result = engine.run()
            trades = extract_floor_touch_trades(sym, result.ledger, signal, entry_cfg, exit_cfg)
            all_trades.extend(trades)
            wins = sum(1 for t in trades if t["pnl"] > 0)
            print(f"{len(trades)} trades, {wins} wins")
        except Exception as e:
            print(f"SKIP - {e}")

    if all_trades:
        df_out = pd.DataFrame(all_trades)
        df_out.to_csv(args.output, index=False)
        total = len(df_out)
        winners = (df_out["pnl"] > 0).sum()
        print(f"\nSaved {total} trades to {args.output}")
        print(f"Win rate: {winners/total*100:.1f}%, Avg PnL: {df_out['pnl'].mean():.2f}%")

        # Quick exit breakdown
        print("\nExit breakdown:")
        for reason, grp in df_out.groupby("exit_reason"):
            print(f"  {reason}: n={len(grp)}, avg_pnl={grp['pnl'].mean():.2f}%, wr={(grp['pnl']>0).mean()*100:.0f}%")
    else:
        print("\nNo trades found.")


if __name__ == "__main__":
    main()
