"""
Walk-Forward Validation — compare train vs test period performance.

Train: 2019-01-01 to 2023-12-31
Test:  2024-01-01 to 2026-03-15

Usage:
    venv/bin/python3 scripts/walk_forward.py
    venv/bin/python3 scripts/walk_forward.py --watchlist "NIFTY 50"
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
from src.trading.signals import EntryConfig, ExitConfig, Trade, check_entry, check_exit

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"

TRAIN_START = "2019-01-01"
TRAIN_END = "2023-12-31"
TEST_START = "2024-01-01"
TEST_END = "2026-03-15"


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


def simulate_trades(
    ticker: str, df: pd.DataFrame,
    entry_cfg: EntryConfig, exit_cfg: ExitConfig,
) -> list[Trade]:
    """Walk through ledger bar-by-bar, enter and exit trades."""
    records = df.to_dict("records")
    n = len(records)
    trades = []
    in_trade = False
    trade = None
    peak_close = 0.0
    delivery_bad_count = 0
    cwvap_values: list[float] = []
    pending_signal: dict | None = None

    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue

        cw = row.get("cwvap", np.nan)
        cwvap_values.append(cw)

        if in_trade:
            if close > peak_close:
                peak_close = close

            bars_held = i - trade.entry_idx
            mfe = max(trade.mfe_pct, (close / trade.entry_price - 1) * 100)
            mae_val = (close / trade.entry_price - 1) * 100
            mae = min(-trade.mae_pct, mae_val)
            trade.mfe_pct = mfe
            if -mae > trade.mae_pct:
                trade.mae_pct = -mae

            reason, delivery_bad_count = check_exit(
                row, trade, peak_close, bars_held,
                delivery_bad_count, cwvap_values, exit_cfg,
            )

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
                delivery_bad_count = 0

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
                soft_filters_passed=sig["soft_count"],
                rdv_pass=sig["details"]["rdv"],
                mcs_pass=sig["details"]["mcs"],
                cwc_pass=sig["details"]["cwc"],
                grad_pass=sig["details"]["grad"],
                regime_at_entry=sig["details"]["regime"],
                psz_at_entry=psz_now if not np.isnan(psz_now) else 0.0,
                psz_peak=psz_now if not np.isnan(psz_now) else 0.0,
            )
            peak_close = close
            delivery_bad_count = 0
            in_trade = True

        else:
            qualifies, soft_count, fdetails = check_entry(row, prev, entry_cfg, records, i)
            if qualifies:
                pending_signal = {"soft_count": soft_count, "details": fdetails}

    if in_trade and trade:
        last = records[-1]
        trade.exit_date = str(last.get("date", ""))[:10]
        trade.exit_price = last.get("close", trade.entry_price)
        trade.exit_reason = "end_of_data"
        trade.pnl_pct = round((trade.exit_price / trade.entry_price - 1) * 100, 2)
        trade.duration = n - 1 - trade.entry_idx
        trade.mfe_pct = round(trade.mfe_pct, 2)
        trade.mae_pct = round(trade.mae_pct, 2)
        trades.append(trade)

    return trades


def run_period(symbols: list[str], start: str, end: str,
               entry_cfg: EntryConfig, exit_cfg: ExitConfig,
               label: str) -> list[Trade]:
    print(f"\n{'='*60}")
    print(f"  {label}: {start} to {end}")
    print(f"{'='*60}")

    all_trades = []
    for i, sym in enumerate(symbols, 1):
        print(f"  [{i}/{len(symbols)}] {sym}...", end=" ", flush=True)
        try:
            engine = DivergenceEngine(sym, start_date=start, end_date=end)
            result = engine.run()
            trades = simulate_trades(sym, result.ledger, entry_cfg, exit_cfg)
            all_trades.extend(trades)
            wins = sum(1 for t in trades if t.pnl_pct > 0)
            print(f"{len(trades)} trades, {wins} wins" if trades else "no trades")
        except Exception as e:
            print(f"SKIP — {e}")

    return all_trades


def summarize(trades: list[Trade], label: str) -> dict:
    if not trades:
        print(f"\n{label}: No trades.")
        return {}

    df = pd.DataFrame([{
        "pnl": t.pnl_pct, "mfe": t.mfe_pct, "mae": t.mae_pct,
        "bars": t.duration, "reason": t.exit_reason,
    } for t in trades])

    total = len(df)
    winners = (df["pnl"] > 0).sum()
    win_rate = winners / total * 100
    avg_pnl = df["pnl"].mean()
    avg_win = df.loc[df["pnl"] > 0, "pnl"].mean() if winners > 0 else 0
    avg_loss = df.loc[df["pnl"] <= 0, "pnl"].mean() if (total - winners) > 0 else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

    print(f"\n{'='*60}")
    print(f"  {label} RESULTS")
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

    return {
        "trades": total, "win_rate": round(win_rate, 1),
        "avg_pnl": round(avg_pnl, 2), "payoff": round(payoff, 2),
        "avg_mfe": round(df["mfe"].mean(), 2), "avg_mae": round(df["mae"].mean(), 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Walk-forward validation")
    parser.add_argument("--watchlist", default="NIFTY 50")
    args = parser.parse_args()

    symbols = get_watchlist_symbols(args.watchlist)
    print(f"Walk-Forward Validation: {args.watchlist} ({len(symbols)} symbols)")
    print(f"Train: {TRAIN_START} — {TRAIN_END}")
    print(f"Test:  {TEST_START} — {TEST_END}")

    # Use quality gate params (PDD_120 + RSZ falling)
    entry_cfg = EntryConfig(pdd_120_max=-3.6, rsz_falling=True)
    exit_cfg = ExitConfig()

    train_trades = run_period(symbols, TRAIN_START, TRAIN_END, entry_cfg, exit_cfg, "TRAIN")
    test_trades = run_period(symbols, TEST_START, TEST_END, entry_cfg, exit_cfg, "TEST")

    train_stats = summarize(train_trades, "TRAIN")
    test_stats = summarize(test_trades, "TEST")

    # Stability comparison
    if train_stats and test_stats:
        print(f"\n{'='*60}")
        print("  PARAMETER STABILITY")
        print(f"{'='*60}")
        for key in ["trades", "win_rate", "avg_pnl", "payoff", "avg_mfe", "avg_mae"]:
            t = train_stats.get(key, 0)
            s = test_stats.get(key, 0)
            delta = s - t if isinstance(t, (int, float)) else 0
            print(f"  {key:12s}  Train={t:>8}  Test={s:>8}  Delta={delta:+.2f}")


if __name__ == "__main__":
    main()
