"""
PSZ Exit Study — Can PSZ extremes save trades that CTS exits cut too early?

Current CTS -1/+1 system exits when CTS drops to buy_threshold or -1.
Hypothesis: if PSZ is still strongly positive at CTS exit, the trade has
more room to run. Use a clipped causal-SG PSZ as a hold/exit override.

Approach:
  1. Compute clipped PSZ: psz_clipped = clip(psz_smooth / scale, -1, +1)
     where scale normalises to fill the [-1, +1] range per stock
  2. Simulate CTS -1/+1 trades (current best config)
  3. At each CTS exit bar, record PSZ state
  4. Test variants:
     A) Baseline: exit on CTS BT/-1 (current)
     B) Hold override: if psz_clipped > 0 at CTS exit, hold until psz_clipped
        drops to psz_sell_threshold or CTS hits -1
     C) PSZ ceiling exit: exit only when psz_clipped hits +1 (or P90)
     D) Combined: CTS exit fires, but if psz_smooth > psz_sell_threshold,
        delay exit until psz_smooth < psz_sell_threshold

Usage:
    venv/bin/python3 scripts/psz_exit_study.py
    venv/bin/python3 scripts/psz_exit_study.py --watchlist "NIFTY 500"
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"

START = "2025-01-01"
END = "2026-03-15"

# PSZ clipping: use rolling P5/P95 to normalise per-stock, then clip to [-1, +1]
PSZ_CLIP_WINDOW = 120
PSZ_CLIP_PCT_LO = 5
PSZ_CLIP_PCT_HI = 95


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


def add_psz_clipped(df: pd.DataFrame) -> pd.DataFrame:
    """Add psz_clipped: psz_smooth normalised to [-1, +1] using rolling percentiles."""
    df = df.copy()
    psz_s = df["psz_smooth"]

    # Rolling P5 and P95 for adaptive normalisation
    lo = psz_s.rolling(PSZ_CLIP_WINDOW, min_periods=30).quantile(PSZ_CLIP_PCT_LO / 100.0)
    hi = psz_s.rolling(PSZ_CLIP_WINDOW, min_periods=30).quantile(PSZ_CLIP_PCT_HI / 100.0)

    # Normalise: map [lo, hi] -> [-1, +1]
    span = hi - lo
    span = span.replace(0, np.nan)
    normalised = 2.0 * (psz_s - lo) / span - 1.0
    df["psz_clipped"] = normalised.clip(-1.0, 1.0).round(4)

    # Also add rolling thresholds for psz_clipped
    df["psz_clip_bt"] = df["psz_clipped"].rolling(60, min_periods=30).quantile(0.10).round(4)
    df["psz_clip_st"] = df["psz_clipped"].rolling(60, min_periods=30).quantile(0.90).round(4)

    return df


@dataclass
class TradeRecord:
    symbol: str
    entry_idx: int
    entry_date: str
    entry_price: float
    # Filled per strategy
    exits: dict = field(default_factory=dict)  # strategy -> {exit_idx, exit_date, exit_price, pnl_pct, reason, bars}
    # State at CTS exit
    cts_exit_idx: int = 0
    psz_at_cts_exit: float = 0.0
    psz_clipped_at_cts_exit: float = 0.0


def check_entry(row: dict) -> bool:
    cts = row.get("cts", np.nan)
    coh = row.get("coherence", np.nan)
    pdd = row.get("pdd_120", np.nan)
    regime = row.get("regime", "")
    if np.isnan(cts) or np.isnan(coh):
        return False
    if not np.isnan(pdd) and pdd <= -10.0:
        return False
    if regime == "uptrend":
        return False
    return cts <= -1.0 and coh <= 0.3


def simulate_all_strategies(ticker: str, df: pd.DataFrame) -> list[TradeRecord]:
    """Run all exit strategies in a single pass."""
    records = df.to_dict("records")
    n = len(records)
    trades: list[TradeRecord] = []

    in_trade = False
    trade: TradeRecord | None = None
    pending_entry = False

    # Per-strategy state
    # A: baseline CTS exit
    a_cts_rose = False
    a_exited = False
    # B: hold if psz_clipped > 0 at CTS exit
    b_cts_rose = False
    b_cts_exit_triggered = False
    b_exited = False
    # C: exit only on psz_clipped hitting psz_clip_st (P90)
    c_cts_rose = False
    c_exited = False
    # D: CTS exit, but delay if psz_smooth > psz_sell_threshold
    d_cts_rose = False
    d_cts_exit_triggered = False
    d_exited = False

    def _exit(trade, strat, idx, row, reason):
        close = row.get("close", np.nan)
        pnl = (close / trade.entry_price - 1) * 100 if trade.entry_price > 0 else 0
        trade.exits[strat] = {
            "exit_idx": idx,
            "exit_date": str(row.get("date", ""))[:10],
            "exit_price": close,
            "pnl_pct": round(pnl, 2),
            "reason": reason,
            "bars": idx - trade.entry_idx,
        }

    for i in range(1, n):
        row = records[i]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue

        cts = row.get("cts", np.nan)
        bt = row.get("cts_buy_threshold", np.nan)
        psz_s = row.get("psz_smooth", np.nan)
        psz_c = row.get("psz_clipped", np.nan)
        psz_st = row.get("psz_sell_threshold", np.nan)
        psz_clip_st = row.get("psz_clip_st", np.nan)

        if in_trade and trade is not None:
            # Track CTS rose for all strategies
            if not np.isnan(cts) and cts > -1.0:
                a_cts_rose = True
                b_cts_rose = True
                c_cts_rose = True
                d_cts_rose = True

            # Strategy A: baseline CTS exit
            if not a_exited and a_cts_rose and not np.isnan(cts):
                if not np.isnan(bt) and cts <= bt:
                    _exit(trade, "A", i, row, "cts_hit_bt")
                    a_exited = True
                    trade.cts_exit_idx = i
                    trade.psz_at_cts_exit = psz_s if not np.isnan(psz_s) else 0.0
                    trade.psz_clipped_at_cts_exit = psz_c if not np.isnan(psz_c) else 0.0
                elif cts <= -1.0:
                    _exit(trade, "A", i, row, "cts_hit_-1")
                    a_exited = True
                    trade.cts_exit_idx = i
                    trade.psz_at_cts_exit = psz_s if not np.isnan(psz_s) else 0.0
                    trade.psz_clipped_at_cts_exit = psz_c if not np.isnan(psz_c) else 0.0

            # Strategy B: hold if psz_clipped > 0 at CTS exit
            if not b_exited and b_cts_rose and not np.isnan(cts):
                if not b_cts_exit_triggered:
                    # Check for CTS exit condition
                    cts_wants_exit = (not np.isnan(bt) and cts <= bt) or cts <= -1.0
                    if cts_wants_exit:
                        if np.isnan(psz_c) or psz_c <= 0:
                            # PSZ not positive — exit normally
                            reason = "cts_hit_bt" if (not np.isnan(bt) and cts <= bt) else "cts_hit_-1"
                            _exit(trade, "B", i, row, reason)
                            b_exited = True
                        else:
                            # PSZ still positive — hold
                            b_cts_exit_triggered = True
                else:
                    # Holding past CTS exit — exit when psz_clipped drops to 0 or CTS hits -1
                    if np.isnan(psz_c) or psz_c <= 0:
                        _exit(trade, "B", i, row, "psz_dropped_zero")
                        b_exited = True
                    elif cts <= -1.0:
                        _exit(trade, "B", i, row, "cts_hit_-1_override")
                        b_exited = True

            # Strategy C: exit on psz_clipped hitting P90 (after CTS rose)
            if not c_exited and c_cts_rose:
                if not np.isnan(psz_c) and not np.isnan(psz_clip_st) and psz_c >= psz_clip_st:
                    _exit(trade, "C", i, row, "psz_hit_ceiling")
                    c_exited = True
                elif not np.isnan(cts) and cts <= -1.0:
                    # Backstop: if CTS drops back to -1, exit
                    _exit(trade, "C", i, row, "cts_hit_-1_backstop")
                    c_exited = True

            # Strategy D: CTS exit, but delay if psz_smooth > psz_sell_threshold
            if not d_exited and d_cts_rose and not np.isnan(cts):
                if not d_cts_exit_triggered:
                    cts_wants_exit = (not np.isnan(bt) and cts <= bt) or cts <= -1.0
                    if cts_wants_exit:
                        psz_strong = not np.isnan(psz_s) and not np.isnan(psz_st) and psz_s > psz_st
                        if not psz_strong:
                            reason = "cts_hit_bt" if (not np.isnan(bt) and cts <= bt) else "cts_hit_-1"
                            _exit(trade, "D", i, row, reason)
                            d_exited = True
                        else:
                            d_cts_exit_triggered = True
                else:
                    # Holding — exit when psz_smooth drops below psz_sell_threshold or CTS hits -1
                    psz_weak = np.isnan(psz_s) or np.isnan(psz_st) or psz_s <= psz_st
                    if psz_weak:
                        _exit(trade, "D", i, row, "psz_below_sell_thresh")
                        d_exited = True
                    elif cts <= -1.0:
                        _exit(trade, "D", i, row, "cts_hit_-1_override")
                        d_exited = True

            # Check if all strategies have exited
            if a_exited and b_exited and c_exited and d_exited:
                trades.append(trade)
                in_trade = False
                trade = None

        elif pending_entry:
            pending_entry = False
            trade = TradeRecord(
                symbol=ticker,
                entry_idx=i,
                entry_date=str(row.get("date", ""))[:10],
                entry_price=close,
            )
            a_cts_rose = b_cts_rose = c_cts_rose = d_cts_rose = False
            a_exited = b_exited = c_exited = d_exited = False
            b_cts_exit_triggered = d_cts_exit_triggered = False
            in_trade = True

        else:
            if check_entry(row):
                pending_entry = True

    # Close open trades at end of data
    if in_trade and trade is not None:
        last = records[-1]
        for strat in ["A", "B", "C", "D"]:
            exited = {"A": a_exited, "B": b_exited, "C": c_exited, "D": d_exited}
            if not exited[strat]:
                _exit(trade, strat, n - 1, last, "end_of_data")
        trades.append(trade)

    return trades


def summarize(trades: list[TradeRecord], label: str):
    if not trades:
        print(f"\n{label}: No trades.")
        return

    strategies = ["A", "B", "C", "D"]
    strat_names = {
        "A": "Baseline (CTS BT/-1)",
        "B": "Hold if psz_clip > 0",
        "C": "Exit on psz_clip P90",
        "D": "Delay if psz > sell_thresh",
    }

    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    print(f"  Total entry signals: {len(trades)}")

    rows = []
    for s in strategies:
        pnls = [t.exits[s]["pnl_pct"] for t in trades if s in t.exits]
        bars = [t.exits[s]["bars"] for t in trades if s in t.exits]
        if not pnls:
            continue
        pnls = np.array(pnls)
        bars_arr = np.array(bars)
        n = len(pnls)
        winners = (pnls > 0).sum()
        win_pct = winners / n * 100
        avg_pnl = pnls.mean()
        avg_win = pnls[pnls > 0].mean() if (pnls > 0).any() else 0
        avg_loss = pnls[pnls <= 0].mean() if (pnls <= 0).any() else 0
        payoff = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
        avg_bars = bars_arr.mean()

        rows.append({
            "Strategy": f"{s} {strat_names[s]}",
            "N": n,
            "Win%": f"{win_pct:.1f}%",
            "AvgPnL": f"{avg_pnl:+.2f}%",
            "AvgWin": f"{avg_win:+.2f}%",
            "AvgLoss": f"{avg_loss:+.2f}%",
            "Payoff": f"{payoff:.2f}x",
            "AvgBars": f"{avg_bars:.1f}",
        })

    print(tabulate(rows, headers="keys", tablefmt="simple"))

    # Exit reason breakdown per strategy
    for s in strategies:
        reasons = {}
        for t in trades:
            if s not in t.exits:
                continue
            r = t.exits[s]["reason"]
            if r not in reasons:
                reasons[r] = {"count": 0, "pnls": []}
            reasons[r]["count"] += 1
            reasons[r]["pnls"].append(t.exits[s]["pnl_pct"])

        if reasons:
            print(f"\n  {s} {strat_names[s]} — Exit Breakdown:")
            reason_rows = []
            for r, data in sorted(reasons.items(), key=lambda x: -x[1]["count"]):
                p = np.array(data["pnls"])
                reason_rows.append({
                    "Reason": r,
                    "Count": data["count"],
                    "AvgPnL": f"{p.mean():+.2f}%",
                    "Win%": f"{(p > 0).mean() * 100:.1f}%",
                })
            print(tabulate(reason_rows, headers="keys", tablefmt="simple"))

    # PSZ state at CTS exit — what did the overrides see?
    psz_at_exit = [(t.psz_clipped_at_cts_exit, t.exits.get("A", {}).get("pnl_pct", 0),
                    t.exits.get("B", {}).get("pnl_pct", 0))
                   for t in trades if "A" in t.exits and t.exits["A"]["reason"] != "end_of_data"]

    if psz_at_exit:
        print(f"\n  PSZ clipped at CTS exit (A baseline):")
        psz_arr = np.array([x[0] for x in psz_at_exit])
        a_pnls = np.array([x[1] for x in psz_at_exit])
        b_pnls = np.array([x[2] for x in psz_at_exit])

        bins = [(-1.01, -0.5), (-0.5, 0.0), (0.0, 0.3), (0.3, 0.6), (0.6, 1.01)]
        bin_rows = []
        for lo, hi in bins:
            mask = (psz_arr >= lo) & (psz_arr < hi)
            n = mask.sum()
            if n == 0:
                continue
            a_avg = a_pnls[mask].mean()
            b_avg = b_pnls[mask].mean()
            delta = b_avg - a_avg
            bin_rows.append({
                "PSZ range": f"[{lo:.1f}, {hi:.1f})",
                "N": n,
                "A AvgPnL": f"{a_avg:+.2f}%",
                "B AvgPnL": f"{b_avg:+.2f}%",
                "B-A delta": f"{delta:+.2f}%",
            })
        print(tabulate(bin_rows, headers="keys", tablefmt="simple"))


def main():
    parser = argparse.ArgumentParser(description="PSZ exit study")
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--start", default=START)
    parser.add_argument("--end", default=END)
    args = parser.parse_args()

    symbols = get_watchlist_symbols(args.watchlist)

    print(f"PSZ Exit Study")
    print(f"Watchlist: {args.watchlist} ({len(symbols)} symbols)")
    print(f"Period: {args.start} to {args.end}")
    print(f"Strategies:")
    print(f"  A: Baseline — exit on CTS BT/-1 (current)")
    print(f"  B: Hold override — if psz_clipped > 0 at CTS exit, hold until psz_clipped <= 0")
    print(f"  C: PSZ ceiling — exit when psz_clipped >= P90 (or CTS -1 backstop)")
    print(f"  D: Delay — if psz_smooth > sell_threshold at CTS exit, hold until below")

    all_trades = []
    for i, sym in enumerate(symbols, 1):
        print(f"  [{i}/{len(symbols)}] {sym}...", end=" ", flush=True)
        try:
            engine = DivergenceEngine(sym, start_date=args.start, end_date=args.end)
            result = engine.run()
            df = add_psz_clipped(result.ledger)
            sym_trades = simulate_all_strategies(sym, df)
            all_trades.extend(sym_trades)
            print(f"{len(sym_trades)} trades")
        except Exception as e:
            print(f"SKIP — {e}")

    summarize(all_trades, f"PSZ EXIT STUDY — {args.watchlist} ({args.start} to {args.end})")


if __name__ == "__main__":
    main()
