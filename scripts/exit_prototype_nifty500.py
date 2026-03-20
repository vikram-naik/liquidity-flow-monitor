"""
Exit Strategy Prototype — compare exit variants across NIFTY 500.

Replays each NextGen entry bar-by-bar with different exit strategies.

Strategies:
  A  current      — existing can_exit() logic
  B  hold_10      — hold exactly 10 bars (benchmark)
  C  stop_only    — 2.5 ATR hard stop, else hold 30 bars
  D  stop+trail   — 2.5 ATR stop + 1.0 ATR trail (activates at +1 ATR)
  E  stop+fg1     — 2.5 ATR stop + feature gate: slope<0 after bar 5
  F  stop+fg2+tr  — 2.5 ATR stop + fg: slope<0 AND cts<entry_cts bar 5+ + 1.0 ATR trail
  G  stop+fg3+tr  — 2.5 ATR stop + fg: slope<0 AND cts<0 bar 7+ + 1.0 ATR trail
  H  stop+fg4+tr  — 2.5 ATR stop + fg: slope<0 AND accel<0 bar 5+ + 1.0 ATR trail
  I  tight+fg2+tr — 2.0 ATR stop + fg2 + 1.0 ATR trail
  J  wide+fg3+tr  — 3.0 ATR stop + fg: slope<0 AND cts<0 bar 7+ + 1.5 ATR trail at +1 ATR

Output: output/exit_prototype_nifty500.txt

Usage:
    venv/bin/python3 scripts/exit_prototype_nifty500.py
    venv/bin/python3 scripts/exit_prototype_nifty500.py --watchlist "NIFTY 50"
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from io import StringIO

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.nextgen import _can_enter, NextGenEntryConfig
from src.trading.signals.price_divergence import can_exit, PriceDivergenceExitConfig
from src.trading.signals.base import Trade

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"
OUT_DIR = Path(__file__).resolve().parent.parent / "output"
OUT_DIR.mkdir(exist_ok=True)

ENTRY_CFG = NextGenEntryConfig()
EXIT_CFG = PriceDivergenceExitConfig()
MAX_HOLD = 30


# ─── Exit strategy definitions ────────────────────────────────────────────────

@dataclass
class ExitStrategy:
    name: str
    stop_atr: float          # hard stop in ATR multiples (0 = no stop)
    trail_atr: float         # trailing stop in ATR multiples (0 = no trail)
    trail_activate_atr: float  # profit in ATR multiples before trail activates
    fg_min_bars: int         # minimum bars before feature gate activates (0 = no gate)
    fg_type: str             # feature gate type: "", "slope", "slope+cts_entry", "slope+cts_zero", "slope+accel"
    use_current_exit: bool   # use current can_exit() instead


STRATEGIES = [
    ExitStrategy("A  current",       0,   0,   0,  0, "",                  True),
    ExitStrategy("B  hold_10",       0,   0,   0,  0, "",                  False),
    ExitStrategy("C  stop_only",     2.5, 0,   0,  0, "",                  False),
    ExitStrategy("D  stop+trail",    2.5, 1.0, 1.0, 0, "",                 False),
    ExitStrategy("E  stop+fg1",      2.5, 0,   0,  5, "slope",             False),
    ExitStrategy("F  stop+fg2+tr",   2.5, 1.0, 1.0, 5, "slope+cts_entry",  False),
    ExitStrategy("G  stop+fg3+tr",   2.5, 1.0, 1.0, 7, "slope+cts_zero",   False),
    ExitStrategy("H  stop+fg4+tr",   2.5, 1.0, 1.0, 5, "slope+accel",      False),
    ExitStrategy("I  tight+fg2+tr",  2.0, 1.0, 1.0, 5, "slope+cts_entry",  False),
    ExitStrategy("J  wide+fg3+tr",   3.0, 1.5, 1.0, 7, "slope+cts_zero",   False),
]


def check_feature_gate(fg_type: str, row: dict, entry_cts: float) -> bool:
    """Returns True if feature gate triggers (should exit)."""
    slope = row.get("cts_slope", np.nan)
    if np.isnan(slope) or slope >= 0:
        return False  # slope still positive — no exit

    # slope < 0 at this point
    if fg_type == "slope":
        return True

    if fg_type == "slope+cts_entry":
        cts = row.get("cts", np.nan)
        return not np.isnan(cts) and cts < entry_cts

    if fg_type == "slope+cts_zero":
        cts = row.get("cts", np.nan)
        return not np.isnan(cts) and cts < 0

    if fg_type == "slope+accel":
        accel = row.get("cts_accel", np.nan)
        return not np.isnan(accel) and accel < 0

    return False


def simulate_strategy(strat: ExitStrategy, records: list[dict], entry_idx: int,
                      entry_price: float, atr: float, cwvap_values: list[float]) -> dict:
    """Simulate a single exit strategy for one trade. Returns trade result."""
    n = len(records)
    atr_pct = atr / entry_price if entry_price > 0 else 0.02
    entry_cts = records[entry_idx].get("cts", 0.0)

    peak_close = entry_price
    peak_pnl = 0.0
    exit_bar = 0
    exit_reason = ""
    exit_pnl = 0.0
    mfe_pct = 0.0
    mae_pct = 0.0

    # For strategy B (hold_10), just compute PnL at bar 10
    if strat.name == "B  hold_10":
        target = min(entry_idx + 10, n - 1)
        close = records[target]["close"]
        pnl = (close / entry_price - 1) * 100
        # Compute MFE/MAE over 10 bars
        for j in range(entry_idx, target + 1):
            c = records[j]["close"]
            p = (c / entry_price - 1) * 100
            mfe_pct = max(mfe_pct, p)
            mae_pct = max(mae_pct, -p)
        return {
            "exit_bar": target - entry_idx,
            "exit_pnl": round(pnl, 3),
            "exit_reason": "hold_10",
            "mfe_pct": round(mfe_pct, 3),
            "mae_pct": round(mae_pct, 3),
        }

    # For strategy A (current exit), use can_exit()
    if strat.use_current_exit:
        trade = Trade(
            symbol="", entry_date="", entry_price=entry_price,
            entry_idx=entry_idx, atr_at_entry=atr,
            soft_filters_passed=0,
            regime_at_entry=str(records[entry_idx].get("regime", "")),
            psz_at_entry=records[entry_idx].get("price_slope_z", 0.0) or 0.0,
        )
        delivery_bad_count = 0
        for j in range(entry_idx + 1, min(entry_idx + MAX_HOLD + 1, n)):
            row = records[j]
            prev = records[j - 1]
            close = row.get("close", np.nan)
            if isinstance(close, float) and np.isnan(close):
                continue

            pnl = (close / entry_price - 1) * 100
            mfe_pct = max(mfe_pct, pnl)
            mae_pct = max(mae_pct, -pnl)

            if close > peak_close:
                peak_close = close

            bars_held = j - entry_idx

            # Hard stop (same as NextGenSignal.check_exit)
            if pnl < -(2.0 * atr_pct * 100):
                return {
                    "exit_bar": bars_held,
                    "exit_pnl": round(pnl, 3),
                    "exit_reason": "hard_stop",
                    "mfe_pct": round(mfe_pct, 3),
                    "mae_pct": round(mae_pct, 3),
                }

            qualifies, intensity, reason = can_exit(trade, row, prev, EXIT_CFG, cwvap_values)
            if qualifies:
                return {
                    "exit_bar": bars_held,
                    "exit_pnl": round(pnl, 3),
                    "exit_reason": reason,
                    "mfe_pct": round(mfe_pct, 3),
                    "mae_pct": round(mae_pct, 3),
                }

        # Hold to end
        last_idx = min(entry_idx + MAX_HOLD, n - 1)
        close = records[last_idx]["close"]
        pnl = (close / entry_price - 1) * 100
        return {
            "exit_bar": last_idx - entry_idx,
            "exit_pnl": round(pnl, 3),
            "exit_reason": "max_hold",
            "mfe_pct": round(mfe_pct, 3),
            "mae_pct": round(mae_pct, 3),
        }

    # Generic layered strategy
    trail_active = False
    trail_peak = 0.0

    for j in range(entry_idx + 1, min(entry_idx + MAX_HOLD + 1, n)):
        row = records[j]
        close = row.get("close", np.nan)
        if isinstance(close, float) and np.isnan(close):
            continue

        bars_held = j - entry_idx
        pnl = (close / entry_price - 1) * 100
        pnl_atr = pnl / (atr_pct * 100) if atr_pct > 0 else 0

        mfe_pct = max(mfe_pct, pnl)
        mae_pct = max(mae_pct, -pnl)

        if close > peak_close:
            peak_close = close
        if pnl > trail_peak:
            trail_peak = pnl

        # Layer 1: Hard stop
        if strat.stop_atr > 0:
            if pnl_atr < -strat.stop_atr:
                return {
                    "exit_bar": bars_held,
                    "exit_pnl": round(pnl, 3),
                    "exit_reason": "hard_stop",
                    "mfe_pct": round(mfe_pct, 3),
                    "mae_pct": round(mae_pct, 3),
                }

        # Layer 2: Feature gate (after min bars)
        if strat.fg_min_bars > 0 and bars_held >= strat.fg_min_bars and strat.fg_type:
            if check_feature_gate(strat.fg_type, row, entry_cts):
                return {
                    "exit_bar": bars_held,
                    "exit_pnl": round(pnl, 3),
                    "exit_reason": f"fg:{strat.fg_type}",
                    "mfe_pct": round(mfe_pct, 3),
                    "mae_pct": round(mae_pct, 3),
                }

        # Layer 3: Trailing stop (activates when profit exceeds threshold)
        if strat.trail_atr > 0:
            if not trail_active and pnl_atr >= strat.trail_activate_atr:
                trail_active = True

            if trail_active:
                dd_from_peak_atr = (trail_peak - pnl) / (atr_pct * 100) if atr_pct > 0 else 0
                if dd_from_peak_atr >= strat.trail_atr:
                    return {
                        "exit_bar": bars_held,
                        "exit_pnl": round(pnl, 3),
                        "exit_reason": "trail_stop",
                        "mfe_pct": round(mfe_pct, 3),
                        "mae_pct": round(mae_pct, 3),
                    }

    # Hold to end
    last_idx = min(entry_idx + MAX_HOLD, n - 1)
    close = records[last_idx]["close"]
    pnl = (close / entry_price - 1) * 100
    return {
        "exit_bar": last_idx - entry_idx,
        "exit_pnl": round(pnl, 3),
        "exit_reason": "max_hold",
        "mfe_pct": round(mfe_pct, 3),
        "mae_pct": round(mae_pct, 3),
    }


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
    """Find all NextGen entries, simulate all exit strategies."""
    result = DivergenceEngine(sym).run()
    df = result.ledger.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.reset_index(drop=True)
    records = df.to_dict("records")
    n = len(records)

    # Build cwvap_values list for current exit logic
    cwvap_values = [r.get("cwvap", np.nan) for r in records]

    window = df[(df["date"] >= start) & (df["date"] <= end)]

    trades = []
    last_entry_idx = -10

    for i in window.index:
        if i < 1 or i <= last_entry_idx + 2:
            continue

        row = records[i]
        prev = records[i - 1]
        ok, intensity, reason = _can_enter(row, prev, ENTRY_CFG)
        if not ok:
            continue

        entry_price = row["close"]
        if entry_price <= 0 or np.isnan(entry_price):
            continue

        atr = row.get("atr_20", 0)
        if not atr or np.isnan(atr) or atr <= 0:
            atr = entry_price * 0.02

        last_entry_idx = i

        trade_base = {
            "sym": sym,
            "date": row["date"].strftime("%Y-%m-%d"),
            "entry_price": round(entry_price, 2),
            "atr_pct": round(atr / entry_price * 100, 3),
            "regime": row.get("regime", ""),
        }

        # Run each strategy
        for si, strat in enumerate(STRATEGIES):
            result = simulate_strategy(strat, records, i, entry_price, atr, cwvap_values)
            trade_base[f"s{si}_pnl"] = result["exit_pnl"]
            trade_base[f"s{si}_bar"] = result["exit_bar"]
            trade_base[f"s{si}_reason"] = result["exit_reason"]
            trade_base[f"s{si}_mfe"] = result["mfe_pct"]
            trade_base[f"s{si}_mae"] = result["mae_pct"]

        trades.append(trade_base)

    return trades


def strat_summary(df: pd.DataFrame, si: int, label: str) -> dict:
    pnl_col = f"s{si}_pnl"
    bar_col = f"s{si}_bar"
    mfe_col = f"s{si}_mfe"
    mae_col = f"s{si}_mae"
    if pnl_col not in df.columns or df.empty:
        return {}
    pnl = df[pnl_col]
    return {
        "label": label,
        "n": len(df),
        "win": (pnl > 0).mean() * 100,
        "avg_pnl": pnl.mean(),
        "med_pnl": pnl.median(),
        "std_pnl": pnl.std(),
        "avg_bar": df[bar_col].mean(),
        "avg_mfe": df[mfe_col].mean(),
        "avg_mae": df[mae_col].mean(),
        "payoff": pnl[pnl > 0].mean() / abs(pnl[pnl <= 0].mean()) if (pnl <= 0).any() and (pnl > 0).any() else 0,
        "expectancy": pnl.mean(),  # same as avg_pnl, for clarity
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", default="NIFTY 500")
    parser.add_argument("--start", default="2025-06-01")
    parser.add_argument("--end", default="2026-03-15")
    args = parser.parse_args()

    out_path = OUT_DIR / "exit_prototype_nifty500.txt"
    symbols = get_symbols(args.watchlist)

    print(f"Scanning {len(symbols)} symbols from '{args.watchlist}' ({args.start} -> {args.end})")
    print(f"Strategies: {len(STRATEGIES)}")
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
            skipped.append(f"{sym}: {e}")
            print(f"SKIP - {e}")

    df = pd.DataFrame(all_trades) if all_trades else pd.DataFrame()

    out = StringIO()
    W = 130

    out.write("=" * W + "\n")
    out.write(f"  EXIT STRATEGY PROTOTYPE — {args.watchlist}\n")
    out.write(f"  Period: {args.start} -> {args.end}   Max hold: {MAX_HOLD} bars\n")
    out.write(f"  Symbols: {len(symbols)}   Skipped: {len(skipped)}   Total trades: {len(df)}\n")
    out.write("=" * W + "\n\n")

    out.write("  Strategy descriptions:\n")
    for si, strat in enumerate(STRATEGIES):
        parts = []
        if strat.use_current_exit:
            parts.append("current can_exit()")
        elif strat.name == "B  hold_10":
            parts.append("hold 10 bars")
        else:
            if strat.stop_atr > 0:
                parts.append(f"stop {strat.stop_atr} ATR")
            if strat.fg_type:
                parts.append(f"fg:{strat.fg_type} bar{strat.fg_min_bars}+")
            if strat.trail_atr > 0:
                parts.append(f"trail {strat.trail_atr} ATR @+{strat.trail_activate_atr} ATR")
        out.write(f"    {strat.name:<22} = {' + '.join(parts)}\n")
    out.write("\n")

    if df.empty:
        out.write("No trades found.\n")
    else:
        # ── Overall Summary ───────────────────────────────────────────────────
        out.write("── OVERALL SUMMARY ─────────────────────────────────────────────────────────────────\n\n")
        hdr = (f"  {'Strategy':<22} {'N':>5} {'Win%':>6} {'AvgPnL%':>8} {'MedPnL%':>8} "
               f"{'StdPnL%':>8} {'AvgBar':>7} {'AvgMFE%':>8} {'AvgMAE%':>8} {'Payoff':>7}\n")
        out.write(hdr)
        out.write("  " + "-" * (W - 2) + "\n")

        summaries = {}
        for si, strat in enumerate(STRATEGIES):
            s = strat_summary(df, si, strat.name)
            summaries[si] = s
            if s:
                out.write(
                    f"  {s['label']:<22} {s['n']:>5} {s['win']:>5.1f}% {s['avg_pnl']:>+7.2f}% "
                    f"{s['med_pnl']:>+7.2f}% {s['std_pnl']:>7.2f}% {s['avg_bar']:>6.1f} "
                    f"{s['avg_mfe']:>7.2f}% {s['avg_mae']:>7.2f}% {s['payoff']:>6.2f}x\n"
                )

        # ── Delta from current (A) ───────────────────────────────────────────
        out.write("\n── DELTA FROM CURRENT EXIT (A) ─────────────────────────────────────────────────────\n\n")
        base = summaries.get(0, {})
        if base:
            out.write(f"  {'Strategy':<22} {'dWin%':>7} {'dAvgPnL':>9} {'dMedPnL':>9} {'dAvgBar':>8} {'dPayoff':>8}\n")
            out.write("  " + "-" * 50 + "\n")
            for si, strat in enumerate(STRATEGIES):
                s = summaries.get(si, {})
                if s:
                    out.write(
                        f"  {strat.name:<22} {s['win']-base['win']:>+6.1f}% "
                        f"{s['avg_pnl']-base['avg_pnl']:>+8.2f}% "
                        f"{s['med_pnl']-base['med_pnl']:>+8.2f}% "
                        f"{s['avg_bar']-base['avg_bar']:>+7.1f} "
                        f"{s['payoff']-base['payoff']:>+7.2f}x\n"
                    )

        # ── Exit Reason Breakdown per strategy ────────────────────────────────
        out.write("\n── EXIT REASON BREAKDOWN ───────────────────────────────────────────────────────────\n")
        for si, strat in enumerate(STRATEGIES):
            reason_col = f"s{si}_reason"
            pnl_col = f"s{si}_pnl"
            bar_col = f"s{si}_bar"
            if reason_col not in df.columns:
                continue
            gb = df.groupby(reason_col).agg(
                n=(pnl_col, "count"),
                win=(pnl_col, lambda x: (x > 0).mean() * 100),
                avg_pnl=(pnl_col, "mean"),
                avg_bar=(bar_col, "mean"),
            ).reset_index().sort_values("n", ascending=False)

            out.write(f"\n  {strat.name}:\n")
            out.write(f"  {'Reason':<40} {'N':>5} {'Win%':>6} {'AvgPnL%':>8} {'AvgBar':>7}\n")
            out.write("  " + "-" * 70 + "\n")
            for _, r in gb.iterrows():
                out.write(f"  {str(r[reason_col]):<40} {int(r['n']):>5} {r['win']:>5.1f}% "
                          f"{r['avg_pnl']:>+7.2f}% {r['avg_bar']:>6.1f}\n")

        # ── Regime breakdown for top strategies ───────────────────────────────
        out.write("\n── REGIME BREAKDOWN (selected strategies) ──────────────────────────────────────────\n")
        # Pick current (0), best new, and hold_10 (1)
        best_si = max(range(2, len(STRATEGIES)),
                      key=lambda si: summaries.get(si, {}).get("avg_pnl", -999))
        for si in [0, 1, best_si]:
            strat = STRATEGIES[si]
            pnl_col = f"s{si}_pnl"
            bar_col = f"s{si}_bar"
            mfe_col = f"s{si}_mfe"
            gb = df.groupby("regime", observed=True).agg(
                n=(pnl_col, "count"),
                win=(pnl_col, lambda x: (x > 0).mean() * 100),
                avg_pnl=(pnl_col, "mean"),
                avg_bar=(bar_col, "mean"),
                avg_mfe=(mfe_col, "mean"),
            ).reset_index().sort_values("n", ascending=False)
            out.write(f"\n  {strat.name}:\n")
            out.write(f"  {'Regime':<15} {'N':>5} {'Win%':>6} {'AvgPnL%':>8} {'AvgBar':>7} {'AvgMFE%':>8}\n")
            out.write("  " + "-" * 55 + "\n")
            for _, r in gb.iterrows():
                out.write(f"  {str(r['regime']):<15} {int(r['n']):>5} {r['win']:>5.1f}% "
                          f"{r['avg_pnl']:>+7.2f}% {r['avg_bar']:>6.1f} {r['avg_mfe']:>7.2f}%\n")

        # ── MFE Capture Analysis ──────────────────────────────────────────────
        out.write("\n── MFE CAPTURE ANALYSIS ────────────────────────────────────────────────────────────\n\n")
        out.write("  How much of the available MFE does each strategy capture?\n\n")
        out.write(f"  {'Strategy':<22} {'AvgPnL%':>8} {'AvgMFE%':>8} {'Capture%':>9} {'LeftOnTable%':>13}\n")
        out.write("  " + "-" * 65 + "\n")
        for si, strat in enumerate(STRATEGIES):
            pnl_col = f"s{si}_pnl"
            mfe_col = f"s{si}_mfe"
            if pnl_col in df.columns and mfe_col in df.columns:
                avg_pnl = df[pnl_col].mean()
                avg_mfe = df[mfe_col].mean()
                capture = avg_pnl / avg_mfe * 100 if avg_mfe > 0 else 0
                left = avg_mfe - avg_pnl
                out.write(f"  {strat.name:<22} {avg_pnl:>+7.2f}% {avg_mfe:>7.2f}% "
                          f"{capture:>8.1f}% {left:>12.2f}%\n")

        # ── Per-Symbol Comparison: current vs best ────────────────────────────
        if best_si != 0:
            out.write(f"\n── PER-SYMBOL: current vs {STRATEGIES[best_si].name} (≥3 trades) ──────────────\n\n")
            sym_rows = []
            for sym in df["sym"].unique():
                sub = df[df["sym"] == sym]
                if len(sub) < 3:
                    continue
                sym_rows.append({
                    "sym": sym,
                    "n": len(sub),
                    "pnl_cur": sub["s0_pnl"].mean(),
                    "pnl_new": sub[f"s{best_si}_pnl"].mean(),
                    "win_cur": (sub["s0_pnl"] > 0).mean() * 100,
                    "win_new": (sub[f"s{best_si}_pnl"] > 0).mean() * 100,
                })
            if sym_rows:
                sdf = pd.DataFrame(sym_rows).sort_values("pnl_new", ascending=False)
                out.write(f"  {'Sym':<16} {'N':>4} {'Win_cur%':>9} {'PnL_cur%':>9} {'Win_new%':>9} {'PnL_new%':>9} {'Delta%':>8}\n")
                out.write("  " + "-" * 70 + "\n")
                for _, r in sdf.head(30).iterrows():
                    delta = r["pnl_new"] - r["pnl_cur"]
                    out.write(f"  {r['sym']:<16} {int(r['n']):>4} {r['win_cur']:>8.1f}% {r['pnl_cur']:>+8.2f}% "
                              f"{r['win_new']:>8.1f}% {r['pnl_new']:>+8.2f}% {delta:>+7.2f}%\n")
                out.write("  ...\n")
                for _, r in sdf.tail(10).iterrows():
                    delta = r["pnl_new"] - r["pnl_cur"]
                    out.write(f"  {r['sym']:<16} {int(r['n']):>4} {r['win_cur']:>8.1f}% {r['pnl_cur']:>+8.2f}% "
                              f"{r['win_new']:>8.1f}% {r['pnl_new']:>+8.2f}% {delta:>+7.2f}%\n")

                # How many symbols improve vs degrade
                improved = sum(1 for r in sym_rows if r["pnl_new"] > r["pnl_cur"])
                out.write(f"\n  Symbols improved: {improved}/{len(sym_rows)} ({improved/len(sym_rows)*100:.1f}%)\n")

    if skipped:
        out.write(f"\n── SKIPPED ({len(skipped)}) ─────────────────────────────────────────────────────\n")
        for s in skipped[:20]:
            out.write(f"  {s}\n")

    report = out.getvalue()
    with open(out_path, "w") as f:
        f.write(report)

    # Console summary
    print("\n")
    lines = report.split("\n")
    in_section = False
    for line in lines:
        if "OVERALL SUMMARY" in line or "DELTA FROM" in line or "====" in line:
            in_section = True
        if in_section:
            print(line)
        if in_section and line.strip() == "" and "DELTA" not in line and "OVERALL" not in line:
            # Check if next meaningful line ends section
            pass
        if "EXIT REASON" in line:
            in_section = False
    print(f"\nFull report -> {out_path}")


if __name__ == "__main__":
    main()
