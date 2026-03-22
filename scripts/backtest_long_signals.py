"""
Long-Only Signal Backtester with Smart Exits.

Entry: PSZ crosses -0.25 upward (bearish momentum fading) + quality filters.
Exit:  Multi-condition system — hard stop, CWVAP trail, delivery deterioration,
       coherence breakdown, MCS collapse, PSZ reversal backstop, time decay.

Usage:
    venv/bin/python3 scripts/backtest_long_signals.py --watchlist "NIFTY 50"
    venv/bin/python3 scripts/backtest_long_signals.py --watchlist "NIFTY 50" --psz-threshold -0.20
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
from src.trading.signals import (
    FAVORABLE_SHAPES, PriceDivergenceEntryConfig, PriceDivergenceExitConfig,
    NextGenEntryConfig, NextGenExitConfig,
    SavgolCTSEntryConfig, SavgolCTSExitConfig,
    Trade, SignalFactory,
)
from src.trading.signals.base import BaseEntryConfig, BaseExitConfig

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"


# ── Watchlist ────────────────────────────────────────────────────────────────

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


# ── Trade Simulation ─────────────────────────────────────────────────────────

def simulate_trades(
    ticker: str, df: pd.DataFrame,
    entry_cfg: BaseEntryConfig, exit_cfg: BaseExitConfig,
    signal
) -> list[Trade]:
    """Walk through ledger bar-by-bar, enter and exit trades."""

    records = df.to_dict("records")
    n = len(records)
    trades = []
    in_trade = False
    trade = None
    peak_close = 0.0
    delivery_bad_count = 0
    # EOD lag: signal fires on bar i, we enter on bar i+1 (next day close)
    pending_signal: dict | None = None

    # Track full CWVAP history for exit logic
    # Initialize with CWVAP of the first bar (index 0)
    cwvap_values = [records[0].get("cwvap", np.nan)]
    # EOD lag for exits: signal fires on bar i, execute at open of bar i+1
    pending_exit_reason: str | None = None

    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            # Append CWVAP for this bar even if close is NaN, to maintain history length
            cwvap_values.append(row.get("cwvap", np.nan))
            continue

        # Track CWVAP history for the current bar (index i)
        cw = row.get("cwvap", np.nan)
        cwvap_values.append(cw)

        # Execute pending exit at today's open (EOD-lag: signal fired previous bar)
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
            # Update peak
            if close > peak_close:
                peak_close = close

            bars_held = i - trade.entry_idx
            mfe = max(trade.mfe_pct, (close / trade.entry_price - 1) * 100)
            mae_val = (close / trade.entry_price - 1) * 100
            mae = min(-trade.mae_pct, mae_val)  # mae_pct stored as positive
            trade.mfe_pct = mfe
            if -mae > trade.mae_pct:
                trade.mae_pct = -mae

            reason, delivery_bad_count = signal.check_exit(
                row, prev, trade, peak_close, bars_held,
                delivery_bad_count, cwvap_values, exit_cfg,
                records, i,
            )

            if reason:
                # EOD lag: schedule exit at next bar's open
                pending_exit_reason = reason

        elif pending_signal is not None:
            # Day after signal — execute entry at today's close
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
                rdv_pass=sig.get("details", {}).get("rdv", False),
                mcs_pass=sig.get("details", {}).get("mcs", False),
                cwc_pass=sig.get("details", {}).get("cwc", False),
                grad_pass=sig.get("details", {}).get("grad", False),
                regime_at_entry=sig.get("details", {}).get("regime", ""),
                entry_tag=sig.get("details", {}).get("entry_tag", ""),
                psz_at_entry=psz_now if not np.isnan(psz_now) else 0.0,
                psz_peak=psz_now if not np.isnan(psz_now) else 0.0,
            )
            peak_close = close
            delivery_bad_count = 0
            in_trade = True

        else:
            # Check for new signal (will enter next bar)
            qualifies, soft_count, fdetails = signal.check_entry(row, prev, entry_cfg, records, i)
            if qualifies:
                pending_signal = {"soft_count": soft_count, "details": fdetails}

    return trades


def analyse_symbol(
    ticker: str, entry_cfg: BaseEntryConfig, exit_cfg: BaseExitConfig, signal
) -> list[Trade]:
    try:
        engine = DivergenceEngine(ticker)
        result = engine.run()
    except Exception as e:
        print(f"SKIP — {e}")
        return []
    return simulate_trades(ticker, result.ledger, entry_cfg, exit_cfg, signal)


# ── Output ───────────────────────────────────────────────────────────────────

# ── Output ───────────────────────────────────────────────────────────────────

def print_results(all_trades: list[Trade]):
    if not all_trades:
        print("\nNo trades generated.")
        return

    # Create DataFrame for analysis
    data = []
    for t in all_trades:
        data.append({
            "symbol": t.symbol,
            "entry": t.entry_date,
            "exit": t.exit_date,
            "reason": t.exit_reason,
            "pnl": t.pnl_pct,
            "mfe": t.mfe_pct,
            "mae": t.mae_pct,
            "bars": t.duration,
            "filters": t.soft_filters_passed,
            "rdv_pass": t.rdv_pass,
            "mcs_pass": t.mcs_pass,
            "cwc_pass": t.cwc_pass,
            "grad_pass": t.grad_pass,
            "regime": t.regime_at_entry,
        })
    df = pd.DataFrame(data)

    # ── Table 0: Detailed Trade Log ──────────────────────────────────────
    print("\n" + "=" * 100)
    print("DETAILED TRADE LOG")
    print("=" * 100)
    log_df = df[["symbol", "entry", "exit", "pnl", "mfe", "mae", "bars", "reason"]].copy()
    log_df.columns = ["Symbol", "Entry", "Exit", "PnL%", "MFE%", "MAE/MFA%", "Days", "Reason"]
    print(tabulate(
        log_df, headers="keys", tablefmt="simple", floatfmt=".2f", showindex=False
    ))

    # ── Table 1: Per-symbol summary ──────────────────────────────────────
    sym_agg = (
        df.groupby("symbol")
        .agg(
            count=("pnl", "size"),
            win_rate=("pnl", lambda x: round((x > 0).mean() * 100, 1)),
            avg_pnl=("pnl", "mean"),
            avg_mfe=("mfe", "mean"),
            avg_mae=("mae", "mean"),
            avg_bars=("bars", "mean"),
        )
        .reset_index()
        .round(2)
    )
    print("\n" + "=" * 100)
    print("PER-SYMBOL SUMMARY")
    print("=" * 100)
    print(tabulate(
        sym_agg, headers=["Symbol", "Trades", "Win%", "Avg PnL%", "Avg MFE%", "Avg MAE%", "Avg Bars"],
        tablefmt="simple", floatfmt=".2f", showindex=False,
    ))

    # ── Table 2: Aggregate ───────────────────────────────────────────────
    total = len(df)
    winners = (df["pnl"] > 0).sum()
    print("\n" + "=" * 100)
    print("AGGREGATE SUMMARY")
    print("=" * 100)
    print(f"  Total trades:  {total}")
    print(f"  Win rate:      {winners/total*100:.1f}%")
    print(f"  Avg P&L:       {df['pnl'].mean():+.2f}%")
    print(f"  Med P&L:       {df['pnl'].median():+.2f}%")
    print(f"  Avg MFE:       {df['mfe'].mean():.2f}%")
    print(f"  Avg MAE:       {df['mae'].mean():.2f}%")
    print(f"  Avg duration:  {df['bars'].mean():.1f} bars")
    avg_win = df.loc[df["pnl"] > 0, "pnl"].mean() if winners > 0 else 0
    avg_loss = df.loc[df["pnl"] <= 0, "pnl"].mean() if (total - winners) > 0 else 0
    print(f"  Avg winner:    {avg_win:+.2f}%")
    print(f"  Avg loser:     {avg_loss:+.2f}%")
    if avg_loss != 0:
        print(f"  Payoff ratio:  {abs(avg_win/avg_loss):.2f}x")

    # ── Table 3: Exit reason breakdown ───────────────────────────────────
    reason_agg = (
        df.groupby("reason")
        .agg(
            count=("pnl", "size"),
            pct=("pnl", lambda x: round(len(x) / total * 100, 1)),
            avg_pnl=("pnl", "mean"),
            win_rate=("pnl", lambda x: round((x > 0).mean() * 100, 1)),
            avg_bars=("bars", "mean"),
        )
        .reset_index()
        .sort_values("count", ascending=False)
        .round(2)
    )
    print("\n" + "=" * 100)
    print("EXIT REASON BREAKDOWN")
    print("=" * 100)
    print(tabulate(
        reason_agg, headers=["Exit Reason", "Count", "% Total", "Avg PnL%", "Win%", "Avg Bars"],
        tablefmt="simple", floatfmt=".2f", showindex=False,
    ))

    # ── Table 4: Filter quality ──────────────────────────────────────────
    filter_agg = (
        df.groupby("filters")
        .agg(
            count=("pnl", "size"),
            win_rate=("pnl", lambda x: round((x > 0).mean() * 100, 1)),
            avg_pnl=("pnl", "mean"),
            avg_mfe=("mfe", "mean"),
        )
        .reset_index()
        .round(2)
    )
    print("\n" + "=" * 100)
    print("ENTRY FILTER QUALITY (by soft filters passed)")
    print("=" * 100)
    print(tabulate(
        filter_agg, headers=["Filters Passed", "Trades", "Win%", "Avg PnL%", "Avg MFE%"],
        tablefmt="simple", floatfmt=".2f", showindex=False,
    ))


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Long-only signal backtester")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--watchlist", help="Watchlist name to backtest")
    group.add_argument("--symbol", help="Single symbol to backtest")

    parser.add_argument("--start-date", help="Start date for entry signals (YYYY-MM-DD)")
    parser.add_argument("--signal", choices=["price_divergence", "nextgen", "savgol_cts"], default="price_divergence",
                        help="Signal strategy to use (default: price_divergence)")

    args = parser.parse_args()

    if args.signal == "nextgen":
        entry_cfg = NextGenEntryConfig()
        exit_cfg = NextGenExitConfig()
    elif args.signal == "savgol_cts":
        entry_cfg = SavgolCTSEntryConfig()
        exit_cfg = SavgolCTSExitConfig()
    else:
        entry_cfg = PriceDivergenceEntryConfig()
        exit_cfg = PriceDivergenceExitConfig()

    if args.symbol:
        symbols = [args.symbol]
    else:
        symbols = get_watchlist_symbols(args.watchlist)

    print(f"Signal: {args.signal}")
    print(f"Symbols: {symbols}")
    if args.start_date:
        print(f"Start date: {args.start_date}")

    all_trades = []
    signal = SignalFactory.get_signal(args.signal)
    
    for i, sym in enumerate(symbols, 1):
        print(f"  [{i}/{len(symbols)}] {sym}...", end=" ", flush=True)
        try:
            engine = DivergenceEngine(sym)
            result = engine.run()
            df = result.ledger
            
            # If start_date is provided, we only look for ENTRIES after that date.
            # But the simulation needs context (prev bars), so we pass the full ledger
            # and filter entry logic inside simulate_trades or by slicing the loop.
            
            # Filtering entries by start_date:
            trades = simulate_trades(sym, df, entry_cfg, exit_cfg, signal)
            
            if args.start_date:
                trades = [t for t in trades if t.entry_date >= args.start_date]
                
            all_trades.extend(trades)
            wins = sum(1 for t in trades if t.pnl_pct > 0)
            print(f"{len(trades)} trades, {wins} wins" if trades else "no trades")
            
        except Exception as e:
            print(f"SKIP \u2014 {e}")

    print_results(all_trades)


if __name__ == "__main__":
    main()
