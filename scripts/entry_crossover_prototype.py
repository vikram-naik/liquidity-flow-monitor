"""
CTS Threshold Prototype — pure CTS mean-reversion signals.

Entry: CTS <= -1 (buy) + coherence <= 0.4
Exit:  CTS drops to buy_threshold or -1 (whichever first, after having risen)

Causal Savgol CTS range is [-1, +1].

Usage:
    venv/bin/python3 scripts/entry_crossover_prototype.py
    venv/bin/python3 scripts/entry_crossover_prototype.py --watchlist "NIFTY 500"
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade
from src.trading.signals.enums import ExitReason

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"

START = "2025-01-01"
END = "2026-03-15"


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


def check_entry(row: dict, exclude_uptrend: bool = True) -> bool:
    """Buy when CTS <= -1, coherence <= 0.3, pdd_120 > -10, regime != uptrend."""
    cts = row.get("cts", np.nan)
    coh = row.get("coherence", np.nan)
    pdd = row.get("pdd_120", np.nan)
    if np.isnan(cts) or np.isnan(coh):
        return False
    if not np.isnan(pdd) and pdd <= -10.0:
        return False
    if exclude_uptrend and row.get("regime", "") == "uptrend":
        return False
    return cts <= -1.0 and coh <= 0.3


def check_exit(row: dict, cts_rose: bool) -> tuple[str | None, bool]:
    """Exit when CTS drops to buy_threshold or -1 (after having risen above -1)."""
    cts = row.get("cts", np.nan)
    bt = row.get("cts_buy_threshold", np.nan)
    if np.isnan(cts):
        return None, cts_rose
    # Track that CTS has risen above -1 (don't exit on the entry bar itself)
    if cts > -1.0:
        cts_rose = True
    if not cts_rose:
        return None, cts_rose
    # Exit: CTS drops to buy_threshold or -1, whichever comes first
    if not np.isnan(bt) and cts <= bt:
        return "cts_hit_bt", cts_rose
    if cts <= -1.0:
        return "cts_hit_-1", cts_rose
    return None, cts_rose


def simulate_trades(ticker: str, df: pd.DataFrame, exclude_uptrend: bool = True) -> list[Trade]:
    """Walk bar-by-bar: buy at CTS=-1, sell when CTS returns to -1."""
    records = df.to_dict("records")
    n = len(records)
    trades = []
    in_trade = False
    trade = None
    peak_close = 0.0
    pending_entry = False
    cts_rose = False

    for i in range(1, n):
        row = records[i]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue

        if in_trade:
            if close > peak_close:
                peak_close = close

            bars_held = i - trade.entry_idx
            mfe = max(trade.mfe_pct, (close / trade.entry_price - 1) * 100)
            mae_val = (close / trade.entry_price - 1) * 100
            trade.mfe_pct = mfe
            if mae_val < -trade.mae_pct:
                trade.mae_pct = -mae_val

            reason, cts_rose = check_exit(row, cts_rose)

            if reason:
                trade.exit_date = str(row.get("date", ""))[:10]
                trade.exit_price = close
                trade.exit_reason = reason
                trade.pnl_pct = round((close / trade.entry_price - 1) * 100, 2)
                trade.duration = bars_held
                trade.mfe_pct = round(trade.mfe_pct, 2)
                trade.mae_pct = round(trade.mae_pct, 2)
                trades.append(trade)
                in_trade = False
                trade = None
                cts_rose = False

        elif pending_entry:
            pending_entry = False
            atr = row.get("atr_20", 0)
            if atr <= 0 or np.isnan(atr):
                continue
            trade = Trade(
                symbol=ticker,
                entry_date=str(row.get("date", ""))[:10],
                entry_price=close,
                entry_idx=i,
                atr_at_entry=atr,
                soft_filters_passed=0,
                regime_at_entry=str(row.get("regime", "-")),
            )
            peak_close = close
            cts_rose = False
            in_trade = True

        else:
            if check_entry(row, exclude_uptrend=exclude_uptrend):
                pending_entry = True

    # Close open trade at end of data
    if in_trade and trade:
        last = records[-1]
        trade.exit_date = str(last.get("date", ""))[:10]
        trade.exit_price = last.get("close", trade.entry_price)
        trade.exit_reason = ExitReason.END_OF_DATA
        trade.pnl_pct = round((trade.exit_price / trade.entry_price - 1) * 100, 2)
        trade.duration = n - 1 - trade.entry_idx
        trade.mfe_pct = round(trade.mfe_pct, 2)
        trade.mae_pct = round(trade.mae_pct, 2)
        trades.append(trade)

    return trades


def summarize(trades: list[Trade], label: str):
    if not trades:
        print(f"\n{label}: No trades.")
        return

    df = pd.DataFrame([{
        "sym": t.symbol,
        "entry": t.entry_date,
        "exit": t.exit_date,
        "pnl": t.pnl_pct,
        "mfe": t.mfe_pct,
        "mae": t.mae_pct,
        "bars": t.duration,
        "reason": t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason),
        "regime": t.regime_at_entry,
    } for t in trades])

    total = len(df)
    winners = (df["pnl"] > 0).sum()
    losers = total - winners
    win_rate = winners / total * 100
    avg_pnl = df["pnl"].mean()
    avg_win = df.loc[df["pnl"] > 0, "pnl"].mean() if winners > 0 else 0
    avg_loss = df.loc[df["pnl"] <= 0, "pnl"].mean() if losers > 0 else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Trades:       {total}")
    print(f"  Win rate:     {win_rate:.1f}%")
    print(f"  Avg P&L:      {avg_pnl:+.2f}%")
    print(f"  Avg winner:   {avg_win:+.2f}%")
    print(f"  Avg loser:    {avg_loss:+.2f}%")
    print(f"  Payoff ratio: {payoff:.2f}x")
    print(f"  Avg MFE:      {df['mfe'].mean():.2f}%")
    print(f"  Avg MAE:      {df['mae'].mean():.2f}%")
    print(f"  Avg duration: {df['bars'].mean():.1f} bars")

    # Exit breakdown
    reason_agg = (
        df.groupby("reason")
        .agg(count=("pnl", "size"), avg_pnl=("pnl", "mean"),
             win_rate=("pnl", lambda x: round((x > 0).mean() * 100, 1)))
        .reset_index()
        .sort_values("count", ascending=False)
        .round(2)
    )
    print(f"\n  Exit Breakdown:")
    print(tabulate(reason_agg, headers=["Reason", "Count", "Avg P&L%", "Win%"],
                   tablefmt="simple", floatfmt=".2f", showindex=False))

    # Regime breakdown
    regime_agg = (
        df.groupby("regime")
        .agg(count=("pnl", "size"), avg_pnl=("pnl", "mean"),
             win_rate=("pnl", lambda x: round((x > 0).mean() * 100, 1)))
        .reset_index()
        .sort_values("count", ascending=False)
        .round(2)
    )
    print(f"\n  Regime Breakdown:")
    print(tabulate(regime_agg, headers=["Regime", "Count", "Avg P&L%", "Win%"],
                   tablefmt="simple", floatfmt=".2f", showindex=False))

    # Per-symbol summary (top/bottom 5)
    sym_agg = (
        df.groupby("sym")
        .agg(n=("pnl", "size"), avg_pnl=("pnl", "mean"),
             win_rate=("pnl", lambda x: round((x > 0).mean() * 100, 1)))
        .reset_index()
        .sort_values("avg_pnl", ascending=False)
        .round(2)
    )
    print(f"\n  Top 5 symbols:")
    print(tabulate(sym_agg.head(), headers=["Symbol", "N", "Avg P&L%", "Win%"],
                   tablefmt="simple", floatfmt=".2f", showindex=False))
    print(f"\n  Bottom 5 symbols:")
    print(tabulate(sym_agg.tail(), headers=["Symbol", "N", "Avg P&L%", "Win%"],
                   tablefmt="simple", floatfmt=".2f", showindex=False))


def main():
    parser = argparse.ArgumentParser(description="CTS threshold prototype")
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--start", default=START)
    parser.add_argument("--end", default=END)
    parser.add_argument("--no-exclude-uptrend", action="store_true",
                        help="Include uptrend regime entries (baseline comparison)")
    args = parser.parse_args()
    exclude_uptrend = not args.no_exclude_uptrend

    symbols = get_watchlist_symbols(args.watchlist)

    regime_note = "regime != uptrend" if exclude_uptrend else "all regimes"
    print(f"CTS Threshold Prototype (Causal Savgol)")
    print(f"Watchlist: {args.watchlist} ({len(symbols)} symbols)")
    print(f"Period: {args.start} to {args.end}")
    print(f"Entry: CTS <= -1 + coherence <= 0.3 + pdd>-10 + {regime_note}")
    print(f"Exit:  CTS drops to buy_threshold or -1 (whichever first, after having risen)")

    all_trades = []
    for i, sym in enumerate(symbols, 1):
        print(f"  [{i}/{len(symbols)}] {sym}...", end=" ", flush=True)
        try:
            engine = DivergenceEngine(sym, start_date=args.start, end_date=args.end)
            result = engine.run()
            trades = simulate_trades(sym, result.ledger, exclude_uptrend=exclude_uptrend)
            all_trades.extend(trades)
            wins = sum(1 for t in trades if t.pnl_pct > 0)
            print(f"{len(trades)} trades, {wins} wins" if trades else "no trades")
        except Exception as e:
            print(f"SKIP — {e}")

    summarize(all_trades, f"CTS THRESHOLD — {args.watchlist} ({args.start} to {args.end})")


if __name__ == "__main__":
    main()
