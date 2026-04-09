"""
Walk-Forward Validation — compare train vs test period performance.

Train: 2019-01-01 to 2023-12-31
Test:  2024-01-01 to today

Usage:
    venv/bin/python3 scripts/walk_forward.py
    venv/bin/python3 scripts/walk_forward.py --watchlist "NIFTY 50"
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import (
    PriceDivergenceEntryConfig, PriceDivergenceExitConfig,
    NextGenEntryConfig, NextGenExitConfig,
    Trade, SignalFactory,
)
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.base import BaseEntryConfig, BaseExitConfig
from src.trading.signals.enums import ExitReason

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

TRAIN_START = "2019-01-01"
TRAIN_END = "2023-12-31"
TEST_START = "2024-01-01"

SEP = "=" * 72
THIN_SEP = "-" * 72


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


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
    entry_cfg: BaseEntryConfig, exit_cfg: BaseExitConfig, signal
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
    pending_exit_reason: ExitReason | str | None = None

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
            trade.exit_date = str(row.get("date", ""))[:10]
            trade.exit_price = round(exit_price, 2)
            trade.exit_reason = pending_exit_reason
            trade.pnl_pct = round((exit_price / trade.entry_price - 1) * 100, 2)
            trade.duration = i - trade.entry_idx
            trade.mfe_pct = round(trade.mfe_pct, 2)
            trade.mae_pct = round(trade.mae_pct, 2)
            trades.append(trade)
            in_trade = False
            trade = None
            delivery_bad_count = 0
            pending_exit_reason = None
            continue

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
                soft_filters_passed=sig.get("soft_count", 0),
                conviction_score=sig.get("details", {}).get("conv_score", 0),
                rdv_pass=sig.get("details", {}).get("rdv", False),
                mcs_pass=sig.get("details", {}).get("mcs", False),
                cwc_pass=sig.get("details", {}).get("cwc", False),
                grad_pass=sig.get("details", {}).get("grad", False),
                regime_at_entry=sig.get("details", {}).get("regime", "-"),
                entry_tag=sig.get("details", {}).get("entry_tag", ""),
                psz_at_entry=psz_now if not np.isnan(psz_now) else 0.0,
                psz_peak=psz_now if not np.isnan(psz_now) else 0.0,
            )
            peak_close = close
            delivery_bad_count = 0
            in_trade = True

        else:
            qualifies, soft_count, fdetails = signal.check_entry(row, prev, entry_cfg, records, i)
            if qualifies:
                pending_signal = {"soft_count": soft_count, "details": fdetails}

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




def compute_profit_factor(trades: list[Trade]) -> float:
    """Gross profits / gross losses. Inf if no losses."""
    gross_win = sum(t.pnl_pct for t in trades if t.pnl_pct > 0)
    gross_loss = abs(sum(t.pnl_pct for t in trades if t.pnl_pct <= 0))
    if gross_loss == 0:
        return float("inf")
    return gross_win / gross_loss


def compute_expectancy(trades: list[Trade]) -> float:
    """Per-trade expectancy = avg_win * win_rate - avg_loss * loss_rate."""
    if not trades:
        return 0.0
    winners = [t.pnl_pct for t in trades if t.pnl_pct > 0]
    losers = [t.pnl_pct for t in trades if t.pnl_pct <= 0]
    n = len(trades)
    avg_win = np.mean(winners) if winners else 0.0
    avg_loss = abs(np.mean(losers)) if losers else 0.0
    wr = len(winners) / n
    lr = len(losers) / n
    return avg_win * wr - avg_loss * lr


def run_period(symbols: list[str], start: str, end: str,
               entry_cfg: BaseEntryConfig, exit_cfg: BaseExitConfig,
               label: str, signal) -> list[Trade]:
    all_trades = []
    failed = []
    for sym in symbols:
        try:
            # Always run the engine with full history to ensure indicators are fully warmed up
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            result = engine.run()
            
            # The simulator iterates the full history but only returns trades
            # that were entered within the [start, end] window.
            trades = simulate_trades(sym, result.ledger, entry_cfg, exit_cfg, signal)
            
            # Filter trades to only those entered in the requested period
            period_trades = [t for t in trades if start <= str(t.entry_date) <= end]
            all_trades.extend(period_trades)
        except Exception as e:
            print(f"Failed {sym}: {repr(e)}")
            failed.append((sym, str(e)))

    traded = len(set(t.symbol for t in all_trades))
    print(f"  {label}: {len(all_trades)} trades across {traded}/{len(symbols)} symbols"
          f" | {len(failed)} failed")
    return all_trades


def summarize(trades: list[Trade], label: str, period_start: str, period_end: str, out: io.StringIO) -> dict:
    """Build summary stats and write formatted report to `out`."""
    def w(line: str = ""):
        out.write(line + "\n")

    if not trades:
        w(f"\n{label}: No trades.")
        return {}

    df = pd.DataFrame([{
        "symbol": t.symbol,
        "pnl": t.pnl_pct, "mfe": t.mfe_pct, "mae": t.mae_pct,
        "bars": t.duration,
        "reason": t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason),
        "entry_tag": t.entry_tag.value if hasattr(t.entry_tag, "value") else str(t.entry_tag),
        "entry_date": t.entry_date, "exit_date": t.exit_date,
    } for t in trades])

    total = len(df)
    winners = (df["pnl"] > 0).sum()
    losers = total - winners
    win_rate = winners / total * 100
    avg_pnl = df["pnl"].mean()
    avg_win = df.loc[df["pnl"] > 0, "pnl"].mean() if winners > 0 else 0
    avg_loss = df.loc[df["pnl"] <= 0, "pnl"].mean() if losers > 0 else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
    profit_factor = compute_profit_factor(trades)
    expectancy = compute_expectancy(trades)
    median_pnl = df["pnl"].median()
    symbols_traded = df["symbol"].nunique()

    w(f"\n{SEP}")
    w(f"  {label} RESULTS  ({period_start} to {period_end})")
    w(SEP)
    w()
    w(f"  {'Metric':<22} {'Value':>10}")
    w(f"  {THIN_SEP[:34]}")
    w(f"  {'Trades':<22} {total:>10}")
    w(f"  {'Symbols traded':<22} {symbols_traded:>10}")
    w(f"  {'Winners / Losers':<22} {f'{winners} / {losers}':>10}")
    w(f"  {'Win rate':<22} {win_rate:>9.1f}%")
    w(f"  {'Avg P&L':<22} {avg_pnl:>+9.2f}%")
    w(f"  {'Median P&L':<22} {median_pnl:>+9.2f}%")
    w(f"  {'Avg winner':<22} {avg_win:>+9.2f}%")
    w(f"  {'Avg loser':<22} {avg_loss:>+9.2f}%")
    w(f"  {'Payoff ratio':<22} {payoff:>9.2f}x")
    w(f"  {'Profit factor':<22} {profit_factor:>9.2f}")
    w(f"  {'Expectancy / trade':<22} {expectancy:>+9.2f}%")
    # CAGR and Max Drawdown omitted — overlapping multi-symbol trades make them misleading
    w(f"  {'Avg MFE':<22} {df['mfe'].mean():>+9.2f}%")
    w(f"  {'Avg MAE':<22} {df['mae'].mean():>9.2f}%")
    w(f"  {'Avg duration':<22} {df['bars'].mean():>8.1f} bars")

    # Exit breakdown
    reason_agg = (
        df.groupby("reason")
        .agg(count=("pnl", "size"), avg_pnl=("pnl", "mean"),
             win_rate=("pnl", lambda x: round((x > 0).mean() * 100, 1)))
        .reset_index()
        .sort_values("count", ascending=False)
        .round(2)
    )
    w(f"\n  Exit Breakdown:")
    w(tabulate(reason_agg, headers=["Exit Reason", "Count", "Avg P&L%", "Win%"],
               tablefmt="simple", floatfmt=".2f", showindex=False))

    # Entry type breakdown
    entry_agg = (
        df.groupby("entry_tag")
        .agg(count=("pnl", "size"), avg_pnl=("pnl", "mean"),
             win_rate=("pnl", lambda x: round((x > 0).mean() * 100, 1)),
             avg_mfe=("mfe", "mean"), avg_mae=("mae", "mean"),
             avg_bars=("bars", "mean"))
        .reset_index()
        .sort_values("count", ascending=False)
        .round(2)
    )
    w(f"\n  Entry Type Breakdown:")
    w(tabulate(entry_agg,
               headers=["Entry Type", "Count", "Avg P&L%", "Win%", "Avg MFE%", "Avg MAE%", "Avg Bars"],
               tablefmt="simple", floatfmt=".2f", showindex=False))

    # Entry Type x Exit Reason cross-tabulation
    cross = (
        df.groupby(["entry_tag", "reason"])
        .agg(count=("pnl", "size"), avg_pnl=("pnl", "mean"),
             win_rate=("pnl", lambda x: round((x > 0).mean() * 100, 1)),
             avg_mfe=("mfe", "mean"), avg_mae=("mae", "mean"))
        .reset_index()
        .sort_values(["entry_tag", "count"], ascending=[True, False])
        .round(2)
    )
    w(f"\n  Entry Type x Exit Reason:")
    w(tabulate(cross,
               headers=["Entry Type", "Exit Reason", "Count", "Avg P&L%", "Win%", "Avg MFE%", "Avg MAE%"],
               tablefmt="simple", floatfmt=".2f", showindex=False))

    # Top / bottom symbols
    sym_agg = (
        df.groupby("symbol")
        .agg(count=("pnl", "size"), total_pnl=("pnl", "sum"), avg_pnl=("pnl", "mean"),
             win_rate=("pnl", lambda x: round((x > 0).mean() * 100, 1)))
        .reset_index()
        .round(2)
    )
    top5 = sym_agg.nlargest(5, "total_pnl")
    bot5 = sym_agg.nsmallest(5, "total_pnl")
    w(f"\n  Top 5 Symbols (by total P&L%):")
    w(tabulate(top5, headers=["Symbol", "Trades", "Total P&L%", "Avg P&L%", "Win%"],
               tablefmt="simple", floatfmt=".2f", showindex=False))
    w(f"\n  Bottom 5 Symbols (by total P&L%):")
    w(tabulate(bot5, headers=["Symbol", "Trades", "Total P&L%", "Avg P&L%", "Win%"],
               tablefmt="simple", floatfmt=".2f", showindex=False))

    return {
        "trades": total, "win_rate": round(win_rate, 1),
        "avg_pnl": round(avg_pnl, 2), "payoff": round(payoff, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy": round(expectancy, 2),
        "avg_mfe": round(df["mfe"].mean(), 2), "avg_mae": round(df["mae"].mean(), 2),
        "median_pnl": round(median_pnl, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Walk-forward validation")
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--signal", default="savgol_cts", choices=["price_divergence", "nextgen", "savgol_cts"],
                        help="Signal strategy to use (default: savgol_cts)")
    args = parser.parse_args()

    test_end = today_str()

    symbols = get_watchlist_symbols(args.watchlist)

    # Build output buffer — everything goes here, then gets printed + saved
    out = io.StringIO()

    def w(line: str = ""):
        out.write(line + "\n")

    w(SEP)
    w(f"  WALK-FORWARD BACKTEST REPORT")
    w(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w(SEP)
    w()
    w(f"  Watchlist:  {args.watchlist} ({len(symbols)} symbols)")
    w(f"  Signal:     {args.signal}")
    w(f"  Train:      {TRAIN_START} to {TRAIN_END}")
    w(f"  Test:       {TEST_START} to {test_end}")
    w()

    if args.signal == "nextgen":
        entry_cfg = NextGenEntryConfig()
        exit_cfg = NextGenExitConfig()
    elif args.signal == "savgol_cts":
        entry_cfg = SavgolCTSEntryConfig()
        exit_cfg = SavgolCTSExitConfig()
    else:
        entry_cfg = PriceDivergenceEntryConfig()
        exit_cfg = PriceDivergenceExitConfig()

    signal = SignalFactory.get_signal(args.signal)

    # --- Run periods ---
    print(f"Running TRAIN period ({TRAIN_START} to {TRAIN_END})...", flush=True)
    train_trades = run_period(symbols, TRAIN_START, TRAIN_END, entry_cfg, exit_cfg, "TRAIN", signal)
    print(f"Running TEST period ({TEST_START} to {test_end})...", flush=True)
    test_trades = run_period(symbols, TEST_START, test_end, entry_cfg, exit_cfg, "TEST", signal)

    # --- Summarize ---
    train_stats = summarize(train_trades, "TRAIN", TRAIN_START, TRAIN_END, out)
    test_stats = summarize(test_trades, "TEST", TEST_START, test_end, out)

    # --- Stability comparison ---
    if train_stats and test_stats:
        w(f"\n{SEP}")
        w("  PARAMETER STABILITY  (Test vs Train)")
        w(SEP)
        w()
        w(f"  {'Metric':<22} {'Train':>10} {'Test':>10} {'Delta':>10}")
        w(f"  {THIN_SEP[:54]}")
        compare_keys = [
            ("trades",         "",  0),
            ("win_rate",       "%", 1),
            ("avg_pnl",        "%", 2),
            ("median_pnl",     "%", 2),
            ("payoff",         "x", 2),
            ("profit_factor",  "",  2),
            ("expectancy",     "%", 2),
            ("avg_mfe",        "%", 2),
            ("avg_mae",        "%", 2),
        ]
        for key, suffix, dec in compare_keys:
            t = train_stats.get(key, 0)
            s = test_stats.get(key, 0)
            delta = s - t
            fmt = f".{dec}f"
            w(f"  {key:<22} {t:>9{fmt}}{suffix} {s:>9{fmt}}{suffix} {delta:>+9{fmt}}{suffix}")

    # --- Config dump for reproducibility ---
    w(f"\n{SEP}")
    w("  CONFIG SNAPSHOT")
    w(SEP)
    w()
    w(f"  Entry: {entry_cfg.__class__.__name__}")
    for field_name in sorted(vars(entry_cfg)):
        val = getattr(entry_cfg, field_name)
        if hasattr(val, '__dataclass_fields__'):
            w(f"    {field_name}:")
            for sub in sorted(vars(val)):
                w(f"      {sub}: {getattr(val, sub)}")
        else:
            w(f"    {field_name}: {val}")
    w(f"  Exit: {exit_cfg.__class__.__name__}")
    for field_name in sorted(vars(exit_cfg)):
        val = getattr(exit_cfg, field_name)
        if hasattr(val, '__dataclass_fields__'):
            w(f"    {field_name}:")
            for sub in sorted(vars(val)):
                w(f"      {sub}: {getattr(val, sub)}")
        else:
            w(f"    {field_name}: {val}")

    w(f"\n{SEP}")

    # --- Output ---
    report = out.getvalue()
    print(report)

    # Write to file
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%d-%b-%Y_%H:%M")
    sanitized_wl = args.watchlist.replace(" ", "_")
    filename = f"{sanitized_wl}_bt_{ts}.txt"
    outpath = OUTPUT_DIR / filename
    outpath.write_text(report)
    print(f"Report saved to {outpath}")


if __name__ == "__main__":
    main()
