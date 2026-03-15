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
    FAVORABLE_SHAPES, EntryConfig, ExitConfig, Trade, check_entry, check_exit,
)

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
    # EOD lag: signal fires on bar i, we enter on bar i+1 (next day close)
    pending_signal: dict | None = None

    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue

        # Track CWVAP history
        cw = row.get("cwvap", np.nan)
        cwvap_values.append(cw)

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
            # Check for new signal (will enter next bar)
            qualifies, soft_count, fdetails = check_entry(row, prev, entry_cfg, records, i)
            if qualifies:
                pending_signal = {"soft_count": soft_count, "details": fdetails}

    # Close any open trade at end of data
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


def analyse_symbol(
    ticker: str, entry_cfg: EntryConfig, exit_cfg: ExitConfig,
) -> list[Trade]:
    try:
        engine = DivergenceEngine(ticker)
        result = engine.run()
    except Exception as e:
        print(f"SKIP — {e}")
        return []
    return simulate_trades(ticker, result.ledger, entry_cfg, exit_cfg)


# ── Output ───────────────────────────────────────────────────────────────────

def print_results(all_trades: list[Trade]):
    if not all_trades:
        print("\nNo trades generated.")
        return

    df = pd.DataFrame([
        {
            "symbol": t.symbol, "entry": t.entry_date, "exit": t.exit_date,
            "reason": t.exit_reason, "pnl": t.pnl_pct, "mfe": t.mfe_pct,
            "mae": t.mae_pct, "bars": t.duration, "filters": t.soft_filters_passed,
            "rdv_pass": t.rdv_pass, "mcs_pass": t.mcs_pass,
            "cwc_pass": t.cwc_pass, "grad_pass": t.grad_pass,
            "regime": t.regime_at_entry,
        }
        for t in all_trades
    ])

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

    # ── Table 5: Individual filter contribution ──────────────────────────
    print("\n" + "=" * 100)
    print("INDIVIDUAL FILTER CONTRIBUTION")
    print("=" * 100)
    for filt in ["rdv_pass", "mcs_pass", "cwc_pass", "grad_pass"]:
        label = filt.replace("_pass", "").upper()
        on = df[df[filt] == True]
        off = df[df[filt] == False]
        on_wr = (on["pnl"] > 0).mean() * 100 if len(on) > 0 else 0
        off_wr = (off["pnl"] > 0).mean() * 100 if len(off) > 0 else 0
        on_pnl = on["pnl"].mean() if len(on) > 0 else 0
        off_pnl = off["pnl"].mean() if len(off) > 0 else 0
        on_mfe = on["mfe"].mean() if len(on) > 0 else 0
        off_mfe = off["mfe"].mean() if len(off) > 0 else 0
        print(f"  {label:6s}  ON: {len(on):4d} trades, {on_wr:5.1f}% win, {on_pnl:+.2f}% avg, {on_mfe:.2f}% MFE"
              f"   |   OFF: {len(off):4d} trades, {off_wr:5.1f}% win, {off_pnl:+.2f}% avg, {off_mfe:.2f}% MFE")

    # ── Table 6: Regime at entry breakdown ────────────────────────────────
    if df["regime"].nunique() > 0:
        regime_agg = (
            df.groupby("regime")
            .agg(
                count=("pnl", "size"),
                win_rate=("pnl", lambda x: round((x > 0).mean() * 100, 1)),
                avg_pnl=("pnl", "mean"),
                avg_mfe=("mfe", "mean"),
                avg_mae=("mae", "mean"),
            )
            .reset_index()
            .sort_values("count", ascending=False)
            .round(2)
        )
        print("\n" + "=" * 100)
        print("REGIME AT ENTRY")
        print("=" * 100)
        print(tabulate(
            regime_agg, headers=["Regime", "Trades", "Win%", "Avg PnL%", "Avg MFE%", "Avg MAE%"],
            tablefmt="simple", floatfmt=".2f", showindex=False,
        ))

    # ── Table 7: MFE left on table (trail exits) ─────────────────────────
    trail_df = df[df["reason"].isin(["trail_atr", "trail_cwvap"])]
    if len(trail_df) > 0:
        leftover = trail_df["mfe"] - trail_df["pnl"]
        print("\n" + "=" * 100)
        print("MFE LEFT ON TABLE (trail exits only)")
        print("=" * 100)
        print(f"  Trail exits:       {len(trail_df)}")
        print(f"  Avg MFE:           {trail_df['mfe'].mean():.2f}%")
        print(f"  Avg exit PnL:      {trail_df['pnl'].mean():+.2f}%")
        print(f"  Avg left on table: {leftover.mean():.2f}%")
        print(f"  Med left on table: {leftover.median():.2f}%")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Long-only signal backtester")
    parser.add_argument("--watchlist", required=True)
    parser.add_argument("--psz-threshold", type=float, default=-0.25)
    parser.add_argument("--stop-atr", type=float, default=2.0)
    parser.add_argument("--trail-atr", type=float, default=1.0)
    parser.add_argument("--max-bars", type=int, default=40)
    parser.add_argument("--min-filters", type=int, default=2)
    parser.add_argument("--no-regime-gate", action="store_true", help="Disable regime hard gate")
    parser.add_argument("--trail-activation", type=float, default=0.5, help="Trail activation ATR multiple")
    parser.add_argument("--pdd120", type=float, default=None, help="PDD_120 max threshold for quality gate")
    parser.add_argument("--rsz-falling", action="store_true", help="Require RSZ 3-bar delta < 0")
    args = parser.parse_args()

    entry_cfg = EntryConfig(
        psz_threshold=args.psz_threshold,
        min_soft_filters=args.min_filters,
        regime_block=() if args.no_regime_gate else ("downtrend",),
        pdd_120_max=args.pdd120,
        rsz_falling=args.rsz_falling,
    )
    exit_cfg = ExitConfig(
        stop_atr_multiple=args.stop_atr,
        trail_atr_fallback=args.trail_atr,
        trail_activation_atr=args.trail_activation,
        psz_exit_threshold=args.psz_threshold,
        max_bars=args.max_bars,
    )

    symbols = get_watchlist_symbols(args.watchlist)
    print(f"Watchlist: {args.watchlist} ({len(symbols)} symbols)")
    print(f"PSZ threshold: {entry_cfg.psz_threshold}, Min filters: {entry_cfg.min_soft_filters}")
    print(f"Stop: {exit_cfg.stop_atr_multiple} ATR, Trail: CWVAP primary / {exit_cfg.trail_atr_fallback} ATR fallback")
    print(f"Max bars: {exit_cfg.max_bars}\n")

    all_trades = []
    for i, sym in enumerate(symbols, 1):
        print(f"  [{i}/{len(symbols)}] {sym}...", end=" ", flush=True)
        trades = analyse_symbol(sym, entry_cfg, exit_cfg)
        all_trades.extend(trades)
        wins = sum(1 for t in trades if t.pnl_pct > 0)
        print(f"{len(trades)} trades, {wins} wins" if trades else "no trades")

    print_results(all_trades)


if __name__ == "__main__":
    main()
