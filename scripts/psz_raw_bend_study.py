"""
PSZ Raw Bend Gate Study — Entry Timing Refinement.

When CTS == -1 AND BT == -1, PSZ raw can be flat (stock grinding at bottom).
The current psz_v gates (rising 3 bars, spread > 0.02) can pass on noise blips
while PSZ raw shows zero structural movement.

This study compares baseline savgol_cts entries vs a "PSZ raw bend" gate that
requires PSZ raw to show actual displacement over the last N bars before
allowing entry.

Usage:
    venv/bin/python3 scripts/psz_raw_bend_study.py --watchlist "NIFTY 50"
    venv/bin/python3 scripts/psz_raw_bend_study.py --symbol CIPLA
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
from src.trading.signals import SavgolCTSEntryConfig, SavgolCTSExitConfig, Trade, SignalFactory
from src.trading.signals.base import BaseEntryConfig, BaseExitConfig

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"


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


# ── PSZ Raw Bend Gate Variants ──────────────────────────────────────────────

@dataclass
class BendGateConfig:
    """PSZ raw displacement gate — requires actual PSZ raw movement."""
    name: str
    lookback: int       # how many bars back to measure PSZ raw delta
    min_delta: float    # minimum PSZ raw rise over lookback window
    enabled: bool = True


VARIANTS = [
    BendGateConfig("A_baseline", lookback=0, min_delta=0.0, enabled=False),
    BendGateConfig("B_bend_3b_0.02", lookback=3, min_delta=0.02),
    BendGateConfig("C_bend_3b_0.04", lookback=3, min_delta=0.04),
    BendGateConfig("D_bend_5b_0.02", lookback=5, min_delta=0.02),
    BendGateConfig("E_bend_5b_0.04", lookback=5, min_delta=0.04),
    BendGateConfig("F_bend_5b_0.06", lookback=5, min_delta=0.06),
    BendGateConfig("G_bend_3b_0.06", lookback=3, min_delta=0.06),
]


def check_entry_with_bend(
    row: dict,
    prev_row: dict,
    signal,
    entry_cfg: SavgolCTSEntryConfig,
    records: list[dict],
    idx: int,
    bend: BendGateConfig,
) -> tuple[bool, int, dict]:
    """Run baseline entry check, then optionally apply PSZ raw bend gate."""
    qualifies, soft_count, details = signal.check_entry(row, prev_row, entry_cfg, records, idx)
    if not qualifies:
        return False, soft_count, details

    # Baseline passes — apply bend gate if enabled
    if not bend.enabled:
        return True, soft_count, details

    if idx < bend.lookback:
        return False, 0, {"reason": "Insufficient history for bend gate"}

    psz_now = row.get("price_slope_z", np.nan)
    psz_back = records[idx - bend.lookback].get("price_slope_z", np.nan)
    if np.isnan(psz_now) or np.isnan(psz_back):
        return False, 0, {"reason": "Missing PSZ data for bend gate"}

    delta = psz_now - psz_back
    if delta < bend.min_delta:
        return False, 0, {"reason": f"PSZ raw bend too small ({delta:+.4f} < {bend.min_delta})"}

    return True, soft_count, details


# ── Trade Simulation ────────────────────────────────────────────────────────

def simulate_trades(
    ticker: str,
    df: pd.DataFrame,
    entry_cfg: SavgolCTSEntryConfig,
    exit_cfg: SavgolCTSExitConfig,
    signal,
    bend: BendGateConfig,
    start_date: str | None = None,
) -> list[Trade]:
    records = df.to_dict("records")
    n = len(records)
    trades = []
    in_trade = False
    trade = None
    peak_close = 0.0
    delivery_bad_count = 0
    pending_signal: dict | None = None
    cwvap_values = [records[0].get("cwvap", np.nan)]

    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            cwvap_values.append(row.get("cwvap", np.nan))
            continue

        cwvap_values.append(row.get("cwvap", np.nan))

        if in_trade:
            if close > peak_close:
                peak_close = close

            bars_held = i - trade.entry_idx
            mfe = max(trade.mfe_pct, (close / trade.entry_price - 1) * 100)
            mae_val = (close / trade.entry_price - 1) * 100
            mae = min(-trade.mae_pct, mae_val)
            trade.mfe_pct = mfe
            if -mae > trade.mae_pct:
                trade.mae_pct = -mae

            reason, delivery_bad_count = signal.check_exit(
                row, prev, trade, peak_close, bars_held,
                delivery_bad_count, cwvap_values, exit_cfg,
                records, i,
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
                soft_filters_passed=sig.get("soft_count", 0),
                psz_at_entry=psz_now if not np.isnan(psz_now) else 0.0,
                psz_peak=psz_now if not np.isnan(psz_now) else 0.0,
            )
            peak_close = close
            delivery_bad_count = 0
            in_trade = True

        else:
            qualifies, soft_count, fdetails = check_entry_with_bend(
                row, prev, signal, entry_cfg, records, i, bend,
            )
            if qualifies:
                pending_signal = {"soft_count": soft_count, "details": fdetails}

    # Filter by start_date if provided
    if start_date:
        trades = [t for t in trades if t.entry_date >= start_date]

    return trades


# ── Output ──────────────────────────────────────────────────────────────────

def summarise(trades: list[Trade]) -> dict:
    if not trades:
        return {"n": 0, "win_pct": 0, "avg_pnl": 0, "med_pnl": 0,
                "avg_mfe": 0, "avg_mae": 0, "payoff": 0, "avg_bars": 0}
    pnls = [t.pnl_pct for t in trades]
    mfes = [t.mfe_pct for t in trades]
    maes = [t.mae_pct for t in trades]
    bars = [t.duration for t in trades]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    avg_win = np.mean(winners) if winners else 0
    avg_loss = np.mean(losers) if losers else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    return {
        "n": len(trades),
        "win_pct": round(len(winners) / len(trades) * 100, 1),
        "avg_pnl": round(np.mean(pnls), 2),
        "med_pnl": round(np.median(pnls), 2),
        "avg_mfe": round(np.mean(mfes), 2),
        "avg_mae": round(np.mean(maes), 2),
        "payoff": round(payoff, 2),
        "avg_bars": round(np.mean(bars), 1),
    }


def print_trade_log(trades: list[Trade], variant_name: str):
    if not trades:
        return
    print(f"\n{'─' * 100}")
    print(f"TRADE LOG — {variant_name}")
    print(f"{'─' * 100}")
    rows = []
    for t in trades:
        rows.append([
            t.symbol, t.entry_date, t.exit_date,
            f"{t.pnl_pct:+.2f}", f"{t.mfe_pct:.2f}", f"{t.mae_pct:.2f}",
            t.duration, t.exit_reason,
        ])
    print(tabulate(rows, headers=["Symbol", "Entry", "Exit", "PnL%", "MFE%", "MAE%", "Bars", "Reason"],
                   tablefmt="simple"))


def print_blocked_signals(baseline_trades: list[Trade], variant_trades: list[Trade], variant_name: str):
    """Show which trades were blocked by the bend gate."""
    variant_keys = {(t.symbol, t.entry_date) for t in variant_trades}
    blocked = [t for t in baseline_trades if (t.symbol, t.entry_date) not in variant_keys]
    if not blocked:
        print(f"\n  {variant_name}: No trades blocked (identical to baseline)")
        return

    print(f"\n{'─' * 100}")
    print(f"BLOCKED by {variant_name} ({len(blocked)} trades)")
    print(f"{'─' * 100}")
    rows = []
    for t in blocked:
        rows.append([
            t.symbol, t.entry_date, t.exit_date,
            f"{t.pnl_pct:+.2f}", f"{t.mfe_pct:.2f}", f"{t.mae_pct:.2f}",
            t.duration, t.exit_reason,
        ])
    print(tabulate(rows, headers=["Symbol", "Entry", "Exit", "PnL%", "MFE%", "MAE%", "Bars", "Reason"],
                   tablefmt="simple"))
    b_pnls = [t.pnl_pct for t in blocked]
    b_wins = sum(1 for p in b_pnls if p > 0)
    print(f"  Blocked: {len(blocked)} trades, {b_wins} winners, avg PnL {np.mean(b_pnls):+.2f}%")


# ── Diagnostic: dump PSZ raw around signal dates ────────────────────────────

def dump_psz_context(ticker: str, df: pd.DataFrame, signal_dates: list[str], window: int = 8):
    """Print PSZ raw, psz_v, CTS, BT around each signal date."""
    records = df.to_dict("records")
    dates = [str(r.get("date", ""))[:10] for r in records]

    for sd in signal_dates:
        if sd not in dates:
            continue
        sig_idx = dates.index(sd)
        start = max(0, sig_idx - window)
        end = min(len(records), sig_idx + window + 1)

        print(f"\n  Context for {ticker} signal on {sd}:")
        print(f"  {'Date':>12s}  {'Close':>8s}  {'CTS':>7s}  {'BT':>7s}  {'PSZ_raw':>8s}  {'psz_v':>8s}  {'coh':>5s}  {'pdd':>7s}")
        for j in range(start, end):
            r = records[j]
            marker = " <<< SIGNAL" if j == sig_idx else ""
            d = str(r.get("date", ""))[:10]
            print(f"  {d:>12s}  {r.get('close', 0):8.1f}  {r.get('cts', 0):7.4f}  "
                  f"{r.get('cts_buy_threshold', 0):7.4f}  {r.get('price_slope_z', 0):8.4f}  "
                  f"{r.get('psz_v', 0):8.4f}  {r.get('coherence', 0):5.2f}  "
                  f"{r.get('pdd_120', 0):7.2f}{marker}")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PSZ Raw Bend Gate Study")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--watchlist", help="Watchlist name")
    group.add_argument("--symbol", help="Single symbol")
    parser.add_argument("--start-date", help="Start date for entries (YYYY-MM-DD)")
    parser.add_argument("--verbose", action="store_true", help="Print per-trade logs and PSZ context")
    args = parser.parse_args()

    if args.symbol:
        symbols = [args.symbol]
    else:
        symbols = get_watchlist_symbols(args.watchlist)

    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    print(f"PSZ Raw Bend Gate Study")
    print(f"Symbols: {len(symbols)}")
    print(f"Start date: {args.start_date or 'all'}")
    print(f"Entry config: psz_v_min={entry_cfg.psz_v_min}, psz_v_rising={entry_cfg.psz_v_rising_bars}, "
          f"psz_v_spread={entry_cfg.psz_v_min_spread}, psz_raw_max={entry_cfg.psz_raw_max}")
    print(f"\nVariants:")
    for v in VARIANTS:
        if v.enabled:
            print(f"  {v.name}: PSZ raw delta over {v.lookback} bars >= {v.min_delta}")
        else:
            print(f"  {v.name}: baseline (no bend gate)")

    # Collect trades per variant
    all_results: dict[str, list[Trade]] = {v.name: [] for v in VARIANTS}
    ledgers: dict[str, pd.DataFrame] = {}

    for i, sym in enumerate(symbols, 1):
        print(f"\n  [{i}/{len(symbols)}] {sym}...", end=" ", flush=True)
        try:
            engine = DivergenceEngine(sym)
            result = engine.run()
            df = result.ledger
            ledgers[sym] = df
        except Exception as e:
            print(f"SKIP — {e}")
            continue

        counts = []
        for v in VARIANTS:
            trades = simulate_trades(sym, df, entry_cfg, exit_cfg, signal, v, args.start_date)
            all_results[v.name].extend(trades)
            counts.append(len(trades))
        print(f"trades: {'/'.join(str(c) for c in counts)}")

    # ── Summary Table ───────────────────────────────────────────────────────
    print(f"\n{'=' * 100}")
    print("VARIANT COMPARISON")
    print(f"{'=' * 100}")
    summary_rows = []
    for v in VARIANTS:
        s = summarise(all_results[v.name])
        summary_rows.append([
            v.name, s["n"], f"{s['win_pct']}%", f"{s['avg_pnl']:+.2f}%",
            f"{s['med_pnl']:+.2f}%", f"{s['avg_mfe']:.2f}%", f"{s['avg_mae']:.2f}%",
            f"{s['payoff']:.2f}x", f"{s['avg_bars']:.1f}",
        ])
    print(tabulate(summary_rows,
                   headers=["Variant", "N", "Win%", "AvgPnL", "MedPnL", "AvgMFE", "AvgMAE", "Payoff", "AvgBars"],
                   tablefmt="simple"))

    # ── Blocked Trades Analysis ─────────────────────────────────────────────
    baseline_trades = all_results["A_baseline"]

    if baseline_trades:
        print(f"\n{'=' * 100}")
        print("BLOCKED TRADES ANALYSIS")
        print(f"{'=' * 100}")

        for v in VARIANTS:
            if not v.enabled:
                continue
            variant_trades = all_results[v.name]
            print_blocked_signals(baseline_trades, variant_trades, v.name)

    # ── Exit Reason Breakdown per Variant ───────────────────────────────────
    print(f"\n{'=' * 100}")
    print("EXIT REASON BREAKDOWN")
    print(f"{'=' * 100}")
    for v in VARIANTS:
        trades = all_results[v.name]
        if not trades:
            continue
        reasons = {}
        for t in trades:
            r = t.exit_reason or "open"
            if r not in reasons:
                reasons[r] = []
            reasons[r].append(t.pnl_pct)

        print(f"\n  {v.name}:")
        reason_rows = []
        for r, pnls in sorted(reasons.items(), key=lambda x: -len(x[1])):
            w = sum(1 for p in pnls if p > 0)
            reason_rows.append([r, len(pnls), f"{w/len(pnls)*100:.0f}%", f"{np.mean(pnls):+.2f}%"])
        print(tabulate(reason_rows, headers=["Reason", "Count", "Win%", "AvgPnL"], tablefmt="simple"))

    # ── Verbose output ──────────────────────────────────────────────────────
    if args.verbose:
        # Print trade logs for baseline and best variant
        print_trade_log(all_results["A_baseline"], "A_baseline")

        # Dump PSZ context for baseline signal dates
        baseline_dates_by_sym: dict[str, list[str]] = {}
        for t in baseline_trades:
            baseline_dates_by_sym.setdefault(t.symbol, []).append(t.entry_date)

        print(f"\n{'=' * 100}")
        print("PSZ RAW CONTEXT AROUND SIGNAL DATES (baseline)")
        print(f"{'=' * 100}")
        for sym, dates in sorted(baseline_dates_by_sym.items()):
            if sym in ledgers:
                dump_psz_context(sym, ledgers[sym], dates)


if __name__ == "__main__":
    main()
