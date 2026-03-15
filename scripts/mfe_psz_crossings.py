"""
MFE/MAE analysis for PSZ zero-crossings.

Finds bars where price_slope_z crosses key thresholds *towards zero*:
  - From below -0.25 / -0.20 to above (bearish momentum fading → long MFE)
  - From above +0.25 / +0.20 to below (bullish momentum fading → short MFE)

Tracks both MFE (max favourable) and MAE (max adverse) excursion from entry
until PSZ crosses back through the threshold (move is over).

Also tracks the closing P&L at the point PSZ reverses (exit_pnl).

Usage:
    venv/bin/python3 scripts/mfe_psz_crossings.py --watchlist "NIFTY 50"
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine

# ── Crossing thresholds ──────────────────────────────────────────────────────
LONG_THRESHOLDS = [-0.25, -0.20]   # PSZ crosses these upward (towards zero)
SHORT_THRESHOLDS = [0.25, 0.20]    # PSZ crosses these downward (towards zero)

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"


@dataclass
class TradeResult:
    mfe: float       # max favourable excursion %
    mae: float       # max adverse excursion % (always positive — how much it went against you)
    exit_pnl: float  # P&L % at exit (when PSZ reverses)
    bars: int        # duration in bars


def get_watchlist_symbols(watchlist_name: str) -> list[str]:
    db = sqlite3.connect(str(DB_PATH))
    row = db.execute(
        "SELECT id FROM watchlists WHERE name = ?", (watchlist_name,)
    ).fetchone()
    if not row:
        available = [r[0] for r in db.execute("SELECT name FROM watchlists ORDER BY name").fetchall()]
        db.close()
        print(f"Watchlist '{watchlist_name}' not found. Available: {available}")
        sys.exit(1)
    symbols = [
        r[0] for r in db.execute(
            "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY display_order",
            (row[0],),
        ).fetchall()
    ]
    db.close()
    return symbols


def find_crossings(psz: np.ndarray, threshold: float, direction: str) -> list[int]:
    """Return indices where PSZ crosses threshold towards zero."""
    crossings = []
    for i in range(1, len(psz)):
        if np.isnan(psz[i]) or np.isnan(psz[i - 1]):
            continue
        if direction == "long" and psz[i - 1] < threshold <= psz[i]:
            crossings.append(i)
        elif direction == "short" and psz[i - 1] > threshold >= psz[i]:
            crossings.append(i)
    return crossings


def compute_trade(
    close: np.ndarray, psz: np.ndarray,
    entry_idx: int, threshold: float, direction: str,
) -> TradeResult | None:
    """Track MFE, MAE, and exit P&L from entry until PSZ crosses back."""
    entry_price = close[entry_idx]
    if entry_price <= 0 or np.isnan(entry_price):
        return None

    n = len(close)
    best = 0.0    # MFE: max move in our favour
    worst = 0.0   # MAE: max move against us
    last_pnl = 0.0
    bars = 0

    for j in range(entry_idx + 1, n):
        bars = j - entry_idx
        p = close[j]
        if np.isnan(p):
            continue

        if direction == "long":
            pnl = (p / entry_price - 1) * 100
        else:
            pnl = (1 - p / entry_price) * 100

        best = max(best, pnl)
        worst = min(worst, pnl)  # most negative = worst drawdown
        last_pnl = pnl

        # Exit: PSZ crosses back through threshold (away from zero)
        if not np.isnan(psz[j]):
            if direction == "long" and psz[j] < threshold:
                break
            if direction == "short" and psz[j] > threshold:
                break

    return TradeResult(
        mfe=float(best),
        mae=float(abs(worst)),  # flip sign: MAE is always positive
        exit_pnl=float(last_pnl),
        bars=bars,
    )


def analyse_symbol(ticker: str) -> list[dict]:
    """Run engine and compute MFE/MAE stats for all threshold crossings."""
    try:
        engine = DivergenceEngine(ticker)
        result = engine.run()
    except Exception as e:
        print(f"SKIP — {e}")
        return []

    df = result.ledger
    psz = df["price_slope_z"].values
    close = df["close"].values
    rows = []

    all_thresholds = [
        (LONG_THRESHOLDS, "long", "Long"),
        (SHORT_THRESHOLDS, "short", "Short"),
    ]

    for thresholds, direction, label in all_thresholds:
        for threshold in thresholds:
            crossings = find_crossings(psz, threshold, direction)
            trades = [compute_trade(close, psz, idx, threshold, direction) for idx in crossings]
            trades = [t for t in trades if t is not None]
            if not trades:
                continue

            mfes = [t.mfe for t in trades]
            maes = [t.mae for t in trades]
            exit_pnls = [t.exit_pnl for t in trades]
            durs = [t.bars for t in trades]
            winners = [t for t in trades if t.exit_pnl > 0]
            losers = [t for t in trades if t.exit_pnl <= 0]

            prefix = "↑" if direction == "long" else "↓"
            rows.append({
                "symbol": ticker,
                "crossing": f"PSZ {prefix} {threshold}",
                "direction": label,
                "count": len(trades),
                "win_rate": round(len(winners) / len(trades) * 100, 1),
                "avg_mfe": round(np.mean(mfes), 2),
                "med_mfe": round(np.median(mfes), 2),
                "avg_mae": round(np.mean(maes), 2),
                "med_mae": round(np.median(maes), 2),
                "avg_exit": round(np.mean(exit_pnls), 2),
                "med_exit": round(np.median(exit_pnls), 2),
                "max_mfe": round(np.max(mfes), 2),
                "max_mae": round(np.max(maes), 2),
                "avg_bars": round(np.mean(durs), 1),
            })

    return rows


def main():
    parser = argparse.ArgumentParser(description="MFE/MAE analysis for PSZ zero-crossings")
    parser.add_argument("--watchlist", required=True, help="Watchlist name (e.g. 'NIFTY 50')")
    args = parser.parse_args()

    symbols = get_watchlist_symbols(args.watchlist)
    print(f"Watchlist: {args.watchlist} ({len(symbols)} symbols)")
    print(f"MFE/MAE measured until PSZ crosses back through threshold\n")

    all_rows = []
    for i, sym in enumerate(symbols, 1):
        print(f"  [{i}/{len(symbols)}] {sym}...", end=" ", flush=True)
        rows = analyse_symbol(sym)
        all_rows.extend(rows)
        total_signals = sum(r["count"] for r in rows)
        print(f"{total_signals} signals" if rows else "no data")

    if not all_rows:
        print("\nNo crossings found.")
        return

    df = pd.DataFrame(all_rows)

    # ── Per-symbol table ─────────────────────────────────────────────────
    display_cols = [
        "symbol", "crossing", "direction", "count", "win_rate",
        "avg_mfe", "avg_mae", "avg_exit", "max_mfe", "max_mae", "avg_bars",
    ]
    print("\n" + "=" * 130)
    print("PER-SYMBOL MFE/MAE (until PSZ reverses)")
    print("=" * 130)
    print(tabulate(
        df[display_cols],
        headers=["Symbol", "Crossing", "Dir", "Count", "Win%",
                 "Avg MFE%", "Avg MAE%", "Avg Exit%", "Max MFE%", "Max MAE%", "Avg Bars"],
        tablefmt="simple",
        floatfmt=".2f",
        showindex=False,
    ))

    # ── Aggregate summary by crossing type ───────────────────────────────
    agg = (
        df
        .groupby(["crossing", "direction"])
        .agg(
            symbols=("symbol", "nunique"),
            total_signals=("count", "sum"),
            win_rate=("win_rate", "mean"),
            avg_mfe=("avg_mfe", "mean"),
            avg_mae=("avg_mae", "mean"),
            avg_exit=("avg_exit", "mean"),
            avg_bars=("avg_bars", "mean"),
        )
        .reset_index()
        .round(2)
    )

    print("\n" + "=" * 130)
    print("AGGREGATE SUMMARY (averaged across symbols)")
    print("=" * 130)
    print(tabulate(
        agg,
        headers=["Crossing", "Dir", "Symbols", "Signals", "Win%",
                 "Avg MFE%", "Avg MAE%", "Avg Exit%", "Avg Bars"],
        tablefmt="simple",
        floatfmt=".2f",
        showindex=False,
    ))


if __name__ == "__main__":
    main()
