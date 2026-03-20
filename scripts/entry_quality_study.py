"""
Entry Quality Study — NIFTY 500

For every NextGen entry, records entry-bar features and forward PnL.
Analyzes which feature combinations discriminate winners from losers.

Goal: find an entry quality gate that achieves 3x payoff ratio (avg_winner / avg_loser).

Sections:
  1. Overall baseline
  2. Feature distributions: winners vs losers
  3. Single-feature bins: pdd_120, cts, velocity_60_norm, regime, cts_accel margin, intensity
  4. Relative feature bins: pdd_rel, cts_margin (threshold-normalised)
  5. Grid: pdd_rel x cts_margin
  6. Quality gate sweep: enumerate all threshold combinations, rank by payoff

Output: output/entry_quality_study.txt

Usage:
    venv/bin/python3 scripts/entry_quality_study.py
    venv/bin/python3 scripts/entry_quality_study.py --watchlist "NIFTY 50"
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.nextgen import _can_enter, NextGenEntryConfig

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"
OUT_DIR = Path(__file__).resolve().parent.parent / "output"
OUT_DIR.mkdir(exist_ok=True)

CFG = NextGenEntryConfig()
FORWARD_BARS = 10      # evaluation horizon
MIN_BARS_NEEDED = 5    # skip trades with fewer forward bars

ENTRY_FEATURES = [
    "cts", "cts_slope", "cts_accel", "cts_accel_threshold",
    "cts_buy_threshold",
    "velocity_60_norm", "vel_dp5",
    "pdd_120", "pdd_120_threshold",
    "price_slope_z", "atr_20",
]


# ── data collection ────────────────────────────────────────────────────────────

def get_symbols(name: str) -> list[str]:
    db = sqlite3.connect(str(DB_PATH))
    row = db.execute("SELECT id FROM watchlists WHERE name = ?", (name,)).fetchone()
    if not row:
        avail = [r[0] for r in db.execute("SELECT name FROM watchlists ORDER BY name").fetchall()]
        db.close()
        print(f"Watchlist '{name}' not found. Available: {avail}")
        sys.exit(1)
    syms = [r[0] for r in db.execute(
        "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY display_order",
        (row[0],),
    ).fetchall()]
    db.close()
    return syms


def scan_symbol(sym: str, start: str, end: str) -> list[dict]:
    """Find all NextGen entries; record entry features + forward returns."""
    result = DivergenceEngine(sym).run()
    df = result.ledger.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.reset_index(drop=True)
    records = df.to_dict("records")
    n = len(records)

    window = df[(df["date"] >= start) & (df["date"] <= end)]
    trades = []
    last_entry_idx = -10

    for i in window.index:
        if i < 1 or i <= last_entry_idx + 2:
            continue

        row = records[i]
        prev = records[i - 1]
        ok, intensity, _ = _can_enter(row, prev, CFG)
        if not ok:
            continue

        entry_price = row.get("close", np.nan)
        if not entry_price or np.isnan(entry_price) or entry_price <= 0:
            continue

        atr = row.get("atr_20", 0) or entry_price * 0.02
        atr_pct = atr / entry_price * 100

        # Forward returns
        pnl_fwd = {}
        for h in [5, 10, 15, 20]:
            idx = i + h
            if idx < n:
                c = records[idx].get("close", np.nan)
                pnl_fwd[h] = (c / entry_price - 1) * 100 if c and not np.isnan(c) else np.nan
            else:
                pnl_fwd[h] = np.nan

        if all(np.isnan(v) for v in pnl_fwd.values()):
            continue

        # Check we have at least MIN_BARS_NEEDED
        available = sum(1 for h in [5, 10, 15, 20] if not np.isnan(pnl_fwd.get(h, np.nan)))
        if available < 1:
            continue

        # Entry features
        ef = {f: row.get(f, np.nan) for f in ENTRY_FEATURES}

        # Relative / derived features
        pdd = ef.get("pdd_120", np.nan)
        pdd_thr = ef.get("pdd_120_threshold", np.nan)
        cts = ef.get("cts", np.nan)
        cts_buy = ef.get("cts_buy_threshold", np.nan)
        cts_accel = ef.get("cts_accel", np.nan)
        cts_accel_thr = ef.get("cts_accel_threshold", np.nan)

        # pdd_rel: (pdd - threshold) / |threshold|  →  0 = just at limit, -1 = one unit below
        pdd_rel = (pdd - pdd_thr) / abs(pdd_thr) if (
            not np.isnan(pdd) and not np.isnan(pdd_thr) and pdd_thr != 0
        ) else np.nan

        # cts_margin: how much cts exceeds cts_buy_threshold (normalised by a fixed scale)
        cts_margin = cts - cts_buy if (
            not np.isnan(cts) and not np.isnan(cts_buy)
        ) else np.nan

        # accel_margin: cts_accel above its threshold (normalised)
        accel_margin = cts_accel - cts_accel_thr if (
            not np.isnan(cts_accel) and not np.isnan(cts_accel_thr)
        ) else np.nan

        last_entry_idx = i
        trades.append({
            "sym": sym,
            "date": row["date"].strftime("%Y-%m-%d"),
            "regime": row.get("regime", ""),
            "intensity": intensity,
            "atr_pct": round(atr_pct, 3),
            **{f"pnl_{h}": round(pnl_fwd[h], 3) if not np.isnan(pnl_fwd[h]) else np.nan
               for h in [5, 10, 15, 20]},
            **{k: v for k, v in ef.items()},
            "pdd_rel": round(pdd_rel, 3) if not np.isnan(pdd_rel) else np.nan,
            "cts_margin": round(cts_margin, 4) if not np.isnan(cts_margin) else np.nan,
            "accel_margin": round(accel_margin, 5) if not np.isnan(accel_margin) else np.nan,
        })

    return trades


# ── analysis helpers ────────────────────────────────────────────────────────────

def payoff_stats(df: pd.DataFrame, pnl_col: str = "pnl_10") -> dict:
    pnl = df[pnl_col].dropna()
    if len(pnl) == 0:
        return {"n": 0, "win_pct": np.nan, "avg_pnl": np.nan,
                "avg_win": np.nan, "avg_loss": np.nan, "payoff": np.nan}
    winners = pnl[pnl > 0]
    losers = pnl[pnl <= 0]
    avg_win = winners.mean() if len(winners) > 0 else np.nan
    avg_loss = abs(losers.mean()) if len(losers) > 0 else np.nan
    payoff = avg_win / avg_loss if (not np.isnan(avg_win) and not np.isnan(avg_loss) and avg_loss > 0) else np.nan
    return {
        "n": len(pnl),
        "win_pct": len(winners) / len(pnl) * 100,
        "avg_pnl": pnl.mean(),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff": payoff,
    }


def fmt_row(label: str, s: dict) -> str:
    n = s["n"]
    if n == 0:
        return f"  {label:<35}  N=0"
    return (
        f"  {label:<35}  N={n:>5}  Win={s['win_pct']:>5.1f}%"
        f"  AvgPnL={s['avg_pnl']:>+6.2f}%"
        f"  AvgWin={s['avg_win']:>+5.2f}%  AvgLoss={s['avg_loss']:>5.2f}%"
        f"  Payoff={s['payoff']:>5.2f}x"
    )


def bin_feature(df: pd.DataFrame, col: str, bins: list, labels: list,
                out: StringIO, pnl_col: str = "pnl_10") -> None:
    df2 = df[df[col].notna()].copy()
    df2["_bin"] = pd.cut(df2[col], bins=bins, labels=labels, right=True)
    out.write(f"\n  {col} bins:\n")
    out.write(f"  {'Label':<35}  {'N':>6}  {'Win%':>6}  {'AvgPnL':>8}  {'AvgWin':>7}  {'AvgLoss':>8}  {'Payoff':>7}\n")
    out.write("  " + "-" * 90 + "\n")
    for lab in labels:
        sub = df2[df2["_bin"] == lab]
        s = payoff_stats(sub, pnl_col)
        out.write(fmt_row(str(lab), s) + "\n")


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", default="NIFTY 500")
    parser.add_argument("--start", default="2025-06-01")
    parser.add_argument("--end", default="2026-03-15")
    args = parser.parse_args()

    out_path = OUT_DIR / "entry_quality_study.txt"
    symbols = get_symbols(args.watchlist)

    print(f"Scanning {len(symbols)} symbols from '{args.watchlist}' ({args.start} -> {args.end})")
    print(f"Output -> {out_path}\n")

    all_trades: list[dict] = []
    skipped: list[str] = []

    for idx, sym in enumerate(symbols, 1):
        print(f"  [{idx:>3}/{len(symbols)}] {sym:<20}", end=" ", flush=True)
        try:
            trades = scan_symbol(sym, args.start, args.end)
            all_trades.extend(trades)
            print(f"{len(trades)} trades")
        except Exception as e:
            skipped.append(sym)
            print(f"ERROR: {e}")

    df = pd.DataFrame(all_trades)
    print(f"\nTotal trades: {len(df)}  Skipped: {len(skipped)}")
    if len(df) == 0:
        print("No trades found.")
        return

    out = StringIO()

    header = "=" * 120
    out.write(header + "\n")
    out.write(f"  ENTRY QUALITY STUDY — {args.watchlist}\n")
    out.write(f"  Period: {args.start} -> {args.end}   Forward evaluation: 10 bars\n")
    out.write(f"  Symbols: {len(symbols)}   Skipped: {len(skipped)}   Total trades: {len(df)}\n")
    out.write(header + "\n\n")

    # ── 1. Overall baseline ────────────────────────────────────────────────────
    out.write("── 1. OVERALL BASELINE ──────────────────────────────────────────────────────────\n\n")
    for h in [5, 10, 15, 20]:
        col = f"pnl_{h}"
        if col in df:
            s = payoff_stats(df, col)
            out.write(fmt_row(f"All trades (PnL{h:>2}d)", s) + "\n")
    out.write("\n")

    # ── 2. Feature distribution: winners vs losers ────────────────────────────
    out.write("── 2. FEATURE DISTRIBUTION AT ENTRY: WINNERS vs LOSERS (pnl_10 horizon) ────────\n\n")
    pnl10 = df["pnl_10"].dropna()
    w = df[df["pnl_10"] > 0]
    lo = df[df["pnl_10"] <= 0]
    out.write(f"  Winners: {len(w)}  ({len(w)/len(pnl10)*100:.1f}%)   Losers: {len(lo)}  ({len(lo)/len(pnl10)*100:.1f}%)\n\n")

    feats_to_compare = [
        "pdd_120", "pdd_rel",
        "cts", "cts_margin",
        "cts_accel", "accel_margin",
        "velocity_60_norm", "vel_dp5",
        "intensity",
    ]
    out.write(f"  {'Feature':<22}  {'Winners Mean':>13}  {'Losers Mean':>12}  {'Winners Med':>12}  {'Losers Med':>11}\n")
    out.write("  " + "-" * 78 + "\n")
    for feat in feats_to_compare:
        if feat not in df.columns:
            continue
        wm = w[feat].dropna().mean()
        lm = lo[feat].dropna().mean()
        wmed = w[feat].dropna().median()
        lmed = lo[feat].dropna().median()
        out.write(f"  {feat:<22}  {wm:>+13.4f}  {lm:>+12.4f}  {wmed:>+12.4f}  {lmed:>+11.4f}\n")
    out.write("\n")

    # ── 3. Single-feature bins ─────────────────────────────────────────────────
    out.write("── 3. SINGLE-FEATURE BINS ───────────────────────────────────────────────────────\n")

    # pdd_120 (raw)
    bin_feature(df, "pdd_120",
                bins=[-999, -3.0, -2.5, -2.0, -1.5, -1.0, -0.5, 999],
                labels=["<-3.0", "-3.0 to -2.5", "-2.5 to -2.0", "-2.0 to -1.5",
                        "-1.5 to -1.0", "-1.0 to -0.5", ">-0.5"],
                out=out)

    # pdd_rel (threshold-relative)
    bin_feature(df, "pdd_rel",
                bins=[-999, -1.5, -1.0, -0.7, -0.4, -0.2, 0.0, 999],
                labels=["<-1.5", "-1.5 to -1.0", "-1.0 to -0.7", "-0.7 to -0.4",
                        "-0.4 to -0.2", "-0.2 to 0.0", ">0.0"],
                out=out)

    # cts (raw)
    bin_feature(df, "cts",
                bins=[-999, -0.15, -0.12, -0.09, -0.06, -0.03, 0.0, 999],
                labels=["<-0.15", "-0.15 to -0.12", "-0.12 to -0.09", "-0.09 to -0.06",
                        "-0.06 to -0.03", "-0.03 to 0.0", ">0.0"],
                out=out)

    # cts_margin (threshold-relative)
    bin_feature(df, "cts_margin",
                bins=[-999, -0.10, -0.05, 0.0, 0.05, 0.10, 999],
                labels=["<-0.10", "-0.10 to -0.05", "-0.05 to 0.0",
                        "0.0 to 0.05", "0.05 to 0.10", ">0.10"],
                out=out)

    # velocity_60_norm
    bin_feature(df, "velocity_60_norm",
                bins=[-999, 0.0, 0.20, 0.35, 0.50, 0.65, 999],
                labels=["<0.0", "0.0 to 0.20", "0.20 to 0.35", "0.35 to 0.50",
                        "0.50 to 0.65", ">0.65"],
                out=out)

    # vel_dp5
    bin_feature(df, "vel_dp5",
                bins=[3.5, 4.5, 5.5],
                labels=["dp5==4", "dp5==5"],
                out=out)

    # accel_margin
    bin_feature(df, "accel_margin",
                bins=[-999, -0.005, 0.0, 0.005, 0.01, 0.02, 999],
                labels=["<-0.005", "-0.005 to 0.0", "0.0 to 0.005",
                        "0.005 to 0.01", "0.01 to 0.02", ">0.02"],
                out=out)

    # intensity
    bin_feature(df, "intensity",
                bins=[-1, 10, 20, 30, 50, 999],
                labels=["0-10", "10-20", "20-30", "30-50", ">50"],
                out=out)

    # regime
    out.write("\n  regime:\n")
    out.write(f"  {'Label':<35}  {'N':>6}  {'Win%':>6}  {'AvgPnL':>8}  {'AvgWin':>7}  {'AvgLoss':>8}  {'Payoff':>7}\n")
    out.write("  " + "-" * 90 + "\n")
    for regime in ["uptrend", "downtrend", "notrend", "transition"]:
        sub = df[df["regime"] == regime]
        s = payoff_stats(sub, "pnl_10")
        out.write(fmt_row(regime, s) + "\n")

    # ── 4. pdd_rel × cts_margin grid ──────────────────────────────────────────
    out.write("\n── 4. GRID: pdd_rel × cts_margin ────────────────────────────────────────────────\n")
    out.write("  (rows = pdd_rel bins, cols = cts_margin bins)  Payoff / N shown\n\n")

    pdd_cuts = [(-999, -1.0), (-1.0, -0.5), (-0.5, -0.2), (-0.2, 999)]
    pdd_lbls = ["pdd_rel<-1.0", "-1.0 to -0.5", "-0.5 to -0.2", "pdd_rel>-0.2"]
    cts_cuts = [(-999, -0.05), (-0.05, 0.0), (0.0, 0.05), (0.05, 999)]
    cts_lbls = ["cts_m<-0.05", "-0.05 to 0", "0 to 0.05", "cts_m>0.05"]

    col_w = 20
    out.write("  " + " " * 16)
    for cl in cts_lbls:
        out.write(f"  {cl:^{col_w}}")
    out.write("\n  " + "-" * (16 + (col_w + 2) * len(cts_lbls)) + "\n")

    for (pl, ph), plbl in zip(pdd_cuts, pdd_lbls):
        out.write(f"  {plbl:<16}")
        for (cl, ch), clbl in zip(cts_cuts, cts_lbls):
            sub = df[
                (df["pdd_rel"] > pl) & (df["pdd_rel"] <= ph) &
                (df["cts_margin"] > cl) & (df["cts_margin"] <= ch)
            ]
            s = payoff_stats(sub, "pnl_10")
            if s["n"] == 0:
                cell = "N=0"
            elif np.isnan(s["payoff"]):
                cell = f"N={s['n']} P=?"
            else:
                cell = f"N={s['n']} P={s['payoff']:.2f}x"
            out.write(f"  {cell:^{col_w}}")
        out.write("\n")
    out.write("\n")

    # ── 5. Quality gate sweep ──────────────────────────────────────────────────
    out.write("── 5. QUALITY GATE SWEEP (pdd_rel + cts_margin thresholds) ─────────────────────\n")
    out.write("  Enumerate combinations. Show those with Payoff >= 2.0x and N >= 100.\n\n")
    out.write(f"  {'Gate':<45}  {'N':>5}  {'Win%':>5}  {'AvgPnL':>7}  {'AvgWin':>6}  {'AvgLoss':>7}  {'Payoff':>7}  {'Kept%':>6}\n")
    out.write("  " + "-" * 110 + "\n")

    total_n = len(df.dropna(subset=["pnl_10"]))
    pdd_thresholds = [-1.5, -1.2, -1.0, -0.8, -0.6, -0.4]
    cts_thresholds = [-0.10, -0.08, -0.06, -0.04, -0.02, 0.0]
    vel_thresholds = [None, 0.5, 0.6]  # None = no vel filter

    results_sweep = []
    for pdd_t in pdd_thresholds:
        for cts_t in cts_thresholds:
            for vel_t in vel_thresholds:
                mask = (df["pdd_rel"] >= pdd_t) & (df["cts_margin"] >= cts_t)
                if vel_t is not None:
                    mask = mask & (df["velocity_60_norm"] <= vel_t)
                sub = df[mask].dropna(subset=["pnl_10"])
                s = payoff_stats(sub, "pnl_10")
                if s["n"] >= 100 and not np.isnan(s["payoff"]):
                    results_sweep.append((s["payoff"], s, pdd_t, cts_t, vel_t))

    results_sweep.sort(reverse=True)
    shown = 0
    for payoff_val, s, pdd_t, cts_t, vel_t in results_sweep:
        if shown >= 30:
            break
        vel_str = f" & vel<={vel_t}" if vel_t is not None else ""
        gate_str = f"pdd_rel>={pdd_t}  cts_m>={cts_t:.2f}{vel_str}"
        kept_pct = s["n"] / total_n * 100
        out.write(
            f"  {gate_str:<45}  {s['n']:>5}  {s['win_pct']:>5.1f}%"
            f"  {s['avg_pnl']:>+7.2f}%  {s['avg_win']:>+6.2f}%  {s['avg_loss']:>7.2f}%"
            f"  {payoff_val:>7.2f}x  {kept_pct:>5.1f}%\n"
        )
        shown += 1

    if not results_sweep:
        out.write("  No gate reached payoff >= 2.0x with N >= 100.\n")
    out.write("\n")

    # ── 6. Best gate summary ───────────────────────────────────────────────────
    out.write("── 6. TOP GATES BY PAYOFF (also showing regime breakdown) ──────────────────────\n\n")
    top = [r for r in results_sweep if r[0] >= 2.5][:5]
    if not top:
        top = results_sweep[:3]

    for rank, (payoff_val, s_all, pdd_t, cts_t, vel_t) in enumerate(top, 1):
        vel_str = f" & vel<={vel_t}" if vel_t is not None else ""
        gate_str = f"pdd_rel>={pdd_t}  cts_m>={cts_t:.2f}{vel_str}"
        out.write(f"  Gate #{rank}: {gate_str}\n")
        out.write(fmt_row("All regimes", s_all) + "\n")

        mask = (df["pdd_rel"] >= pdd_t) & (df["cts_margin"] >= cts_t)
        if vel_t is not None:
            mask = mask & (df["velocity_60_norm"] <= vel_t)
        sub = df[mask].dropna(subset=["pnl_10"])
        for regime in ["uptrend", "downtrend", "notrend", "transition"]:
            s_r = payoff_stats(sub[sub["regime"] == regime], "pnl_10")
            if s_r["n"] >= 10:
                out.write(fmt_row(f"  {regime}", s_r) + "\n")
        out.write("\n")

    # ── 7. Recommended gate ────────────────────────────────────────────────────
    out.write("── 7. RECOMMENDED GATE ──────────────────────────────────────────────────────────\n\n")
    target = [r for r in results_sweep if r[0] >= 3.0]
    if target:
        payoff_val, s, pdd_t, cts_t, vel_t = target[0]
        vel_str = f" & velocity_60_norm <= {vel_t}" if vel_t is not None else ""
        out.write(f"  First gate reaching 3x payoff:\n")
        out.write(f"    pdd_rel >= {pdd_t}  AND  cts_margin >= {cts_t:.2f}{vel_str}\n")
        out.write(f"    (pdd_rel = (pdd_120 - pdd_120_threshold) / abs(pdd_120_threshold))\n")
        out.write(f"    (cts_margin = cts - cts_buy_threshold)\n\n")
        out.write(fmt_row("Result", s) + "\n")
        kept = s["n"] / total_n * 100
        out.write(f"\n  Signals kept: {s['n']}/{total_n} ({kept:.1f}%)\n")
        out.write(f"  Signals filtered: {total_n - s['n']} ({100-kept:.1f}%)\n")
    else:
        best = results_sweep[0] if results_sweep else None
        if best:
            payoff_val, s, pdd_t, cts_t, vel_t = best
            out.write(f"  3x payoff not reached. Best achievable: {payoff_val:.2f}x\n")
            vel_str = f" & velocity_60_norm <= {vel_t}" if vel_t is not None else ""
            out.write(f"    pdd_rel >= {pdd_t}  AND  cts_margin >= {cts_t:.2f}{vel_str}\n")
            out.write(fmt_row("Result", s) + "\n")
        else:
            out.write("  No qualifying gate found.\n")
    out.write("\n")

    # ── write output ───────────────────────────────────────────────────────────
    text = out.getvalue()
    print(text)
    with open(out_path, "w") as f:
        f.write(text)
    print(f"\nWritten -> {out_path}")


if __name__ == "__main__":
    main()
