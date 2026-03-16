#!/usr/bin/env python3
"""
Backtest: CTS + PSZ combined signal strategy.

Entry:  PSZ rising from below threshold toward 0, AND CTS is rising.
Exit:   CTS meaningfully reverses (falls from peak by threshold).

Scans all symbols in a watchlist, collects per-trade stats.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from dataclasses import dataclass

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"


# ── Helpers ──────────────────────────────────────────────────────────────────

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


# ── Config ───────────────────────────────────────────────────────────────────

@dataclass
class CTSPSZConfig:
    # Entry — PSZ crossing + CTS inflection (acceleration)
    psz_entry_threshold: float = -0.25    # PSZ must cross this upward
    cts_slope_min: float = 0.0            # CTS slope must be >= this (0 = rising)
    cts_accel_min: float = 0.0            # CTS accel must be >= this (0 = bending up)
    require_accel: bool = True            # use acceleration for entry
    require_slope: bool = False           # use slope for entry

    # Exit — CTS slope turns negative (trend reversal)
    exit_cts_slope_max: float = 0.0       # exit when CTS slope drops below this
    exit_cts_accel_max: float = 0.0       # exit when CTS accel drops below this
    exit_on_slope: bool = True            # exit on slope turning negative
    exit_on_accel: bool = False           # exit on accel turning negative

    # Hard stop (ATR-based, safety net)
    stop_atr_multiple: float = 2.0

    # Max holding period
    max_bars: int = 60


@dataclass
class Trade:
    symbol: str
    entry_date: str
    entry_price: float
    entry_idx: int
    atr_at_entry: float
    psz_at_entry: float
    cts_at_entry: float
    cts_slope_at_entry: float = 0.0
    cts_accel_at_entry: float = 0.0
    exit_date: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_pct: float = 0.0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    duration: int = 0
    cts_slope_peak: float = 0.0


# ── Entry Logic ──────────────────────────────────────────────────────────────

def check_entry(records: list[dict], idx: int, cfg: CTSPSZConfig) -> bool:
    if idx < 2:
        return False

    row = records[idx]
    prev = records[idx - 1]

    psz = row.get("price_slope_z", np.nan)
    prev_psz = prev.get("price_slope_z", np.nan)

    if np.isnan(psz) or np.isnan(prev_psz):
        return False

    # PSZ must cross threshold upward
    if not (prev_psz < cfg.psz_entry_threshold <= psz):
        return False

    # CTS acceleration: curve is bending upward (2nd derivative positive)
    if cfg.require_accel:
        accel = row.get("cts_accel", np.nan)
        if np.isnan(accel) or accel < cfg.cts_accel_min:
            return False

    # CTS slope: trend is already rising (1st derivative positive)
    if cfg.require_slope:
        slope = row.get("cts_slope", np.nan)
        if np.isnan(slope) or slope < cfg.cts_slope_min:
            return False

    return True


# ── Exit Logic ───────────────────────────────────────────────────────────────

def check_exit(row: dict, trade: Trade, bars_held: int,
               cfg: CTSPSZConfig) -> str | None:
    close = row["close"]
    entry = trade.entry_price
    atr_pct = trade.atr_at_entry / entry if entry > 0 else 0.02

    pnl_pct = (close / entry - 1) * 100
    cts_slope = row.get("cts_slope", 0)

    # Track CTS slope peak
    if cts_slope > trade.cts_slope_peak:
        trade.cts_slope_peak = cts_slope

    # 1. Hard stop
    if pnl_pct < -(cfg.stop_atr_multiple * atr_pct * 100):
        return "hard_stop"

    # 2. CTS slope turns negative — trend is now falling
    if cfg.exit_on_slope and cts_slope < cfg.exit_cts_slope_max:
        return "cts_slope_neg"

    # 3. CTS acceleration turns negative — trend is bending downward
    if cfg.exit_on_accel:
        accel = row.get("cts_accel", 0)
        if accel < cfg.exit_cts_accel_max:
            return "cts_accel_neg"

    # 4. Time decay
    if bars_held >= cfg.max_bars:
        return "time_decay"

    return None


# ── Simulate ─────────────────────────────────────────────────────────────────

def simulate_trades(ticker: str, df: pd.DataFrame, cfg: CTSPSZConfig) -> list[Trade]:
    records = df.to_dict("records")
    trades: list[Trade] = []
    in_trade = False
    trade = None
    peak_close = 0.0

    for i in range(1, len(records)):
        row = records[i]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue

        if not in_trade:
            if check_entry(records, i, cfg):
                # EOD lag: signal today, enter next day
                if i + 1 < len(records):
                    entry_row = records[i + 1]
                    entry_close = entry_row.get("close", np.nan)
                    if np.isnan(entry_close):
                        continue
                    trade = Trade(
                        symbol=ticker,
                        entry_date=str(entry_row["date"])[:10],
                        entry_price=entry_close,
                        entry_idx=i + 1,
                        atr_at_entry=row.get("atr_20", entry_close * 0.02),
                        psz_at_entry=row.get("price_slope_z", 0),
                        cts_at_entry=row.get("cts", 0),
                        cts_slope_at_entry=row.get("cts_slope", 0),
                        cts_accel_at_entry=row.get("cts_accel", 0),
                    )
                    peak_close = entry_close
                    in_trade = True
        else:
            bars_held = i - trade.entry_idx

            # Track MFE/MAE
            if close > peak_close:
                peak_close = close
            trade.mfe_pct = max(trade.mfe_pct, (close / trade.entry_price - 1) * 100)
            trade.mae_pct = min(trade.mae_pct, (close / trade.entry_price - 1) * 100)

            reason = check_exit(row, trade, bars_held, cfg)
            if reason:
                trade.exit_date = str(row["date"])[:10]
                trade.exit_price = close
                trade.exit_reason = reason
                trade.pnl_pct = (close / trade.entry_price - 1) * 100
                trade.duration = bars_held
                trades.append(trade)
                in_trade = False
                trade = None

    # Close any open trade at end
    if in_trade and trade:
        last = records[-1]
        trade.exit_date = str(last["date"])[:10]
        trade.exit_price = last["close"]
        trade.exit_reason = "end_of_data"
        trade.pnl_pct = (last["close"] / trade.entry_price - 1) * 100
        trade.duration = len(records) - 1 - trade.entry_idx
        trades.append(trade)

    return trades


def analyse_symbol(ticker: str, cfg: CTSPSZConfig) -> list[Trade]:
    try:
        engine = DivergenceEngine(ticker)
        result = engine.run()
        return simulate_trades(ticker, result.ledger, cfg)
    except Exception as e:
        print(f"ERROR: {e}")
        return []


# ── Reporting ────────────────────────────────────────────────────────────────

def print_summary(all_trades: list[Trade], label: str = ""):
    if not all_trades:
        print("No trades.")
        return

    pnls = [t.pnl_pct for t in all_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_rate = len(wins) / len(pnls) * 100 if pnls else 0
    avg_pnl = np.mean(pnls)
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
    avg_dur = np.mean([t.duration for t in all_trades])
    avg_mfe = np.mean([t.mfe_pct for t in all_trades])
    avg_mae = np.mean([t.mae_pct for t in all_trades])

    # Exit reason breakdown
    reasons = {}
    for t in all_trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    header = f"{'=' * 60}"
    if label:
        header = f"\n{header}\n  {label}\n{header}"
    print(header)
    print(f"  Trades:       {len(all_trades)}")
    print(f"  Win Rate:     {win_rate:.1f}%")
    print(f"  Avg P&L:      {avg_pnl:+.2f}%")
    print(f"  Avg Winner:   {avg_win:+.2f}%")
    print(f"  Avg Loser:    {avg_loss:+.2f}%")
    print(f"  Payoff Ratio: {payoff:.2f}x")
    print(f"  Avg Duration: {avg_dur:.1f} bars")
    print(f"  Avg MFE:      {avg_mfe:+.2f}%")
    print(f"  Avg MAE:      {avg_mae:+.2f}%")
    print(f"  MFE left:     {avg_mfe - avg_pnl:+.2f}%")
    print(f"\n  Exit reasons:")
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        pnl_for_reason = [t.pnl_pct for t in all_trades if t.exit_reason == reason]
        print(f"    {reason:20s}  {count:4d}  avg {np.mean(pnl_for_reason):+.2f}%")
    print()


def _collect_stats(all_trades):
    """Compute summary stats for a list of trades."""
    if not all_trades:
        return None
    pnls = [t.pnl_pct for t in all_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / len(pnls) * 100
    avg_pnl = np.mean(pnls)
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    return {
        "trades": len(all_trades),
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff": payoff,
        "avg_dur": np.mean([t.duration for t in all_trades]),
    }


def sweep_params(symbols: list[str]):
    """Run a parameter sweep over entry/exit mode combinations."""
    configs = []

    for psz_thresh in [-0.30, -0.25, -0.20]:
        # A: entry=accel, exit=slope (accel bends up → slope turns down)
        configs.append(("accel→slope", CTSPSZConfig(
            psz_entry_threshold=psz_thresh,
            require_accel=True, require_slope=False,
            exit_on_slope=True, exit_on_accel=False,
        )))
        # B: entry=accel, exit=accel (accel bends up → accel bends down)
        configs.append(("accel→accel", CTSPSZConfig(
            psz_entry_threshold=psz_thresh,
            require_accel=True, require_slope=False,
            exit_on_slope=False, exit_on_accel=True,
        )))
        # C: entry=slope, exit=slope (slope rising → slope turns down)
        configs.append(("slope→slope", CTSPSZConfig(
            psz_entry_threshold=psz_thresh,
            require_accel=False, require_slope=True,
            exit_on_slope=True, exit_on_accel=False,
        )))
        # D: entry=accel+slope, exit=slope (both confirm → slope reversal)
        configs.append(("both→slope", CTSPSZConfig(
            psz_entry_threshold=psz_thresh,
            require_accel=True, require_slope=True,
            exit_on_slope=True, exit_on_accel=False,
        )))
        # E: entry=accel, exit=slope+accel (enter on bend, exit on either)
        configs.append(("accel→both", CTSPSZConfig(
            psz_entry_threshold=psz_thresh,
            require_accel=True, require_slope=False,
            exit_on_slope=True, exit_on_accel=True,
        )))

    print(f"\nSweeping {len(configs)} configurations across {len(symbols)} symbols...\n")

    results = []
    for ci, (mode_label, cfg) in enumerate(configs):
        all_trades = []
        for sym in symbols:
            all_trades.extend(analyse_symbol(sym, cfg))

        stats = _collect_stats(all_trades)
        if not stats:
            continue

        stats["psz_thresh"] = cfg.psz_entry_threshold
        stats["mode"] = mode_label
        results.append(stats)

        print(f"  [{ci+1:3d}/{len(configs)}] PSZ={cfg.psz_entry_threshold:5.2f} {mode_label:15s}  "
              f"trades={stats['trades']:4d}  WR={stats['win_rate']:5.1f}%  "
              f"avg={stats['avg_pnl']:+5.2f}%  payoff={stats['payoff']:.2f}x  "
              f"dur={stats['avg_dur']:.1f}")

    # Sort and print top configs
    results.sort(key=lambda r: r["avg_pnl"], reverse=True)
    print(f"\n{'=' * 100}")
    print(f"  TOP CONFIGURATIONS (by avg P&L)")
    print(f"{'=' * 100}")
    print(f"  {'PSZ':>6s}  {'Mode':>15s}  {'Trades':>6s}  {'WR%':>5s}  "
          f"{'AvgPnL':>7s}  {'AvgWin':>7s}  {'AvgLoss':>7s}  {'Payoff':>7s}  {'Dur':>5s}")
    print(f"  {'-'*6}  {'-'*15}  {'-'*6}  {'-'*5}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*5}")
    for r in results:
        print(f"  {r['psz_thresh']:6.2f}  {r['mode']:>15s}  "
              f"{r['trades']:6d}  {r['win_rate']:5.1f}  {r['avg_pnl']:+7.2f}  "
              f"{r['avg_win']:+7.2f}  {r['avg_loss']:+7.2f}  {r['payoff']:7.2f}  "
              f"{r['avg_dur']:5.1f}")

    return results


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CTS+PSZ Signal Backtest (slope/accel)")
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--sweep", action="store_true", help="Run parameter sweep")
    parser.add_argument("--psz-threshold", type=float, default=-0.25)
    parser.add_argument("--entry-mode", choices=["accel", "slope", "both"], default="accel",
                        help="Entry requires: accel>0, slope>0, or both")
    parser.add_argument("--exit-mode", choices=["slope", "accel", "both"], default="slope",
                        help="Exit when: slope<0, accel<0, or either")
    args = parser.parse_args()

    symbols = get_watchlist_symbols(args.watchlist)
    print(f"Watchlist: {args.watchlist} ({len(symbols)} symbols)")

    if args.sweep:
        sweep_params(symbols)
        return

    cfg = CTSPSZConfig(
        psz_entry_threshold=args.psz_threshold,
        require_accel=args.entry_mode in ("accel", "both"),
        require_slope=args.entry_mode in ("slope", "both"),
        exit_on_slope=args.exit_mode in ("slope", "both"),
        exit_on_accel=args.exit_mode in ("accel", "both"),
    )

    entry_desc = args.entry_mode
    exit_desc = args.exit_mode
    print(f"Config: PSZ threshold={cfg.psz_entry_threshold}, "
          f"entry={entry_desc}, exit={exit_desc}")

    all_trades = []
    for i, sym in enumerate(symbols, 1):
        print(f"  [{i}/{len(symbols)}] {sym}...", end=" ", flush=True)
        trades = analyse_symbol(sym, cfg)
        print(f"{len(trades)} trades")
        all_trades.extend(trades)

    print_summary(all_trades, f"CTS+PSZ Strategy — {args.watchlist}")

    # Per-symbol breakdown
    sym_stats = {}
    for t in all_trades:
        sym_stats.setdefault(t.symbol, []).append(t.pnl_pct)

    print(f"\n  Per-symbol (top 10 by avg P&L):")
    ranked = sorted(sym_stats.items(), key=lambda x: np.mean(x[1]), reverse=True)
    for sym, pnls in ranked[:10]:
        print(f"    {sym:15s}  {len(pnls):3d} trades  avg={np.mean(pnls):+.2f}%  "
              f"WR={sum(1 for p in pnls if p > 0)/len(pnls)*100:.0f}%")

    print(f"\n  Worst 5:")
    for sym, pnls in ranked[-5:]:
        print(f"    {sym:15s}  {len(pnls):3d} trades  avg={np.mean(pnls):+.2f}%  "
              f"WR={sum(1 for p in pnls if p > 0)/len(pnls)*100:.0f}%")


if __name__ == "__main__":
    main()
