"""
PSZ Crossing Entry Prototype — catches earlier inflections than CTS -1.

Entry: PSZ crosses psz_buy_threshold (P10) upward + gates
Exit variants:
  A) PSZ hits psz_sell_threshold (P70)
  B) PSZ drops back to psz_buy_threshold (like CTS BT exit)
  C) PSZ hits psz_sell_threshold OR drops to buy_threshold (whichever first, after rising)
  D) CTS-based: CTS hits cts_sell_threshold (P90) — institutional confirmation

Gates tested:
  - coherence <= 0.3
  - pdd_120 > -10
  - regime != uptrend
  - CTS < 0 (institutional still weak — pure PSZ play, not redundant with CTS -1)

Usage:
    venv/bin/python3 scripts/psz_entry_prototype.py
    venv/bin/python3 scripts/psz_entry_prototype.py --watchlist "NIFTY 500"
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


@dataclass
class TradeRecord:
    symbol: str
    entry_idx: int
    entry_date: str
    entry_price: float
    regime: str = ""
    cts_at_entry: float = 0.0
    psz_at_entry: float = 0.0
    coh_at_entry: float = 0.0
    exits: dict = field(default_factory=dict)


def check_psz_crossing(row: dict, prev_row: dict) -> bool:
    """PSZ crosses psz_buy_threshold upward."""
    psz = row.get("price_slope_z", np.nan)
    psz_prev = prev_row.get("price_slope_z", np.nan)
    bt = row.get("psz_buy_threshold", np.nan)
    if np.isnan(psz) or np.isnan(psz_prev) or np.isnan(bt):
        return False
    return psz_prev < bt and psz >= bt


GATE_CONFIGS = {
    # name -> (description, gate_fn)
    "ungated": ("PSZ cross only", lambda r, p: True),
    "no_uptrend": ("+ no uptrend", lambda r, p: r.get("regime", "") != "uptrend"),
    "coh03": ("+ coh<=0.3 + no uptrend",
              lambda r, p: r.get("regime", "") != "uptrend"
              and not np.isnan(r.get("coherence", np.nan))
              and r.get("coherence", 1.0) <= 0.3),
    "coh03_pdd": ("+ coh<=0.3 + pdd>-10 + no uptrend",
                  lambda r, p: r.get("regime", "") != "uptrend"
                  and not np.isnan(r.get("coherence", np.nan))
                  and r.get("coherence", 1.0) <= 0.3
                  and not (not np.isnan(r.get("pdd_120", np.nan)) and r.get("pdd_120", 0) <= -10.0)),
    "coh03_pdd_ctsneg": ("+ coh<=0.3 + pdd>-10 + CTS<0 + no uptrend",
                         lambda r, p: r.get("regime", "") != "uptrend"
                         and not np.isnan(r.get("coherence", np.nan))
                         and r.get("coherence", 1.0) <= 0.3
                         and not (not np.isnan(r.get("pdd_120", np.nan)) and r.get("pdd_120", 0) <= -10.0)
                         and not np.isnan(r.get("cts", np.nan))
                         and r.get("cts", 0) < 0),
    "coh03_pdd_ctslt05": ("+ coh<=0.3 + pdd>-10 + CTS<-0.5 + no uptrend",
                          lambda r, p: r.get("regime", "") != "uptrend"
                          and not np.isnan(r.get("coherence", np.nan))
                          and r.get("coherence", 1.0) <= 0.3
                          and not (not np.isnan(r.get("pdd_120", np.nan)) and r.get("pdd_120", 0) <= -10.0)
                          and not np.isnan(r.get("cts", np.nan))
                          and r.get("cts", 0) < -0.5),
}


def simulate(ticker: str, df: pd.DataFrame, gate_name: str) -> list[TradeRecord]:
    """Simulate PSZ crossing trades with multiple exit strategies."""
    gate_fn = GATE_CONFIGS[gate_name][1]
    records = df.to_dict("records")
    n = len(records)
    trades: list[TradeRecord] = []

    in_trade = False
    trade: TradeRecord | None = None
    pending_entry = False

    # Exit state per strategy
    a_exited = b_exited = c_exited = d_exited = False
    # For B/C: track if PSZ has risen above buy_threshold (don't exit on entry bar)
    psz_rose = False

    def _exit(trade, strat, idx, row, reason):
        close = row.get("close", np.nan)
        pnl = (close / trade.entry_price - 1) * 100 if trade.entry_price > 0 else 0
        trade.exits[strat] = {
            "exit_idx": idx,
            "exit_date": str(row.get("date", ""))[:10],
            "pnl_pct": round(pnl, 2),
            "reason": reason,
            "bars": idx - trade.entry_idx,
        }

    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue

        psz = row.get("price_slope_z", np.nan)
        psz_bt = row.get("psz_buy_threshold", np.nan)
        psz_st = row.get("psz_sell_threshold", np.nan)
        cts = row.get("cts", np.nan)
        cts_st = row.get("cts_sell_threshold", np.nan)

        if in_trade and trade is not None:
            # Track PSZ having risen above buy_threshold
            if not np.isnan(psz) and not np.isnan(psz_bt) and psz > psz_bt:
                psz_rose = True

            # A: exit when PSZ hits sell_threshold (P70)
            if not a_exited:
                if not np.isnan(psz) and not np.isnan(psz_st) and psz >= psz_st:
                    _exit(trade, "A", i, row, "psz_hit_sell")
                    a_exited = True

            # B: exit when PSZ drops back to buy_threshold (after having risen)
            if not b_exited and psz_rose:
                if not np.isnan(psz) and not np.isnan(psz_bt) and psz <= psz_bt:
                    _exit(trade, "B", i, row, "psz_hit_bt")
                    b_exited = True

            # C: exit when PSZ hits sell_threshold OR drops to buy_threshold
            if not c_exited:
                if not np.isnan(psz) and not np.isnan(psz_st) and psz >= psz_st:
                    _exit(trade, "C", i, row, "psz_hit_sell")
                    c_exited = True
                elif psz_rose and not np.isnan(psz) and not np.isnan(psz_bt) and psz <= psz_bt:
                    _exit(trade, "C", i, row, "psz_hit_bt")
                    c_exited = True

            # D: exit when CTS hits sell_threshold (P90)
            if not d_exited:
                if not np.isnan(cts) and not np.isnan(cts_st) and cts >= cts_st:
                    _exit(trade, "D", i, row, "cts_hit_sell")
                    d_exited = True

            if a_exited and b_exited and c_exited and d_exited:
                trades.append(trade)
                in_trade = False
                trade = None

        elif pending_entry:
            pending_entry = False
            trade = TradeRecord(
                symbol=ticker, entry_idx=i,
                entry_date=str(row.get("date", ""))[:10],
                entry_price=close,
                regime=str(row.get("regime", "")),
                cts_at_entry=cts if not np.isnan(cts) else 0.0,
                psz_at_entry=psz if not np.isnan(psz) else 0.0,
                coh_at_entry=row.get("coherence", 0.0),
            )
            a_exited = b_exited = c_exited = d_exited = False
            psz_rose = False
            in_trade = True

        else:
            if check_psz_crossing(row, prev) and gate_fn(row, prev):
                pending_entry = True

    # Close open trades
    if in_trade and trade is not None:
        last = records[-1]
        for s in ["A", "B", "C", "D"]:
            exited = {"A": a_exited, "B": b_exited, "C": c_exited, "D": d_exited}
            if not exited[s]:
                _exit(trade, s, n - 1, last, "end_of_data")
        trades.append(trade)

    return trades


def summarize(trades: list[TradeRecord], label: str):
    if not trades:
        print(f"\n{label}: No trades.")
        return

    strat_names = {
        "A": "PSZ hit sell_thresh (P70)",
        "B": "PSZ drop to buy_thresh",
        "C": "PSZ sell OR buy_thresh",
        "D": "CTS hit sell_thresh (P90)",
    }

    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    print(f"  Trades: {len(trades)}")

    rows = []
    for s in ["A", "B", "C", "D"]:
        pnls = [t.exits[s]["pnl_pct"] for t in trades if s in t.exits]
        bars = [t.exits[s]["bars"] for t in trades if s in t.exits]
        if not pnls:
            continue
        pnls = np.array(pnls)
        n = len(pnls)
        winners = (pnls > 0).sum()
        win_pct = winners / n * 100
        avg_pnl = pnls.mean()
        avg_win = pnls[pnls > 0].mean() if (pnls > 0).any() else 0
        avg_loss = pnls[pnls <= 0].mean() if (pnls <= 0).any() else 0
        payoff = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
        avg_bars = np.mean(bars)
        rows.append({
            "Exit": f"{s} {strat_names[s]}",
            "N": n, "Win%": f"{win_pct:.1f}%",
            "AvgPnL": f"{avg_pnl:+.2f}%",
            "AvgWin": f"{avg_win:+.2f}%",
            "AvgLoss": f"{avg_loss:+.2f}%",
            "Payoff": f"{payoff:.2f}x",
            "Bars": f"{avg_bars:.1f}",
        })
    print(tabulate(rows, headers="keys", tablefmt="simple"))

    # Exit breakdown for best strategy (C)
    for s in ["C"]:
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
            print(f"\n  {s} Exit Breakdown:")
            for r, d in sorted(reasons.items(), key=lambda x: -x[1]["count"]):
                p = np.array(d["pnls"])
                print(f"    {r}: N={d['count']}, avg={p.mean():+.2f}%, win={((p>0).mean()*100):.1f}%")

    # Regime breakdown for C
    regime_data = []
    for t in trades:
        if "C" in t.exits:
            regime_data.append({"regime": t.regime, "pnl": t.exits["C"]["pnl_pct"]})
    if regime_data:
        rdf = pd.DataFrame(regime_data)
        ragg = (rdf.groupby("regime")
                .agg(n=("pnl", "size"), avg=("pnl", "mean"),
                     win=("pnl", lambda x: f"{(x>0).mean()*100:.1f}%"))
                .reset_index().sort_values("n", ascending=False))
        ragg["avg"] = ragg["avg"].apply(lambda x: f"{x:+.2f}%")
        print(f"\n  C Regime Breakdown:")
        print(tabulate(ragg.values, headers=["Regime", "N", "AvgPnL", "Win%"], tablefmt="simple"))

    # CTS at entry distribution
    cts_vals = [t.cts_at_entry for t in trades]
    if cts_vals:
        arr = np.array(cts_vals)
        print(f"\n  CTS at entry: mean={arr.mean():.3f}, med={np.median(arr):.3f}, "
              f"[{arr.min():.3f}, {arr.max():.3f}]")


def main():
    parser = argparse.ArgumentParser(description="PSZ entry prototype")
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--start", default=START)
    parser.add_argument("--end", default=END)
    args = parser.parse_args()

    symbols = get_watchlist_symbols(args.watchlist)

    print(f"PSZ Crossing Entry Prototype")
    print(f"Watchlist: {args.watchlist} ({len(symbols)} symbols)")
    print(f"Period: {args.start} to {args.end}")

    # Run each gate config
    for gate_name, (desc, _) in GATE_CONFIGS.items():
        all_trades = []
        for i, sym in enumerate(symbols, 1):
            if gate_name == list(GATE_CONFIGS.keys())[0]:
                print(f"  [{i}/{len(symbols)}] {sym}...", end=" ", flush=True)
            try:
                engine = DivergenceEngine(sym, start_date=args.start, end_date=args.end)
                result = engine.run()
                sym_trades = simulate(sym, result.ledger, gate_name)
                all_trades.extend(sym_trades)
                if gate_name == list(GATE_CONFIGS.keys())[0]:
                    print(f"{len(sym_trades)} trades")
            except Exception as e:
                if gate_name == list(GATE_CONFIGS.keys())[0]:
                    print(f"SKIP — {e}")

        summarize(all_trades, f"Gate: {gate_name} ({desc})")


if __name__ == "__main__":
    main()
