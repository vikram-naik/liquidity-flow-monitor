"""
Hard-Stop Anatomy Study — NIFTY 500

For every NextGen entry, simulates a bare hold (no exit logic, max 30 bars) and
classifies the trade outcome:

  hard_stop_territory  — MAE exceeds 2.0 × ATR (would have hit current hard stop)
  deep_loss            — MAE > 1.5 × ATR but < 2.0 × ATR
  recoverable          — dipped > 1.0 × ATR but recovered to positive 10d PnL
  clean_win            — pnl_10 > 0, MAE < 1.0 × ATR

Analyzes entry-bar features of hard-stop trades vs all others to identify
a discriminating gate.

Sections:
  1. Outcome distribution
  2. Feature distribution: hard_stop_territory vs rest
  3. Single-feature bins vs hard-stop rate
  4. Gate sweep: which entry filter reduces hard-stop rate most efficiently
  5. Early-exit simulation: if we exit within bar N when pnl < -K×ATR, what is
     the PnL vs the full hard stop loss?

Output: output/hard_stop_study.txt

Usage:
    venv/bin/python3 scripts/hard_stop_study.py
    venv/bin/python3 scripts/hard_stop_study.py --watchlist "NIFTY 50"
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
MAX_HOLD = 30
HS_ATR_MULT = 2.0    # hard stop multiple (matches NextGenExitConfig.stop_atr_multiple)
EARLY_ATR   = 1.0    # "early exit" trigger multiple


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


ENTRY_FEATS = [
    "cts", "cts_slope", "cts_accel", "cts_accel_threshold", "cts_buy_threshold",
    "velocity_60_norm", "vel_dp5", "pdd_120", "pdd_120_threshold",
    "price_slope_z", "atr_20",
]


def scan_symbol(sym: str, start: str, end: str) -> list[dict]:
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
        atr_pct = atr / entry_price          # fraction, not percent
        hard_stop_pct = -(HS_ATR_MULT * atr_pct * 100)   # e.g. -8%
        early_exit_pct = -(EARLY_ATR  * atr_pct * 100)   # e.g. -4%

        # Forward bars
        peak_pnl = 0.0
        trough_pnl = 0.0
        early_exit_bar = None
        early_exit_pnl = None
        pnl_bars = {}

        for j in range(1, MAX_HOLD + 1):
            idx = i + j
            if idx >= n:
                break
            c = records[idx].get("close", np.nan)
            if np.isnan(c):
                continue
            pnl = (c / entry_price - 1) * 100
            pnl_bars[j] = pnl
            if pnl > peak_pnl:
                peak_pnl = pnl
            if pnl < trough_pnl:
                trough_pnl = pnl
            # First bar where early-exit threshold is breached
            if early_exit_bar is None and pnl <= early_exit_pct:
                early_exit_bar = j
                early_exit_pnl = pnl

        if not pnl_bars:
            continue

        mae_pct = abs(trough_pnl)
        mae_atr = mae_pct / (atr_pct * 100) if atr_pct > 0 else np.nan
        pnl_10 = pnl_bars.get(10, pnl_bars.get(max(pnl_bars), np.nan))

        # Classify outcome
        hit_hs = mae_atr >= HS_ATR_MULT
        if hit_hs:
            outcome = "hard_stop_territory"
        elif mae_atr >= 1.5:
            outcome = "deep_loss"
        elif pnl_10 > 0:
            outcome = "win"
        else:
            outcome = "small_loss"

        # Entry relative features
        pdd = row.get("pdd_120", np.nan)
        pdd_thr = row.get("pdd_120_threshold", np.nan)
        cts = row.get("cts", np.nan)
        cts_buy = row.get("cts_buy_threshold", np.nan)
        pdd_rel = (pdd - pdd_thr) / abs(pdd_thr) if (
            not np.isnan(pdd) and not np.isnan(pdd_thr) and pdd_thr != 0) else np.nan
        cts_margin = cts - cts_buy if (not np.isnan(cts) and not np.isnan(cts_buy)) else np.nan
        vel = row.get("velocity_60_norm", np.nan)

        last_entry_idx = i
        trades.append({
            "sym": sym,
            "date": row["date"].strftime("%Y-%m-%d"),
            "regime": row.get("regime", ""),
            "intensity": intensity,
            "atr_pct": round(atr_pct * 100, 3),
            "hard_stop_pct": round(hard_stop_pct, 2),
            "mae_pct": round(mae_pct, 3),
            "mae_atr": round(mae_atr, 2) if not np.isnan(mae_atr) else np.nan,
            "mfe_pct": round(peak_pnl, 3),
            "pnl_10": round(pnl_10, 3) if not np.isnan(pnl_10) else np.nan,
            "outcome": outcome,
            "hit_hard_stop": hit_hs,
            "early_exit_bar": early_exit_bar,
            "early_exit_pnl": round(early_exit_pnl, 3) if early_exit_pnl is not None else np.nan,
            **{f: row.get(f, np.nan) for f in ENTRY_FEATS},
            "pdd_rel": round(pdd_rel, 3) if not np.isnan(pdd_rel) else np.nan,
            "cts_margin": round(cts_margin, 4) if not np.isnan(cts_margin) else np.nan,
        })

    return trades


# ── helpers ────────────────────────────────────────────────────────────────────

def hs_stats(df: pd.DataFrame, label: str, out: StringIO) -> None:
    n = len(df)
    if n == 0:
        return
    hs = df["hit_hard_stop"].sum()
    hs_pct = hs / n * 100
    avg_pnl = df["pnl_10"].dropna().mean()
    avg_hs_loss = df.loc[df["hit_hard_stop"], "mae_pct"].mean() if hs > 0 else np.nan
    out.write(
        f"  {label:<40}  N={n:>5}  HS%={hs_pct:>5.1f}%  HS_count={int(hs):>4}"
        f"  AvgPnL10={avg_pnl:>+6.2f}%"
        + (f"  AvgHS_MAE=-{avg_hs_loss:.2f}%" if not np.isnan(avg_hs_loss) else "")
        + "\n"
    )


def bin_hs(df: pd.DataFrame, col: str, bins: list, labels: list, out: StringIO) -> None:
    df2 = df[df[col].notna()].copy()
    df2["_bin"] = pd.cut(df2[col], bins=bins, labels=labels, right=True)
    out.write(f"\n  {col} bins:\n")
    out.write(f"  {'Label':<30}  {'N':>5}  {'HS%':>6}  {'HS_n':>5}  {'AvgPnL':>8}  {'AvgWin':>7}  {'AvgLoss':>8}\n")
    out.write("  " + "-" * 80 + "\n")
    for lab in labels:
        sub = df2[df2["_bin"] == lab]
        n = len(sub)
        if n == 0:
            continue
        hs_n = sub["hit_hard_stop"].sum()
        hs_p = hs_n / n * 100
        pnl = sub["pnl_10"].dropna()
        avg_pnl = pnl.mean()
        avg_win = pnl[pnl > 0].mean() if (pnl > 0).any() else np.nan
        avg_loss = pnl[pnl <= 0].mean() if (pnl <= 0).any() else np.nan
        win_str = f"{avg_win:>+7.2f}%" if not np.isnan(avg_win) else "       "
        loss_str = f"{avg_loss:>+8.2f}%" if not np.isnan(avg_loss) else "        "
        out.write(
            f"  {str(lab):<30}  {n:>5}  {hs_p:>5.1f}%  {int(hs_n):>5}  {avg_pnl:>+8.2f}%  {win_str}  {loss_str}\n"
        )


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", default="NIFTY 500")
    parser.add_argument("--start", default="2025-06-01")
    parser.add_argument("--end", default="2026-03-15")
    args = parser.parse_args()

    out_path = OUT_DIR / "hard_stop_study.txt"
    symbols = get_symbols(args.watchlist)

    print(f"Scanning {len(symbols)} symbols from '{args.watchlist}' ({args.start} -> {args.end})")
    print(f"Hard-stop threshold: {HS_ATR_MULT}× ATR\nOutput -> {out_path}\n")

    all_trades: list[dict] = []
    skipped = []

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
        return

    out = StringIO()
    sep = "=" * 120
    out.write(sep + "\n")
    out.write(f"  HARD-STOP ANATOMY STUDY — {args.watchlist}\n")
    out.write(f"  Period: {args.start} -> {args.end}   Hard-stop: >{HS_ATR_MULT}× ATR adverse\n")
    out.write(f"  Symbols: {len(symbols)}   Total trades: {len(df)}\n")
    out.write(sep + "\n\n")

    # ── 1. Outcome distribution ────────────────────────────────────────────────
    out.write("── 1. OUTCOME DISTRIBUTION ──────────────────────────────────────────────────────\n\n")
    for oc in ["hard_stop_territory", "deep_loss", "small_loss", "win"]:
        sub = df[df["outcome"] == oc]
        hs_stats(df[df["outcome"] == oc], oc, out)
    out.write("\n")

    # Regime breakdown of hard stops
    out.write("  Hard-stop rate by regime:\n")
    out.write(f"  {'Regime':<20}  {'N':>5}  {'HS%':>6}  {'HS_n':>5}  {'AvgPnL':>8}\n")
    out.write("  " + "-" * 50 + "\n")
    for regime in ["uptrend", "downtrend", "notrend", "transition"]:
        sub = df[df["regime"] == regime]
        if len(sub) == 0:
            continue
        hs_n = sub["hit_hard_stop"].sum()
        out.write(f"  {regime:<20}  {len(sub):>5}  {hs_n/len(sub)*100:>5.1f}%  {int(hs_n):>5}  {sub['pnl_10'].dropna().mean():>+8.2f}%\n")
    out.write("\n")

    # ── 2. Feature distribution: hard-stop vs rest ─────────────────────────────
    out.write("── 2. FEATURE DISTRIBUTION AT ENTRY: HARD-STOP vs REST ─────────────────────────\n\n")
    hs_df = df[df["hit_hard_stop"]]
    ok_df = df[~df["hit_hard_stop"]]
    out.write(f"  Hard-stop territory: {len(hs_df)}  ({len(hs_df)/len(df)*100:.1f}%)\n")
    out.write(f"  Rest (no hard stop): {len(ok_df)}  ({len(ok_df)/len(df)*100:.1f}%)\n\n")

    feats = ["pdd_120", "pdd_rel", "cts", "cts_margin",
             "velocity_60_norm", "vel_dp5", "cts_accel", "atr_pct", "intensity"]
    out.write(f"  {'Feature':<22}  {'HS Mean':>10}  {'Rest Mean':>10}  {'HS Med':>10}  {'Rest Med':>10}  {'Diff':>8}\n")
    out.write("  " + "-" * 78 + "\n")
    for f in feats:
        if f not in df.columns:
            continue
        hm = hs_df[f].dropna().mean()
        om = ok_df[f].dropna().mean()
        hmed = hs_df[f].dropna().median()
        omed = ok_df[f].dropna().median()
        diff = hm - om
        out.write(f"  {f:<22}  {hm:>+10.4f}  {om:>+10.4f}  {hmed:>+10.4f}  {omed:>+10.4f}  {diff:>+8.4f}\n")
    out.write("\n")

    # ── 3. Single-feature bins vs hard-stop rate ───────────────────────────────
    out.write("── 3. SINGLE-FEATURE BINS vs HARD-STOP RATE ────────────────────────────────────\n")

    bin_hs(df, "pdd_rel",
           bins=[-999, -1.5, -1.0, -0.7, -0.4, -0.2, 0.0, 999],
           labels=["<-1.5", "-1.5 to -1.0", "-1.0 to -0.7", "-0.7 to -0.4",
                   "-0.4 to -0.2", "-0.2 to 0.0", ">0.0"],
           out=out)

    bin_hs(df, "cts",
           bins=[-999, -0.15, -0.12, -0.09, -0.06, -0.03, 0.0, 999],
           labels=["<-0.15", "-0.15 to -0.12", "-0.12 to -0.09", "-0.09 to -0.06",
                   "-0.06 to -0.03", "-0.03 to 0.0", ">0.0"],
           out=out)

    bin_hs(df, "velocity_60_norm",
           bins=[-999, 0.0, 0.20, 0.35, 0.50, 0.65, 999],
           labels=["<0.0", "0.0-0.20", "0.20-0.35", "0.35-0.50", "0.50-0.65", ">0.65"],
           out=out)

    bin_hs(df, "atr_pct",
           bins=[0, 2.0, 2.5, 3.0, 3.5, 4.5, 999],
           labels=["<2.0%", "2.0-2.5%", "2.5-3.0%", "3.0-3.5%", "3.5-4.5%", ">4.5%"],
           out=out)

    bin_hs(df, "cts_margin",
           bins=[-999, 0.0, 0.05, 0.10, 0.20, 999],
           labels=["<0.0", "0.0-0.05", "0.05-0.10", "0.10-0.20", ">0.20"],
           out=out)

    out.write("\n")

    # ── 4. Gate sweep: which filter reduces HS rate most ──────────────────────
    out.write("── 4. GATE SWEEP: WHICH ENTRY FILTER REDUCES HARD-STOP RATE MOST ───────────────\n")
    out.write("  Sorted by hard-stop reduction efficiency (HS removed / signals lost).\n\n")
    out.write(f"  {'Gate':<50}  {'N':>5}  {'HS%':>6}  {'HS_n':>5}  {'HS_rem':>7}  {'Sig_rem':>8}  {'Eff':>6}  {'AvgPnL':>8}\n")
    out.write("  " + "-" * 110 + "\n")

    total_n = len(df)
    total_hs = df["hit_hard_stop"].sum()

    candidates = []
    pdd_thresholds = [-1.5, -1.2, -1.0, -0.8, -0.6, -0.4, -0.2]
    cts_thresholds = [None, -0.12, -0.10, -0.08, -0.05, 0.0]
    vel_thresholds = [None, 0.5, 0.6]
    atr_thresholds = [None, 3.0, 3.5, 4.0]

    for pdd_t in pdd_thresholds:
        for cts_t in cts_thresholds:
            for vel_t in vel_thresholds:
                for atr_t in atr_thresholds:
                    mask = df["pdd_rel"] >= pdd_t
                    if cts_t is not None:
                        mask = mask & (df["cts"] >= cts_t)
                    if vel_t is not None:
                        mask = mask & (df["velocity_60_norm"] <= vel_t)
                    if atr_t is not None:
                        mask = mask & (df["atr_pct"] <= atr_t)
                    sub = df[mask]
                    n = len(sub)
                    if n < 50:
                        continue
                    hs_n = sub["hit_hard_stop"].sum()
                    hs_removed = total_hs - hs_n
                    sigs_removed = total_n - n
                    hs_rate = hs_n / n * 100 if n > 0 else 0
                    # Efficiency: HS removed per signal lost
                    eff = hs_removed / sigs_removed if sigs_removed > 0 else np.nan
                    avg_pnl = sub["pnl_10"].dropna().mean()
                    candidates.append((eff, hs_rate, hs_removed, sigs_removed, n, hs_n,
                                       avg_pnl, pdd_t, cts_t, vel_t, atr_t))

    candidates = [c for c in candidates if not np.isnan(c[0])]
    candidates.sort(key=lambda x: x[0], reverse=True)
    shown = 0
    for eff, hs_rate, hs_rem, sig_rem, n, hs_n, avg_pnl, pdd_t, cts_t, vel_t, atr_t in candidates:
        if shown >= 25:
            break
        parts = [f"pdd_rel>={pdd_t}"]
        if cts_t is not None:
            parts.append(f"cts>={cts_t}")
        if vel_t is not None:
            parts.append(f"vel<={vel_t}")
        if atr_t is not None:
            parts.append(f"atr<={atr_t}%")
        gate_str = "  ".join(parts)
        out.write(
            f"  {gate_str:<50}  {n:>5}  {hs_rate:>5.1f}%  {int(hs_n):>5}  "
            f"{int(hs_rem):>7}  {int(sig_rem):>8}  {eff:>6.3f}  {avg_pnl:>+8.2f}%\n"
        )
        shown += 1

    out.write("\n")

    # ── 5. Early-exit simulation ───────────────────────────────────────────────
    out.write("── 5. EARLY EXIT SIMULATION (exit when pnl < -K×ATR within first N bars) ────────\n")
    out.write("  Compares: hard stop loss vs exiting early at each bar+threshold combo.\n\n")
    out.write(f"  {'Bar limit':<10}  {'ATR mult':<10}  {'Triggered':>10}  {'AvgPnL_early':>14}  {'AvgPnL_hold':>13}  {'Saved/trade':>12}\n")
    out.write("  " + "-" * 75 + "\n")

    hs_trades = df[df["hit_hard_stop"]].copy()
    if len(hs_trades) > 0:
        avg_hs_loss = -hs_trades["mae_pct"].mean()  # negative (loss)

        for bar_lim in [2, 3, 5, 7, 10]:
            for k_mult in [0.75, 1.0, 1.25, 1.5]:
                # Simulate: exit hs_trade when pnl < -k_mult × ATR within bar_lim bars
                # Use early_exit_bar and early_exit_pnl (which was computed at 1.0×ATR)
                # Re-approximate from atr_pct and trough info
                triggered = 0
                early_pnls = []
                # For this approximation, use early_exit_pnl (recorded at 1.0×ATR breach)
                # and scale
                for _, tr in hs_trades.iterrows():
                    threshold_pct = -(k_mult * tr["atr_pct"])
                    eb = tr["early_exit_bar"]
                    ep = tr["early_exit_pnl"]
                    if pd.notna(eb) and pd.notna(ep) and eb <= bar_lim and ep <= threshold_pct:
                        triggered += 1
                        early_pnls.append(ep)
                    # else they still hit full hard stop

                if triggered == 0:
                    continue
                avg_early = np.mean(early_pnls)
                # Non-triggered ones: assume full hard stop loss
                non_triggered = len(hs_trades) - triggered
                blended = (triggered * avg_early + non_triggered * avg_hs_loss) / len(hs_trades)
                saved = blended - avg_hs_loss
                trig_pct = triggered / len(hs_trades) * 100

                out.write(
                    f"  bar<={bar_lim:<6}   {k_mult:.2f}×ATR    {trig_pct:>8.1f}%  "
                    f"{avg_early:>+13.2f}%  {avg_hs_loss:>+12.2f}%  {saved:>+11.2f}%\n"
                )
        out.write("\n")

    # ── write ──────────────────────────────────────────────────────────────────
    text = out.getvalue()
    print(text)
    with open(out_path, "w") as f:
        f.write(text)
    print(f"\nWritten -> {out_path}")


if __name__ == "__main__":
    main()
