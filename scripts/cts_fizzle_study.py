"""
CTS Fizzle Study — analyze trades that enter at CTS=-1 but fail to reach CTS=+1.

Goals:
  1. Entry-bar feature comparison: completed vs fizzled trades
  2. In-trade feature evolution: when do fizzled trades lose momentum?
  3. Test candidate exit triggers: cts_slope, cts_accel, psz, etc.

Usage:
    venv/bin/python3 scripts/cts_fizzle_study.py
    venv/bin/python3 scripts/cts_fizzle_study.py --watchlist "NIFTY 50"
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
from src.trading.signals.enums import ExitReason

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"
OUT_PATH = Path(__file__).resolve().parent.parent / "output"

START = "2025-01-01"
END = "2026-03-15"

# Features to capture at entry and during the trade
FEATURES = [
    "cts", "cts_slope", "cts_accel",
    "velocity_60_norm", "pdd_120", "pdd_120_threshold",
    "price_slope_z", "regime",
    "psz_v", "psz_smooth",
    "coherence",
]


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


def collect_trades(symbols: list[str], start: str, end: str) -> list[dict]:
    """Collect all CTS=-1/+1 trades with per-bar feature snapshots."""
    all_trades = []

    for idx, sym in enumerate(symbols, 1):
        print(f"  [{idx}/{len(symbols)}] {sym}...", end=" ", flush=True)
        try:
            engine = DivergenceEngine(sym, start_date=start, end_date=end)
            result = engine.run()
            df = result.ledger.copy()
            records = df.to_dict("records")
            n = len(records)

            in_trade = False
            entry_idx = 0
            entry_price = 0.0
            entry_bar = {}
            pending = False
            trade_bars = []

            for i in range(1, n):
                row = records[i]
                close = row.get("close", np.nan)
                cts = row.get("cts", np.nan)
                if np.isnan(close) or np.isnan(cts):
                    continue

                if in_trade:
                    pnl_pct = (close / entry_price - 1) * 100
                    bars_held = i - entry_idx
                    # Snapshot features at this bar
                    bar_snap = {
                        "bar": bars_held,
                        "pnl_pct": pnl_pct,
                        "close": close,
                    }
                    for f in FEATURES:
                        bar_snap[f] = row.get(f, np.nan)
                    trade_bars.append(bar_snap)

                    # Check exit: CTS >= +1
                    if cts >= 1.0:
                        trade_rec = _build_trade(
                            sym, entry_bar, trade_bars, entry_price, close,
                            bars_held, pnl_pct, "cts_hit_+1",
                        )
                        all_trades.append(trade_rec)
                        in_trade = False
                        trade_bars = []

                elif pending:
                    pending = False
                    atr = row.get("atr_20", 0)
                    if atr <= 0 or np.isnan(atr):
                        continue
                    entry_price = close
                    entry_idx = i
                    # Capture entry bar features
                    entry_bar = {"atr": atr}
                    for f in FEATURES:
                        entry_bar[f] = row.get(f, np.nan)
                    entry_bar["regime"] = str(row.get("regime", "-"))
                    trade_bars = []
                    in_trade = True

                else:
                    if cts <= -1.0:
                        pending = True

            # Open trade at end of data
            if in_trade:
                last = records[-1]
                close = last.get("close", entry_price)
                pnl_pct = (close / entry_price - 1) * 100
                bars_held = n - 1 - entry_idx
                trade_rec = _build_trade(
                    sym, entry_bar, trade_bars, entry_price, close,
                    bars_held, pnl_pct, ExitReason.END_OF_DATA.value,
                )
                all_trades.append(trade_rec)

            count = sum(1 for t in all_trades if t["sym"] == sym)
            print(f"{count} trades")
        except Exception as e:
            print(f"SKIP — {e}")

    return all_trades


def _build_trade(sym, entry_bar, trade_bars, entry_price, exit_price,
                 duration, pnl_pct, exit_reason) -> dict:
    """Build a trade record with entry features and per-bar evolution."""
    # Compute MFE/MAE from trade bars
    pnls = [b["pnl_pct"] for b in trade_bars]
    mfe = max(pnls) if pnls else 0
    mae = min(pnls) if pnls else 0

    # Peak CTS during trade
    cts_vals = [b["cts"] for b in trade_bars if not np.isnan(b.get("cts", np.nan))]
    peak_cts = max(cts_vals) if cts_vals else np.nan

    # Bar where CTS peaked
    if cts_vals:
        peak_cts_bar = trade_bars[[b["cts"] for b in trade_bars].index(peak_cts)]["bar"]
    else:
        peak_cts_bar = np.nan

    # Slope trajectory: count bars where cts_slope < 0
    slope_neg_bars = sum(1 for b in trade_bars
                         if not np.isnan(b.get("cts_slope", np.nan)) and b["cts_slope"] < 0)
    slope_total_bars = sum(1 for b in trade_bars
                           if not np.isnan(b.get("cts_slope", np.nan)))

    # First bar where cts_slope goes negative after being positive
    first_slope_neg = np.nan
    was_positive = False
    for b in trade_bars:
        s = b.get("cts_slope", np.nan)
        if np.isnan(s):
            continue
        if s > 0:
            was_positive = True
        elif was_positive and s < 0:
            first_slope_neg = b["bar"]
            break

    # PnL at first slope negative
    pnl_at_first_slope_neg = np.nan
    if not np.isnan(first_slope_neg):
        for b in trade_bars:
            if b["bar"] == first_slope_neg:
                pnl_at_first_slope_neg = b["pnl_pct"]
                break

    # Early exit simulations: exit when cts_slope < 0 for N consecutive bars
    consec_neg = 0
    exit_sim = {}  # {N_bars: (exit_bar, exit_pnl)}
    for b in trade_bars:
        s = b.get("cts_slope", np.nan)
        if np.isnan(s):
            continue
        if s < 0:
            consec_neg += 1
        else:
            consec_neg = 0
        for n_thresh in [1, 2, 3, 5]:
            if n_thresh not in exit_sim and consec_neg >= n_thresh:
                exit_sim[n_thresh] = (b["bar"], b["pnl_pct"])

    # Exit when cts_accel < 0 for N consecutive bars
    consec_accel_neg = 0
    accel_exit_sim = {}
    for b in trade_bars:
        a = b.get("cts_accel", np.nan)
        if np.isnan(a):
            continue
        if a < 0:
            consec_accel_neg += 1
        else:
            consec_accel_neg = 0
        for n_thresh in [1, 2, 3, 5]:
            if n_thresh not in accel_exit_sim and consec_accel_neg >= n_thresh:
                accel_exit_sim[n_thresh] = (b["bar"], b["pnl_pct"])

    # Exit when CTS drops below a level after having risen
    cts_drop_exit = {}
    peak_so_far = -1.0
    for b in trade_bars:
        c = b.get("cts", np.nan)
        if np.isnan(c):
            continue
        if c > peak_so_far:
            peak_so_far = c
        for lvl in [0.0, -0.3, -0.5]:
            if lvl not in cts_drop_exit and peak_so_far > 0 and c < lvl:
                cts_drop_exit[lvl] = (b["bar"], b["pnl_pct"])

    rec = {
        "sym": sym,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl_pct": round(pnl_pct, 2),
        "mfe_pct": round(mfe, 2),
        "mae_pct": round(mae, 2),
        "duration": duration,
        "exit_reason": exit_reason,
        "peak_cts": round(peak_cts, 3) if not np.isnan(peak_cts) else np.nan,
        "peak_cts_bar": peak_cts_bar,
        "slope_neg_pct": round(slope_neg_bars / slope_total_bars * 100, 1) if slope_total_bars > 0 else np.nan,
        "first_slope_neg_bar": first_slope_neg,
        "pnl_at_first_slope_neg": round(pnl_at_first_slope_neg, 2) if not np.isnan(pnl_at_first_slope_neg) else np.nan,
        # Entry features
        "entry_regime": entry_bar.get("regime", "-"),
        "entry_cts_slope": entry_bar.get("cts_slope", np.nan),
        "entry_cts_accel": entry_bar.get("cts_accel", np.nan),
        "entry_velocity": entry_bar.get("velocity_60_norm", np.nan),
        "entry_pdd_120": entry_bar.get("pdd_120", np.nan),
        "entry_price_slope_z": entry_bar.get("price_slope_z", np.nan),
        "entry_psz_v": entry_bar.get("psz_v", np.nan),
        "entry_coherence": entry_bar.get("coherence", np.nan),
        "entry_atr": entry_bar.get("atr", np.nan),
    }

    # Add exit simulation columns
    for n in [1, 2, 3, 5]:
        if n in exit_sim:
            rec[f"slope_neg_{n}_bar"] = exit_sim[n][0]
            rec[f"slope_neg_{n}_pnl"] = round(exit_sim[n][1], 2)
        else:
            rec[f"slope_neg_{n}_bar"] = np.nan
            rec[f"slope_neg_{n}_pnl"] = np.nan

    for n in [1, 2, 3, 5]:
        if n in accel_exit_sim:
            rec[f"accel_neg_{n}_bar"] = accel_exit_sim[n][0]
            rec[f"accel_neg_{n}_pnl"] = round(accel_exit_sim[n][1], 2)
        else:
            rec[f"accel_neg_{n}_bar"] = np.nan
            rec[f"accel_neg_{n}_pnl"] = np.nan

    for lvl in [0.0, -0.3, -0.5]:
        key = f"cts_drop_{lvl:.1f}"
        if lvl in cts_drop_exit:
            rec[f"{key}_bar"] = cts_drop_exit[lvl][0]
            rec[f"{key}_pnl"] = round(cts_drop_exit[lvl][1], 2)
        else:
            rec[f"{key}_bar"] = np.nan
            rec[f"{key}_pnl"] = np.nan

    return rec


def analyze(trades: list[dict], label: str):
    df = pd.DataFrame(trades)
    total = len(df)
    completed = df[df["exit_reason"] == "cts_hit_+1"]
    fizzled = df[df["exit_reason"] == ExitReason.END_OF_DATA.value]

    out_lines = []
    def p(s=""):
        out_lines.append(s)
        print(s)

    p(f"\n{'='*80}")
    p(f"  CTS FIZZLE STUDY — {label}")
    p(f"  Total: {total}  |  Completed: {len(completed)}  |  Fizzled: {len(fizzled)}")
    p(f"{'='*80}")

    # ── 1. OVERVIEW ──
    p(f"\n── 1. OVERVIEW ──")
    for name, sub in [("Completed", completed), ("Fizzled", fizzled)]:
        if len(sub) == 0:
            continue
        wins = (sub["pnl_pct"] > 0).sum()
        p(f"\n  {name}: N={len(sub)}, Win%={wins/len(sub)*100:.1f}, "
          f"AvgPnL={sub['pnl_pct'].mean():+.2f}%, "
          f"AvgMFE={sub['mfe_pct'].mean():.2f}%, AvgMAE={sub['mae_pct'].mean():.2f}%, "
          f"AvgDur={sub['duration'].mean():.1f} bars")

    # ── 2. ENTRY FEATURE COMPARISON ──
    p(f"\n── 2. ENTRY FEATURE COMPARISON: Completed vs Fizzled ──")
    entry_features = [
        "entry_cts_slope", "entry_cts_accel", "entry_velocity",
        "entry_pdd_120", "entry_price_slope_z", "entry_psz_v", "entry_coherence",
    ]
    rows = []
    for f in entry_features:
        c_vals = completed[f].dropna()
        f_vals = fizzled[f].dropna()
        if len(c_vals) == 0 or len(f_vals) == 0:
            continue
        rows.append({
            "Feature": f.replace("entry_", ""),
            "Completed Mean": round(c_vals.mean(), 4),
            "Fizzled Mean": round(f_vals.mean(), 4),
            "Comp Med": round(c_vals.median(), 4),
            "Fizz Med": round(f_vals.median(), 4),
            "Diff (C-F)": round(c_vals.mean() - f_vals.mean(), 4),
        })
    if rows:
        p(tabulate(rows, headers="keys", tablefmt="simple", floatfmt=".4f"))

    # ── 3. REGIME BREAKDOWN ──
    p(f"\n── 3. REGIME AT ENTRY ──")
    for name, sub in [("Completed", completed), ("Fizzled", fizzled)]:
        if len(sub) == 0:
            continue
        regime_ct = sub["entry_regime"].value_counts()
        p(f"\n  {name}:")
        for r, ct in regime_ct.items():
            sub_r = sub[sub["entry_regime"] == r]
            p(f"    {r:12s}  N={ct:4d}  AvgPnL={sub_r['pnl_pct'].mean():+.2f}%  "
              f"Win%={((sub_r['pnl_pct']>0).sum()/len(sub_r)*100):.1f}")

    # ── 4. PEAK CTS ANALYSIS ──
    p(f"\n── 4. PEAK CTS REACHED DURING TRADE ──")
    p(f"  (How far did CTS recover before fizzling?)")
    bins = [-1.0, -0.5, 0.0, 0.3, 0.5, 0.7, 0.9, 1.01]
    labels_b = ["<-0.5", "-0.5–0.0", "0.0–0.3", "0.3–0.5", "0.5–0.7", "0.7–0.9", "0.9+"]
    for name, sub in [("Fizzled", fizzled), ("Completed", completed)]:
        if len(sub) == 0:
            continue
        sub = sub.copy()
        sub["peak_cts_bin"] = pd.cut(sub["peak_cts"], bins=bins, labels=labels_b, right=False)
        p(f"\n  {name}:")
        for b in labels_b:
            grp = sub[sub["peak_cts_bin"] == b]
            if len(grp) == 0:
                continue
            p(f"    Peak CTS {b:10s}  N={len(grp):4d}  AvgPnL={grp['pnl_pct'].mean():+.2f}%  "
              f"AvgDur={grp['duration'].mean():.0f}b")

    # ── 5. CTS SLOPE NEGATIVE EXIT SIMULATION ──
    p(f"\n── 5. EXIT SIMULATION: cts_slope < 0 for N consecutive bars ──")
    p(f"  Compare: actual outcome vs exiting when slope turns negative.")
    for n_thresh in [1, 2, 3, 5]:
        col_bar = f"slope_neg_{n_thresh}_bar"
        col_pnl = f"slope_neg_{n_thresh}_pnl"
        p(f"\n  --- slope < 0 for {n_thresh} bar(s) ---")
        for name, sub in [("Completed", completed), ("Fizzled", fizzled)]:
            if len(sub) == 0:
                continue
            triggered = sub[sub[col_pnl].notna()]
            not_triggered = sub[sub[col_pnl].isna()]
            if len(triggered) > 0:
                p(f"    {name}: triggered={len(triggered)}/{len(sub)}  "
                  f"AvgExitPnL={triggered[col_pnl].mean():+.2f}%  "
                  f"AvgExitBar={triggered[col_bar].mean():.1f}  "
                  f"(actual AvgPnL={triggered['pnl_pct'].mean():+.2f}%)  "
                  f"Saved={triggered['pnl_pct'].mean() - triggered[col_pnl].mean():+.2f}%")
            if len(not_triggered) > 0:
                p(f"    {name}: NOT triggered={len(not_triggered)}  "
                  f"AvgPnL={not_triggered['pnl_pct'].mean():+.2f}%")

    # ── 6. CTS ACCEL NEGATIVE EXIT SIMULATION ──
    p(f"\n── 6. EXIT SIMULATION: cts_accel < 0 for N consecutive bars ──")
    for n_thresh in [1, 2, 3, 5]:
        col_bar = f"accel_neg_{n_thresh}_bar"
        col_pnl = f"accel_neg_{n_thresh}_pnl"
        p(f"\n  --- accel < 0 for {n_thresh} bar(s) ---")
        for name, sub in [("Completed", completed), ("Fizzled", fizzled)]:
            if len(sub) == 0:
                continue
            triggered = sub[sub[col_pnl].notna()]
            if len(triggered) > 0:
                p(f"    {name}: triggered={len(triggered)}/{len(sub)}  "
                  f"AvgExitPnL={triggered[col_pnl].mean():+.2f}%  "
                  f"AvgExitBar={triggered[col_bar].mean():.1f}  "
                  f"(actual AvgPnL={triggered['pnl_pct'].mean():+.2f}%)  "
                  f"Saved={triggered['pnl_pct'].mean() - triggered[col_pnl].mean():+.2f}%")

    # ── 7. CTS LEVEL DROP EXIT SIMULATION ──
    p(f"\n── 7. EXIT SIMULATION: CTS drops below level after having been > 0 ──")
    for lvl in [0.0, -0.3, -0.5]:
        key = f"cts_drop_{lvl:.1f}"
        col_bar = f"{key}_bar"
        col_pnl = f"{key}_pnl"
        p(f"\n  --- CTS drops below {lvl:.1f} (after peak > 0) ---")
        for name, sub in [("Completed", completed), ("Fizzled", fizzled)]:
            if len(sub) == 0:
                continue
            triggered = sub[sub[col_pnl].notna()]
            not_triggered = sub[sub[col_pnl].isna()]
            if len(triggered) > 0:
                p(f"    {name}: triggered={len(triggered)}/{len(sub)}  "
                  f"AvgExitPnL={triggered[col_pnl].mean():+.2f}%  "
                  f"AvgExitBar={triggered[col_bar].mean():.1f}  "
                  f"(actual AvgPnL={triggered['pnl_pct'].mean():+.2f}%)")
            if len(not_triggered) > 0:
                p(f"    {name}: NOT triggered={len(not_triggered)}  "
                  f"AvgPnL={not_triggered['pnl_pct'].mean():+.2f}%")

    # ── 8. COMBINED SUMMARY: BEST EXIT TRIGGERS ──
    p(f"\n── 8. NET IMPACT: What if we applied each exit trigger to ALL trades? ──")
    p(f"  (Overrides actual exit for triggered trades, keeps actual for rest)")

    baseline_pnl = df["pnl_pct"].mean()
    baseline_win = (df["pnl_pct"] > 0).mean() * 100
    p(f"\n  Baseline: {total} trades, AvgPnL={baseline_pnl:+.2f}%, Win%={baseline_win:.1f}")

    results = []
    # Slope triggers
    for n in [1, 2, 3, 5]:
        col_pnl = f"slope_neg_{n}_pnl"
        sim_pnl = df.apply(lambda r: r[col_pnl] if not np.isnan(r[col_pnl]) else r["pnl_pct"], axis=1)
        trig_n = df[col_pnl].notna().sum()
        results.append({
            "Trigger": f"slope<0 × {n}bars",
            "Triggered": trig_n,
            "AvgPnL": round(sim_pnl.mean(), 2),
            "Win%": round((sim_pnl > 0).mean() * 100, 1),
            "Delta PnL": round(sim_pnl.mean() - baseline_pnl, 2),
        })

    # Accel triggers
    for n in [1, 2, 3, 5]:
        col_pnl = f"accel_neg_{n}_pnl"
        sim_pnl = df.apply(lambda r: r[col_pnl] if not np.isnan(r[col_pnl]) else r["pnl_pct"], axis=1)
        trig_n = df[col_pnl].notna().sum()
        results.append({
            "Trigger": f"accel<0 × {n}bars",
            "Triggered": trig_n,
            "AvgPnL": round(sim_pnl.mean(), 2),
            "Win%": round((sim_pnl > 0).mean() * 100, 1),
            "Delta PnL": round(sim_pnl.mean() - baseline_pnl, 2),
        })

    # CTS drop triggers
    for lvl in [0.0, -0.3, -0.5]:
        key = f"cts_drop_{lvl:.1f}"
        col_pnl = f"{key}_pnl"
        sim_pnl = df.apply(lambda r: r[col_pnl] if not np.isnan(r[col_pnl]) else r["pnl_pct"], axis=1)
        trig_n = df[col_pnl].notna().sum()
        results.append({
            "Trigger": f"CTS drop<{lvl:.1f}",
            "Triggered": trig_n,
            "AvgPnL": round(sim_pnl.mean(), 2),
            "Win%": round((sim_pnl > 0).mean() * 100, 1),
            "Delta PnL": round(sim_pnl.mean() - baseline_pnl, 2),
        })

    results.sort(key=lambda x: x["AvgPnL"], reverse=True)
    p(tabulate(results, headers="keys", tablefmt="simple", floatfmt=".2f"))

    # Save output
    OUT_PATH.mkdir(exist_ok=True)
    outfile = OUT_PATH / "cts_fizzle_study.txt"
    with open(outfile, "w") as f:
        f.write("\n".join(out_lines))
    print(f"\n  Saved to {outfile}")

    # Also save raw trade data for further analysis
    csv_file = OUT_PATH / "cts_fizzle_trades.csv"
    df.to_csv(csv_file, index=False)
    print(f"  Trade data saved to {csv_file}")


def main():
    parser = argparse.ArgumentParser(description="CTS fizzle study")
    parser.add_argument("--watchlist", default="NIFTY 500")
    parser.add_argument("--start", default=START)
    parser.add_argument("--end", default=END)
    args = parser.parse_args()

    symbols = get_watchlist_symbols(args.watchlist)

    print(f"CTS Fizzle Study")
    print(f"Watchlist: {args.watchlist} ({len(symbols)} symbols)")
    print(f"Period: {args.start} to {args.end}")

    trades = collect_trades(symbols, args.start, args.end)
    analyze(trades, f"{args.watchlist} ({args.start} to {args.end})")


if __name__ == "__main__":
    main()
