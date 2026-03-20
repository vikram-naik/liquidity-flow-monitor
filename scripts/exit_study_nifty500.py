"""
Exit Strategy Empirical Study — Trade Anatomy Analysis.

For every NextGen entry across NIFTY 500, holds for MAX_HOLD bars with NO exit
logic, recording per-bar features. Analyzes:

1. MAE distribution — at what drawdown are trades unrecoverable? (stop-loss calibration)
2. MFE distribution & timing — how much upside, when does it peak?
3. Drawdown-from-peak — what pullback from MFE separates healthy vs dead? (trailing stop)
4. Feature deltas — which features change direction before the price peaks? (exit gates)
5. Feature correlation with trade outcome

Output: output/exit_study_nifty500.txt

Usage:
    venv/bin/python3 scripts/exit_study_nifty500.py
    venv/bin/python3 scripts/exit_study_nifty500.py --watchlist "NIFTY 50" --max-hold 20
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from io import StringIO

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.nextgen import _can_enter, NextGenEntryConfig

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"
OUT_DIR = Path(__file__).resolve().parent.parent / "output"
OUT_DIR.mkdir(exist_ok=True)

CFG = NextGenEntryConfig()
MAX_HOLD = 30  # bars to track after entry

# Features to track per bar during the trade
TRACK_FEATURES = [
    "cts", "cts_slope", "cts_accel", "velocity_60_norm", "vel_dp5",
    "cdvl", "pdd_120", "pdd_120_threshold", "cts_accel_threshold",
    "price_slope_z", "mcs_composite", "cwvap_dist",
]


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


def scan_symbol(sym: str, start: str, end: str, max_hold: int) -> list[dict]:
    """Find all NextGen entries and track forward for max_hold bars."""
    result = DivergenceEngine(sym).run()
    df = result.ledger.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.reset_index(drop=True)
    records = df.to_dict("records")
    n = len(records)

    window = df[(df["date"] >= start) & (df["date"] <= end)]

    trades = []
    last_entry_idx = -10  # prevent overlapping trade windows

    for i in window.index:
        if i < 1 or i <= last_entry_idx + 2:
            continue  # skip if too close to previous entry

        row = records[i]
        prev = records[i - 1]
        ok, intensity, reason = _can_enter(row, prev, CFG)
        if not ok:
            continue

        entry_price = row["close"]
        if entry_price <= 0 or np.isnan(entry_price):
            continue

        atr = row.get("atr_20", 0)
        if not atr or np.isnan(atr) or atr <= 0:
            atr = entry_price * 0.02

        atr_pct = atr / entry_price * 100

        last_entry_idx = i

        # Track forward
        peak_pnl = 0.0
        peak_bar = 0
        trough_pnl = 0.0
        trough_bar = 0
        bars = []

        for j in range(max_hold + 1):
            idx = i + j
            if idx >= n:
                break
            r = records[idx]
            close = r.get("close", np.nan)
            if np.isnan(close):
                continue

            pnl = (close / entry_price - 1) * 100
            dd_from_peak = pnl - peak_pnl if peak_pnl > 0 else 0.0

            bar_data = {
                "bar": j,
                "pnl": round(pnl, 3),
                "close": close,
                "dd_from_peak": round(dd_from_peak, 3),
            }
            for feat in TRACK_FEATURES:
                val = r.get(feat, np.nan)
                bar_data[feat] = val if not (isinstance(val, float) and np.isnan(val)) else None

            bars.append(bar_data)

            if pnl > peak_pnl:
                peak_pnl = pnl
                peak_bar = j
            if pnl < trough_pnl:
                trough_pnl = pnl
                trough_bar = j

        if len(bars) < 5:
            continue

        # Determine outcome at various horizons
        pnl_at = {}
        for h in [5, 10, 15, 20, 30]:
            if h < len(bars):
                pnl_at[h] = bars[h]["pnl"]
            else:
                pnl_at[h] = bars[-1]["pnl"] if bars else np.nan

        # Features at entry
        entry_feats = {}
        for feat in TRACK_FEATURES:
            entry_feats[f"entry_{feat}"] = bars[0].get(feat)

        # Features at MFE bar
        mfe_feats = {}
        mfe_bar_data = bars[peak_bar] if peak_bar < len(bars) else bars[0]
        for feat in TRACK_FEATURES:
            mfe_feats[f"mfe_{feat}"] = mfe_bar_data.get(feat)

        # Features 1 bar after MFE (if available)
        post_mfe_feats = {}
        if peak_bar + 1 < len(bars):
            post_bar = bars[peak_bar + 1]
            for feat in TRACK_FEATURES:
                post_mfe_feats[f"post_mfe_{feat}"] = post_bar.get(feat)

        # Drawdown from peak at various bars after MFE
        dd_after_mfe = {}
        for offset in [1, 2, 3, 5, 10]:
            look = peak_bar + offset
            if look < len(bars):
                dd_after_mfe[f"dd_after_mfe_{offset}"] = bars[look]["dd_from_peak"]

        trade = {
            "sym": sym,
            "date": row["date"].strftime("%Y-%m-%d"),
            "entry_price": round(entry_price, 2),
            "atr_pct": round(atr_pct, 3),
            "intensity": intensity,
            "regime": row.get("regime", ""),
            "mfe_pct": round(peak_pnl, 3),
            "mfe_bar": peak_bar,
            "mae_pct": round(abs(trough_pnl), 3),
            "mae_bar": trough_bar,
            "n_bars": len(bars) - 1,
            **{f"pnl_{h}": round(pnl_at[h], 3) for h in [5, 10, 15, 20, 30]},
            **entry_feats,
            **mfe_feats,
            **post_mfe_feats,
            **dd_after_mfe,
        }

        # Store full per-bar data for detailed analysis (only key columns)
        trade["_bars"] = bars

        trades.append(trade)

    return trades


def pct_below(series: pd.Series, threshold: float) -> float:
    return (series < threshold).mean() * 100


def pct_above(series: pd.Series, threshold: float) -> float:
    return (series > threshold).mean() * 100


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", default="NIFTY 500")
    parser.add_argument("--start", default="2025-06-01")
    parser.add_argument("--end", default="2026-03-15")
    parser.add_argument("--max-hold", type=int, default=MAX_HOLD)
    args = parser.parse_args()

    max_hold = args.max_hold
    out_path = OUT_DIR / "exit_study_nifty500.txt"
    symbols = get_symbols(args.watchlist)

    print(f"Scanning {len(symbols)} symbols from '{args.watchlist}' ({args.start} -> {args.end})")
    print(f"Max hold: {max_hold} bars")
    print(f"Output -> {out_path}\n")

    all_trades: list[dict] = []
    skipped: list[str] = []

    for idx, sym in enumerate(symbols, 1):
        print(f"  [{idx:>3}/{len(symbols)}] {sym:<20}", end=" ", flush=True)
        try:
            trades = scan_symbol(sym, args.start, args.end, max_hold)
            all_trades.extend(trades)
            print(f"{len(trades)} trades")
        except Exception as e:
            skipped.append(f"{sym}: {e}")
            print(f"SKIP - {e}")

    # Build DataFrame (without per-bar data)
    trade_rows = [{k: v for k, v in t.items() if k != "_bars"} for t in all_trades]
    df = pd.DataFrame(trade_rows) if trade_rows else pd.DataFrame()

    out = StringIO()
    W = 120

    out.write("=" * W + "\n")
    out.write(f"  EXIT STRATEGY EMPIRICAL STUDY — {args.watchlist}\n")
    out.write(f"  Period: {args.start} -> {args.end}\n")
    out.write(f"  Max hold: {max_hold} bars\n")
    out.write(f"  Symbols scanned: {len(symbols)}   Skipped: {len(skipped)}\n")
    out.write(f"  Total trades (NextGen entries): {len(df)}\n")
    out.write("=" * W + "\n\n")

    if df.empty:
        out.write("No trades found.\n")
    else:
        winners = df[df["pnl_10"] > 0]
        losers = df[df["pnl_10"] <= 0]

        # ═══════════════════════════════════════════════════════════════════════
        # 1. OVERALL TRADE STATISTICS
        # ═══════════════════════════════════════════════════════════════════════
        out.write("── 1. OVERALL TRADE STATISTICS ─────────────────────────────────────────────────────\n\n")
        out.write(f"  Total trades:    {len(df)}\n")
        out.write(f"  Winners (10d>0): {len(winners)} ({len(winners)/len(df)*100:.1f}%)\n")
        out.write(f"  Losers (10d<=0): {len(losers)} ({len(losers)/len(df)*100:.1f}%)\n\n")

        for label, sub in [("All", df), ("Winners", winners), ("Losers", losers)]:
            if sub.empty:
                continue
            out.write(f"  {label} (N={len(sub)}):\n")
            for h in [5, 10, 15, 20, 30]:
                col = f"pnl_{h}"
                if col in sub.columns:
                    out.write(f"    PnL {h:>2}d: avg={sub[col].mean():+.2f}%  med={sub[col].median():+.2f}%  "
                              f"std={sub[col].std():.2f}%  win={pct_above(sub[col], 0):.1f}%\n")
            out.write(f"    MFE:    avg={sub['mfe_pct'].mean():.2f}%  med={sub['mfe_pct'].median():.2f}%\n")
            out.write(f"    MAE:    avg={sub['mae_pct'].mean():.2f}%  med={sub['mae_pct'].median():.2f}%\n")
            out.write(f"    MFE bar: avg={sub['mfe_bar'].mean():.1f}  med={sub['mfe_bar'].median():.0f}\n")
            out.write(f"    ATR%:   avg={sub['atr_pct'].mean():.2f}%  med={sub['atr_pct'].median():.2f}%\n")
            out.write("\n")

        # ═══════════════════════════════════════════════════════════════════════
        # 2. MAE DISTRIBUTION — STOP-LOSS CALIBRATION
        # ═══════════════════════════════════════════════════════════════════════
        out.write("── 2. MAE DISTRIBUTION (Stop-Loss Calibration) ─────────────────────────────────────\n\n")
        out.write("  Question: At what drawdown level are trades unrecoverable?\n\n")

        # MAE in absolute % terms
        out.write("  MAE percentiles (max adverse excursion during hold):\n")
        for p in [25, 50, 75, 90, 95]:
            out.write(f"    P{p}: {df['mae_pct'].quantile(p/100):.2f}%\n")

        out.write("\n  MAE distribution: % of trades that dip below threshold:\n")
        out.write(f"  {'Threshold':<12} {'All':>8} {'Winners':>8} {'Losers':>8} {'Recovery%':>10}\n")
        out.write("  " + "-" * 50 + "\n")
        for thresh in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0]:
            all_pct = pct_above(df["mae_pct"], thresh)
            win_pct = pct_above(winners["mae_pct"], thresh) if not winners.empty else 0
            lose_pct = pct_above(losers["mae_pct"], thresh) if not losers.empty else 0
            # Recovery rate: of trades that dip below thresh, what % end positive at 10d?
            dipped = df[df["mae_pct"] > thresh]
            recovery = pct_above(dipped["pnl_10"], 0) if not dipped.empty else 0
            out.write(f"  > {thresh:<9.1f} {all_pct:>7.1f}% {win_pct:>7.1f}% {lose_pct:>7.1f}% {recovery:>9.1f}%\n")

        # MAE in ATR multiples
        if "atr_pct" in df.columns:
            df["mae_atr"] = df["mae_pct"] / df["atr_pct"].clip(lower=0.01)
            out.write("\n  MAE in ATR multiples:\n")
            out.write(f"  {'ATR mult':<12} {'All':>8} {'Winners':>8} {'Losers':>8} {'Recovery%':>10}\n")
            out.write("  " + "-" * 50 + "\n")
            for mult in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
                all_pct = pct_above(df["mae_atr"], mult)
                win_pct = pct_above(winners["mae_pct"] / winners["atr_pct"].clip(lower=0.01), mult) if not winners.empty else 0
                lose_pct = pct_above(losers["mae_pct"] / losers["atr_pct"].clip(lower=0.01), mult) if not losers.empty else 0
                dipped = df[df["mae_atr"] > mult]
                recovery = pct_above(dipped["pnl_10"], 0) if not dipped.empty else 0
                out.write(f"  > {mult:<9.1f} {all_pct:>7.1f}% {win_pct:>7.1f}% {lose_pct:>7.1f}% {recovery:>9.1f}%\n")

        # ═══════════════════════════════════════════════════════════════════════
        # 3. MFE DISTRIBUTION & TIMING
        # ═══════════════════════════════════════════════════════════════════════
        out.write("\n── 3. MFE DISTRIBUTION & TIMING ───────────────────────────────────────────────────\n\n")
        out.write("  Question: How much upside is available, and when does the peak occur?\n\n")

        out.write("  MFE percentiles:\n")
        for p in [25, 50, 75, 90, 95]:
            out.write(f"    P{p}: {df['mfe_pct'].quantile(p/100):.2f}%\n")

        out.write("\n  MFE bar (when peak occurs) percentiles:\n")
        for p in [25, 50, 75, 90]:
            out.write(f"    P{p}: bar {df['mfe_bar'].quantile(p/100):.0f}\n")

        out.write("\n  MFE timing distribution:\n")
        out.write(f"  {'Bar range':<15} {'Count':>6} {'%':>6} {'Avg MFE%':>10} {'Avg PnL10%':>12}\n")
        out.write("  " + "-" * 55 + "\n")
        for lo, hi, label in [(0, 0, "bar 0 (entry)"), (1, 2, "bars 1-2"), (3, 5, "bars 3-5"),
                               (6, 10, "bars 6-10"), (11, 15, "bars 11-15"), (16, 20, "bars 16-20"),
                               (21, 30, "bars 21-30")]:
            sub = df[(df["mfe_bar"] >= lo) & (df["mfe_bar"] <= hi)]
            if sub.empty:
                continue
            out.write(f"  {label:<15} {len(sub):>6} {len(sub)/len(df)*100:>5.1f}% {sub['mfe_pct'].mean():>+9.2f}% "
                      f"{sub['pnl_10'].mean():>+11.2f}%\n")

        # ═══════════════════════════════════════════════════════════════════════
        # 4. DRAWDOWN FROM PEAK (Trailing Stop Calibration)
        # ═══════════════════════════════════════════════════════════════════════
        out.write("\n── 4. DRAWDOWN FROM PEAK (Trailing Stop Calibration) ────────────────────────────\n\n")
        out.write("  Question: After MFE is hit, how much does it pull back? What threshold separates\n")
        out.write("  healthy pullback from dead trade?\n\n")

        # Compute max drawdown from peak for each trade (using per-bar data)
        max_dd_from_peak = []
        for t in all_trades:
            bars = t["_bars"]
            peak = 0.0
            max_dd = 0.0
            for b in bars:
                pnl = b["pnl"]
                if pnl > peak:
                    peak = pnl
                dd = peak - pnl
                if dd > max_dd:
                    max_dd = dd
            max_dd_from_peak.append(round(max_dd, 3))

        df["max_dd_from_peak"] = max_dd_from_peak

        out.write("  Max drawdown-from-peak percentiles:\n")
        for p in [25, 50, 75, 90, 95]:
            out.write(f"    P{p}: {df['max_dd_from_peak'].quantile(p/100):.2f}%\n")

        # For trades with MFE > 2%, what does the pullback look like?
        good_mfe = df[df["mfe_pct"] > 2.0]
        if not good_mfe.empty:
            out.write(f"\n  Trades with MFE > 2% (N={len(good_mfe)}):\n")
            out.write(f"    Avg MFE: {good_mfe['mfe_pct'].mean():.2f}%\n")
            out.write(f"    Avg max DD from peak: {good_mfe['max_dd_from_peak'].mean():.2f}%\n")
            out.write(f"    Avg PnL at 10d: {good_mfe['pnl_10'].mean():+.2f}%\n")
            out.write(f"    Avg PnL at 20d: {good_mfe['pnl_20'].mean():+.2f}%\n")

        # DD from peak in ATR multiples
        if "atr_pct" in df.columns:
            df["dd_peak_atr"] = df["max_dd_from_peak"] / df["atr_pct"].clip(lower=0.01)
            out.write("\n  Trailing stop analysis: If we exit when DD from peak exceeds X ATR:\n")
            out.write(f"  {'Trail ATR':<12} {'Triggered%':>10} {'Avg PnL at trigger':>20} {'Avg MFE captured':>18}\n")
            out.write("  " + "-" * 65 + "\n")
            for mult in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
                triggered = df[df["dd_peak_atr"] >= mult]
                not_triggered = df[df["dd_peak_atr"] < mult]
                trig_pct = len(triggered) / len(df) * 100
                # For triggered trades, estimate PnL at trail (MFE - trail_dd)
                trail_pnl = (triggered["mfe_pct"] - mult * triggered["atr_pct"]).mean() if not triggered.empty else 0
                mfe_captured = trail_pnl / triggered["mfe_pct"].mean() * 100 if not triggered.empty and triggered["mfe_pct"].mean() > 0 else 0
                out.write(f"  {mult:<12.1f} {trig_pct:>9.1f}% {trail_pnl:>+19.2f}% {mfe_captured:>17.1f}%\n")

        # ═══════════════════════════════════════════════════════════════════════
        # 5. FEATURE ANALYSIS AT KEY TRADE MILESTONES
        # ═══════════════════════════════════════════════════════════════════════
        out.write("\n── 5. FEATURE VALUES AT TRADE MILESTONES ──────────────────────────────────────────\n\n")
        out.write("  Question: Which features change direction before the price peaks?\n\n")

        key_feats = ["cts", "cts_slope", "cts_accel", "velocity_60_norm", "vel_dp5", "cdvl", "pdd_120"]
        out.write(f"  {'Feature':<20} {'At Entry':>10} {'At MFE':>10} {'Delta':>10} {'Post-MFE+1':>12}\n")
        out.write("  " + "-" * 65 + "\n")
        for feat in key_feats:
            entry_col = f"entry_{feat}"
            mfe_col = f"mfe_{feat}"
            post_col = f"post_mfe_{feat}"
            if entry_col in df.columns and mfe_col in df.columns:
                entry_val = df[entry_col].dropna().mean()
                mfe_val = df[mfe_col].dropna().mean()
                delta = mfe_val - entry_val
                post_val = df[post_col].dropna().mean() if post_col in df.columns else np.nan
                post_str = f"{post_val:>+11.4f}" if not np.isnan(post_val) else "        n/a"
                out.write(f"  {feat:<20} {entry_val:>+10.4f} {mfe_val:>+10.4f} {delta:>+10.4f} {post_str}\n")

        # Same but for winners vs losers
        for label, sub_df in [("Winners", winners), ("Losers", losers)]:
            out.write(f"\n  {label} (N={len(sub_df)}):\n")
            out.write(f"  {'Feature':<20} {'At Entry':>10} {'At MFE':>10} {'Delta':>10}\n")
            out.write("  " + "-" * 55 + "\n")
            for feat in key_feats:
                entry_col = f"entry_{feat}"
                mfe_col = f"mfe_{feat}"
                if entry_col in sub_df.columns and mfe_col in sub_df.columns:
                    entry_val = sub_df[entry_col].dropna().mean()
                    mfe_val = sub_df[mfe_col].dropna().mean()
                    delta = mfe_val - entry_val
                    out.write(f"  {feat:<20} {entry_val:>+10.4f} {mfe_val:>+10.4f} {delta:>+10.4f}\n")

        # ═══════════════════════════════════════════════════════════════════════
        # 6. PER-BAR FEATURE EVOLUTION (averaged across all trades)
        # ═══════════════════════════════════════════════════════════════════════
        out.write("\n── 6. PER-BAR FEATURE EVOLUTION (averaged across trades) ──────────────────────────\n\n")
        out.write("  Shows how features evolve bar-by-bar from entry. Helps identify leading indicators.\n\n")

        # Build per-bar aggregates
        bar_agg = {}
        for t in all_trades:
            for b in t["_bars"]:
                bar_num = b["bar"]
                if bar_num not in bar_agg:
                    bar_agg[bar_num] = {"pnl": [], "n": 0}
                    for feat in key_feats:
                        bar_agg[bar_num][feat] = []
                bar_agg[bar_num]["pnl"].append(b["pnl"])
                bar_agg[bar_num]["n"] += 1
                for feat in key_feats:
                    val = b.get(feat)
                    if val is not None:
                        bar_agg[bar_num][feat].append(val)

        out.write(f"  {'Bar':>4} {'N':>5} {'AvgPnL%':>8} {'CTS':>8} {'Slope':>8} {'Accel':>8} "
                  f"{'Vel60':>8} {'DP5':>5} {'CDVL':>8} {'PDD120':>8}\n")
        out.write("  " + "-" * 80 + "\n")
        for bar_num in sorted(bar_agg.keys()):
            if bar_num > max_hold:
                break
            ba = bar_agg[bar_num]
            n = ba["n"]
            avg_pnl = np.mean(ba["pnl"])
            vals = {}
            for feat in key_feats:
                vals[feat] = np.mean(ba[feat]) if ba[feat] else np.nan
            out.write(
                f"  {bar_num:>4} {n:>5} {avg_pnl:>+7.2f}% "
                f"{vals.get('cts', np.nan):>+7.3f} {vals.get('cts_slope', np.nan):>+7.4f} "
                f"{vals.get('cts_accel', np.nan):>+7.5f} {vals.get('velocity_60_norm', np.nan):>+7.3f} "
                f"{vals.get('vel_dp5', np.nan):>4.1f} {vals.get('cdvl', np.nan):>+7.3f} "
                f"{vals.get('pdd_120', np.nan):>+7.2f}\n"
            )

        # Same for winners only
        out.write(f"\n  PER-BAR EVOLUTION — WINNERS ONLY\n\n")
        bar_agg_w = {}
        for t in all_trades:
            trade_row = {k: v for k, v in t.items() if k != "_bars"}
            if trade_row.get("pnl_10", 0) <= 0:
                continue
            for b in t["_bars"]:
                bar_num = b["bar"]
                if bar_num not in bar_agg_w:
                    bar_agg_w[bar_num] = {"pnl": [], "n": 0}
                    for feat in key_feats:
                        bar_agg_w[bar_num][feat] = []
                bar_agg_w[bar_num]["pnl"].append(b["pnl"])
                bar_agg_w[bar_num]["n"] += 1
                for feat in key_feats:
                    val = b.get(feat)
                    if val is not None:
                        bar_agg_w[bar_num][feat].append(val)

        out.write(f"  {'Bar':>4} {'N':>5} {'AvgPnL%':>8} {'CTS':>8} {'Slope':>8} {'Accel':>8} "
                  f"{'Vel60':>8} {'DP5':>5} {'CDVL':>8} {'PDD120':>8}\n")
        out.write("  " + "-" * 80 + "\n")
        for bar_num in sorted(bar_agg_w.keys()):
            if bar_num > max_hold:
                break
            ba = bar_agg_w[bar_num]
            n = ba["n"]
            avg_pnl = np.mean(ba["pnl"])
            vals = {}
            for feat in key_feats:
                vals[feat] = np.mean(ba[feat]) if ba[feat] else np.nan
            out.write(
                f"  {bar_num:>4} {n:>5} {avg_pnl:>+7.2f}% "
                f"{vals.get('cts', np.nan):>+7.3f} {vals.get('cts_slope', np.nan):>+7.4f} "
                f"{vals.get('cts_accel', np.nan):>+7.5f} {vals.get('velocity_60_norm', np.nan):>+7.3f} "
                f"{vals.get('vel_dp5', np.nan):>4.1f} {vals.get('cdvl', np.nan):>+7.3f} "
                f"{vals.get('pdd_120', np.nan):>+7.2f}\n"
            )

        # Same for losers only
        out.write(f"\n  PER-BAR EVOLUTION — LOSERS ONLY\n\n")
        bar_agg_l = {}
        for t in all_trades:
            trade_row = {k: v for k, v in t.items() if k != "_bars"}
            if trade_row.get("pnl_10", 0) > 0:
                continue
            for b in t["_bars"]:
                bar_num = b["bar"]
                if bar_num not in bar_agg_l:
                    bar_agg_l[bar_num] = {"pnl": [], "n": 0}
                    for feat in key_feats:
                        bar_agg_l[bar_num][feat] = []
                bar_agg_l[bar_num]["pnl"].append(b["pnl"])
                bar_agg_l[bar_num]["n"] += 1
                for feat in key_feats:
                    val = b.get(feat)
                    if val is not None:
                        bar_agg_l[bar_num][feat].append(val)

        out.write(f"  {'Bar':>4} {'N':>5} {'AvgPnL%':>8} {'CTS':>8} {'Slope':>8} {'Accel':>8} "
                  f"{'Vel60':>8} {'DP5':>5} {'CDVL':>8} {'PDD120':>8}\n")
        out.write("  " + "-" * 80 + "\n")
        for bar_num in sorted(bar_agg_l.keys()):
            if bar_num > max_hold:
                break
            ba = bar_agg_l[bar_num]
            n = ba["n"]
            avg_pnl = np.mean(ba["pnl"])
            vals = {}
            for feat in key_feats:
                vals[feat] = np.mean(ba[feat]) if ba[feat] else np.nan
            out.write(
                f"  {bar_num:>4} {n:>5} {avg_pnl:>+7.2f}% "
                f"{vals.get('cts', np.nan):>+7.3f} {vals.get('cts_slope', np.nan):>+7.4f} "
                f"{vals.get('cts_accel', np.nan):>+7.5f} {vals.get('velocity_60_norm', np.nan):>+7.3f} "
                f"{vals.get('vel_dp5', np.nan):>4.1f} {vals.get('cdvl', np.nan):>+7.3f} "
                f"{vals.get('pdd_120', np.nan):>+7.2f}\n"
            )

        # ═══════════════════════════════════════════════════════════════════════
        # 7. FEATURE CORRELATION WITH TRADE OUTCOME
        # ═══════════════════════════════════════════════════════════════════════
        out.write("\n── 7. FEATURE CORRELATION WITH TRADE OUTCOME ──────────────────────────────────────\n\n")
        out.write("  Spearman rank correlation of entry features with forward PnL.\n\n")

        from scipy.stats import spearmanr

        out.write(f"  {'Feature':<25} {'r vs PnL5':>10} {'r vs PnL10':>11} {'r vs PnL20':>11}\n")
        out.write("  " + "-" * 60 + "\n")
        for feat in TRACK_FEATURES:
            col = f"entry_{feat}"
            if col not in df.columns:
                continue
            valid = df[[col, "pnl_5", "pnl_10", "pnl_20"]].dropna()
            if len(valid) < 20:
                continue
            r5, _ = spearmanr(valid[col], valid["pnl_5"])
            r10, _ = spearmanr(valid[col], valid["pnl_10"])
            r20, _ = spearmanr(valid[col], valid["pnl_20"])
            out.write(f"  {feat:<25} {r5:>+10.3f} {r10:>+11.3f} {r20:>+11.3f}\n")

        # Also correlate feature DELTAS (entry to MFE)
        out.write("\n  Feature delta (entry→MFE) correlation with PnL10:\n\n")
        out.write(f"  {'Feature delta':<25} {'r vs PnL10':>11}\n")
        out.write("  " + "-" * 40 + "\n")
        for feat in key_feats:
            entry_col = f"entry_{feat}"
            mfe_col = f"mfe_{feat}"
            if entry_col in df.columns and mfe_col in df.columns:
                delta = df[mfe_col] - df[entry_col]
                valid = pd.DataFrame({"delta": delta, "pnl_10": df["pnl_10"]}).dropna()
                if len(valid) < 20:
                    continue
                r, _ = spearmanr(valid["delta"], valid["pnl_10"])
                out.write(f"  d_{feat:<22} {r:>+11.3f}\n")

        # ═══════════════════════════════════════════════════════════════════════
        # 8. REGIME BREAKDOWN
        # ═══════════════════════════════════════════════════════════════════════
        out.write("\n── 8. REGIME BREAKDOWN ─────────────────────────────────────────────────────────────\n\n")
        gb = df.groupby("regime", observed=True).agg(
            n=("pnl_10", "count"),
            win10=("pnl_10", lambda x: (x > 0).mean() * 100),
            avg10=("pnl_10", "mean"),
            avg_mfe=("mfe_pct", "mean"),
            avg_mae=("mae_pct", "mean"),
            avg_mfe_bar=("mfe_bar", "mean"),
        ).reset_index().sort_values("n", ascending=False)

        out.write(f"  {'Regime':<15} {'N':>6} {'Win10%':>8} {'Avg10%':>9} {'AvgMFE%':>9} {'AvgMAE%':>9} {'MFEbar':>8}\n")
        out.write("  " + "-" * 70 + "\n")
        for _, r in gb.iterrows():
            out.write(f"  {str(r['regime']):<15} {int(r['n']):>6} {r['win10']:>7.1f}% {r['avg10']:>+8.2f}% "
                      f"{r['avg_mfe']:>8.2f}% {r['avg_mae']:>8.2f}% {r['avg_mfe_bar']:>7.1f}\n")

    if skipped:
        out.write(f"\n── SKIPPED ({len(skipped)}) ─────────────────────────────────────────────────────\n")
        for s in skipped[:50]:
            out.write(f"  {s}\n")
        if len(skipped) > 50:
            out.write(f"  ... and {len(skipped) - 50} more\n")

    report = out.getvalue()
    with open(out_path, "w") as f:
        f.write(report)

    # Console summary
    print("\n" + "=" * W)
    lines = report.split("\n")
    for line in lines:
        if any(x in line for x in [
            "====", "EXIT STRATEGY", "Period", "Total trades", "Winners", "Losers",
            "── 1.", "── 2.", "── 3.", "── 4.", "── 5.", "── 6.", "── 7.", "── 8.",
            "PnL 10d:", "MFE:", "MAE:", "Recovery",
        ]):
            print(line)
    print(f"\nFull report -> {out_path}")


if __name__ == "__main__":
    main()
