"""
PSZ_v Momentum Decay Study — Post-Entry Velocity Analysis.

Hypothesis: trades where psz_v amplitude decays rapidly post-entry
(narrowing cycles, fewer bars per half-cycle) are duds. Trades where
psz_v sustains amplitude or accelerates are winners.

Measures per trade (post-entry bars +1 to +10):
  - Cycle 1 amplitude: avg abs(psz_v) bars +1 to +5
  - Cycle 2 amplitude: avg abs(psz_v) bars +6 to +10
  - Amplitude decay ratio: cycle2 / cycle1 (< 1.0 = fading)
  - Peak abs(psz_v) in each cycle
  - Half-cycle widths: consecutive same-sign bar counts in each cycle
  - Correlation of all metrics with trade PnL

Two regime splits:
  - 2020-01-01 to 2023-12-31 (trending bull)
  - 2024-01-01 to present (choppy/volatile)

Usage:
    venv/bin/python3 scripts/psz_v_momentum_study.py --watchlist "NIFTY 50"
    venv/bin/python3 scripts/psz_v_momentum_study.py --symbol RELIANCE
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
from src.trading.signals import SignalFactory, SavgolCTSEntryConfig, SavgolCTSExitConfig
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


def count_half_cycles(psz_v_values: list[float]) -> list[int]:
    """Count consecutive same-sign bars (half-cycle widths).

    Returns list of half-cycle lengths found in the sequence.
    E.g. [+, +, +, -, -, +, +, +, +] -> [3, 2, 4]
    """
    if not psz_v_values:
        return []
    widths = []
    current_sign = 1 if psz_v_values[0] >= 0 else -1
    count = 1
    for v in psz_v_values[1:]:
        s = 1 if v >= 0 else -1
        if s == current_sign:
            count += 1
        else:
            widths.append(count)
            current_sign = s
            count = 1
    widths.append(count)
    return widths


def analyse_trade_momentum(records: list[dict], signal_idx: int, entry_idx: int,
                           exit_idx: int | None, pnl: float) -> dict | None:
    """Extract psz_v momentum metrics for a single trade."""
    n = len(records)

    # We need at least 10 bars post-entry for two full cycles
    max_bar = min(entry_idx + 10, n - 1)
    if exit_idx is not None:
        max_bar = min(max_bar, exit_idx)

    bars_available = max_bar - entry_idx
    if bars_available < 3:
        return None

    # Collect psz_v from entry+1 onward (entry bar is execution, +1 is first post-entry bar)
    post_entry_psz_v = []
    for j in range(1, 11):
        idx = entry_idx + j
        if idx >= n or (exit_idx is not None and idx > exit_idx):
            break
        post_entry_psz_v.append(records[idx].get("psz_v", np.nan))

    post_entry_psz_v = [v for v in post_entry_psz_v if not np.isnan(v)]
    if len(post_entry_psz_v) < 3:
        return None

    # Signal bar metrics
    signal_psz_v = records[signal_idx].get("psz_v", np.nan)
    signal_psz_raw = records[signal_idx].get("price_slope_z", np.nan)
    entry_close = records[entry_idx].get("close", np.nan)

    # Cycle 1: bars +1 to +5 (indices 0-4 in post_entry_psz_v)
    c1 = post_entry_psz_v[:5]
    c1_abs = [abs(v) for v in c1]
    c1_avg = np.mean(c1_abs) if c1_abs else np.nan
    c1_peak = max(c1_abs) if c1_abs else np.nan

    # Cycle 2: bars +6 to +10 (indices 5-9 in post_entry_psz_v)
    c2 = post_entry_psz_v[5:10]
    c2_abs = [abs(v) for v in c2]
    c2_avg = np.mean(c2_abs) if c2_abs else np.nan
    c2_peak = max(c2_abs) if c2_abs else np.nan

    # Decay ratio
    decay_ratio = (c2_avg / c1_avg) if (c1_avg and c1_avg > 0 and not np.isnan(c2_avg)) else np.nan

    # Half-cycle widths
    hc_widths = count_half_cycles(post_entry_psz_v)
    avg_hc_width = np.mean(hc_widths) if hc_widths else np.nan

    # Early half-cycles vs late half-cycles
    hc_early = hc_widths[:2] if len(hc_widths) >= 2 else hc_widths
    hc_late = hc_widths[2:] if len(hc_widths) > 2 else []
    avg_hc_early = np.mean(hc_early) if hc_early else np.nan
    avg_hc_late = np.mean(hc_late) if hc_late else np.nan

    # Bar-by-bar abs(psz_v) for the first 10 post-entry bars
    bar_vals = {}
    for j in range(min(10, len(post_entry_psz_v))):
        bar_vals[f"abs_psz_v_bar{j+1}"] = abs(post_entry_psz_v[j])

    result = {
        "signal_psz_v": signal_psz_v,
        "signal_psz_raw": signal_psz_raw,
        "entry_close": entry_close,
        "pnl": pnl,
        "winner": 1 if pnl > 0 else 0,
        "c1_avg_abs": c1_avg,
        "c1_peak_abs": c1_peak,
        "c2_avg_abs": c2_avg,
        "c2_peak_abs": c2_peak,
        "decay_ratio": decay_ratio,
        "avg_hc_width": avg_hc_width,
        "avg_hc_early": avg_hc_early,
        "avg_hc_late": avg_hc_late,
        "n_half_cycles": len(hc_widths),
        "bars_measured": len(post_entry_psz_v),
    }
    result.update(bar_vals)
    return result


def simulate_and_extract(ticker: str, signal, entry_cfg, exit_cfg,
                         start_date: str | None = None) -> list[dict]:
    """Run signal simulation and extract momentum metrics per trade."""
    try:
        engine = DivergenceEngine(ticker)
        result = engine.run()
    except Exception as e:
        print(f"  SKIP — {e}")
        return []

    df = result.ledger
    records = df.to_dict("records")
    n = len(records)

    trades_data = []
    in_trade = False
    trade_signal_idx = None
    trade_entry_idx = None
    trade_entry_price = 0.0
    peak_close = 0.0
    delivery_bad_count = 0
    pending_signal_idx = None
    cwvap_values = [records[0].get("cwvap", np.nan)]

    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        cwvap_values.append(row.get("cwvap", np.nan))

        if isinstance(close, float) and np.isnan(close):
            continue

        date_str = str(row.get("date", ""))[:10]

        if in_trade:
            if close > peak_close:
                peak_close = close
            bars_held = i - trade_entry_idx
            reason, delivery_bad_count = signal.check_exit(
                row, prev,
                type("T", (), {"entry_idx": trade_entry_idx, "entry_price": trade_entry_price,
                               "atr_at_entry": row.get("atr_20", 1), "psz_at_entry": 0,
                               "psz_peak": 0})(),
                peak_close, bars_held, delivery_bad_count, cwvap_values, exit_cfg,
            )
            if reason:
                pnl = (close / trade_entry_price - 1) * 100
                regime = records[trade_signal_idx].get("regime", "")
                entry_date = str(records[trade_entry_idx].get("date", ""))[:10]

                metrics = analyse_trade_momentum(records, trade_signal_idx, trade_entry_idx, i, pnl)
                if metrics:
                    metrics["symbol"] = ticker
                    metrics["entry_date"] = entry_date
                    metrics["exit_date"] = date_str
                    metrics["exit_reason"] = reason
                    metrics["regime"] = regime
                    metrics["duration"] = bars_held
                    if start_date is None or entry_date >= start_date:
                        trades_data.append(metrics)

                in_trade = False
                delivery_bad_count = 0

        elif pending_signal_idx is not None:
            atr = row.get("atr_20", 0) or 0
            if not atr or np.isnan(float(atr)):
                atr = close * 0.02
            trade_signal_idx = pending_signal_idx
            trade_entry_idx = i
            trade_entry_price = close
            peak_close = close
            delivery_bad_count = 0
            in_trade = True
            pending_signal_idx = None

        else:
            ok, intensity, det = signal.check_entry(row, prev, entry_cfg, records, i)
            if ok:
                pending_signal_idx = i

    return trades_data


def print_analysis(df: pd.DataFrame, label: str):
    """Print detailed momentum analysis for a set of trades."""
    if df.empty:
        print(f"\n{label}: No trades")
        return

    total = len(df)
    winners = df[df["winner"] == 1]
    losers = df[df["winner"] == 0]

    print(f"\n{'=' * 100}")
    print(f"{label}")
    print(f"{'=' * 100}")
    print(f"  Total trades: {total} | Winners: {len(winners)} ({len(winners)/total*100:.1f}%) | "
          f"Losers: {len(losers)} ({len(losers)/total*100:.1f}%)")
    print(f"  Avg PnL: {df['pnl'].mean():+.2f}% | Med PnL: {df['pnl'].median():+.2f}%")

    # ── Table 1: Winner vs Loser momentum comparison ────────────────────
    print(f"\n--- Winner vs Loser: Post-Entry PSZ_v Momentum ---")
    metrics = ["c1_avg_abs", "c1_peak_abs", "c2_avg_abs", "c2_peak_abs",
               "decay_ratio", "avg_hc_width", "avg_hc_early", "avg_hc_late", "n_half_cycles"]
    rows = []
    for m in metrics:
        w_vals = winners[m].dropna()
        l_vals = losers[m].dropna()
        rows.append({
            "Metric": m,
            "Winners (mean)": f"{w_vals.mean():.4f}" if not w_vals.empty else "—",
            "Winners (med)": f"{w_vals.median():.4f}" if not w_vals.empty else "—",
            "Losers (mean)": f"{l_vals.mean():.4f}" if not l_vals.empty else "—",
            "Losers (med)": f"{l_vals.median():.4f}" if not l_vals.empty else "—",
            "Gap": f"{w_vals.mean() - l_vals.mean():.4f}" if not w_vals.empty and not l_vals.empty else "—",
        })
    print(tabulate(rows, headers="keys", tablefmt="simple", showindex=False))

    # ── Table 2: Bar-by-bar abs(psz_v) comparison ───────────────────────
    print(f"\n--- Bar-by-Bar avg abs(psz_v): Winners vs Losers ---")
    bar_rows = []
    for b in range(1, 11):
        col = f"abs_psz_v_bar{b}"
        if col not in df.columns:
            continue
        w_vals = winners[col].dropna()
        l_vals = losers[col].dropna()
        bar_rows.append({
            "Bar": f"+{b}",
            "Winners": f"{w_vals.mean():.5f}" if not w_vals.empty else "—",
            "Losers": f"{l_vals.mean():.5f}" if not l_vals.empty else "—",
            "W count": len(w_vals),
            "L count": len(l_vals),
        })
    print(tabulate(bar_rows, headers="keys", tablefmt="simple", showindex=False))

    # ── Table 3: Correlation with PnL ───────────────────────────────────
    print(f"\n--- Correlation with PnL ---")
    corr_metrics = ["c1_avg_abs", "c1_peak_abs", "c2_avg_abs", "decay_ratio",
                    "avg_hc_width", "signal_psz_v", "n_half_cycles"]
    corr_rows = []
    for m in corr_metrics:
        valid = df[[m, "pnl"]].dropna()
        if len(valid) > 5:
            r = valid[m].corr(valid["pnl"])
            corr_rows.append({"Feature": m, "Pearson r": f"{r:+.3f}", "N": len(valid)})
    print(tabulate(corr_rows, headers="keys", tablefmt="simple", showindex=False))

    # ── Table 4: Threshold sweep for c1_avg_abs ─────────────────────────
    print(f"\n--- Threshold Sweep: c1_avg_abs (Cycle 1 avg amplitude) ---")
    thresholds = [0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05]
    sweep_rows = []
    for t in thresholds:
        above = df[df["c1_avg_abs"] >= t]
        below = df[df["c1_avg_abs"] < t]
        sweep_rows.append({
            "Threshold": f">= {t:.3f}",
            "N (above)": len(above),
            "Win% (above)": f"{above['winner'].mean()*100:.1f}" if not above.empty else "—",
            "Avg PnL (above)": f"{above['pnl'].mean():+.2f}" if not above.empty else "—",
            "N (below)": len(below),
            "Win% (below)": f"{below['winner'].mean()*100:.1f}" if not below.empty else "—",
            "Avg PnL (below)": f"{below['pnl'].mean():+.2f}" if not below.empty else "—",
        })
    print(tabulate(sweep_rows, headers="keys", tablefmt="simple", showindex=False))

    # ── Table 5: Threshold sweep for decay_ratio ────────────────────────
    valid_decay = df[df["decay_ratio"].notna()]
    if not valid_decay.empty:
        print(f"\n--- Threshold Sweep: decay_ratio (Cycle 2 / Cycle 1) ---")
        d_thresholds = [0.3, 0.5, 0.7, 1.0, 1.5, 2.0]
        d_rows = []
        for t in d_thresholds:
            above = valid_decay[valid_decay["decay_ratio"] >= t]
            below = valid_decay[valid_decay["decay_ratio"] < t]
            d_rows.append({
                "Threshold": f">= {t:.1f}",
                "N (above)": len(above),
                "Win% (above)": f"{above['winner'].mean()*100:.1f}" if not above.empty else "—",
                "Avg PnL (above)": f"{above['pnl'].mean():+.2f}" if not above.empty else "—",
                "N (below)": len(below),
                "Win% (below)": f"{below['winner'].mean()*100:.1f}" if not below.empty else "—",
                "Avg PnL (below)": f"{below['pnl'].mean():+.2f}" if not below.empty else "—",
            })
        print(tabulate(d_rows, headers="keys", tablefmt="simple", showindex=False))

    # ── Table 6: Half-cycle width analysis ──────────────────────────────
    print(f"\n--- Half-Cycle Width: Winners vs Losers ---")
    hc_rows = []
    for label_hc, grp in [("Winners", winners), ("Losers", losers)]:
        if grp.empty:
            continue
        hc_rows.append({
            "Group": label_hc,
            "Avg HC Width": f"{grp['avg_hc_width'].mean():.2f}",
            "Avg Early HC": f"{grp['avg_hc_early'].mean():.2f}",
            "Avg Late HC": f"{grp['avg_hc_late'].dropna().mean():.2f}" if not grp['avg_hc_late'].dropna().empty else "—",
            "Avg N HCs": f"{grp['n_half_cycles'].mean():.1f}",
        })
    print(tabulate(hc_rows, headers="keys", tablefmt="simple", showindex=False))

    # ── Table 7: Detailed trade log (sorted by c1_avg_abs) ─────────────
    print(f"\n--- Trade Log (sorted by Cycle 1 amplitude) ---")
    log_cols = ["symbol", "entry_date", "exit_date", "pnl", "duration", "regime",
                "c1_avg_abs", "c2_avg_abs", "decay_ratio", "avg_hc_width", "exit_reason"]
    log = df[log_cols].copy().sort_values("c1_avg_abs")
    log.columns = ["Symbol", "Entry", "Exit", "PnL%", "Bars", "Regime",
                   "C1 Avg", "C2 Avg", "Decay", "HC Width", "Exit"]
    print(tabulate(log, headers="keys", tablefmt="simple", floatfmt=".4f", showindex=False))


def main():
    parser = argparse.ArgumentParser(description="PSZ_v post-entry momentum decay study")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--watchlist", help="Watchlist name")
    group.add_argument("--symbol", help="Single symbol")
    args = parser.parse_args()

    if args.symbol:
        symbols = [args.symbol]
    else:
        symbols = get_watchlist_symbols(args.watchlist)

    signal = SignalFactory.get_signal("savgol_cts")
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()

    print(f"PSZ_v Momentum Decay Study")
    print(f"Symbols: {len(symbols)}")
    print(f"Entry config: {entry_cfg}")
    print(f"Exit config: {exit_cfg}")

    all_trades = []
    for i, sym in enumerate(symbols, 1):
        print(f"  [{i}/{len(symbols)}] {sym}...", end=" ", flush=True)
        trades = simulate_and_extract(sym, signal, entry_cfg, exit_cfg)
        print(f"{len(trades)} trades")
        all_trades.extend(trades)

    if not all_trades:
        print("\nNo trades generated.")
        return

    df = pd.DataFrame(all_trades)

    # Overall analysis
    print_analysis(df, "ALL TRADES — FULL PERIOD")

    # Regime 1: 2020–2023 (trending bull)
    r1 = df[(df["entry_date"] >= "2020-01-01") & (df["entry_date"] <= "2023-12-31")]
    print_analysis(r1, "REGIME 1 — TRENDING BULL (2020–2023)")

    # Regime 2: 2024–present (choppy/volatile)
    r2 = df[df["entry_date"] >= "2024-01-01"]
    print_analysis(r2, "REGIME 2 — CHOPPY/VOLATILE (2024–present)")

    # Save raw data
    out_path = Path(__file__).resolve().parent.parent / "output" / "psz_v_momentum_study.csv"
    out_path.parent.mkdir(exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"\nRaw data saved to {out_path}")


if __name__ == "__main__":
    main()
