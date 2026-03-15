#!/usr/bin/env python3
"""
test_dual_ema_assister.py — Dual-EMA Assister Signal Experiment
───────────────────────────────────────────────────────────────────
Compare current assister method (cei_raw crosses cei EMA14) against
proposed dual-EMA method (EMA5 crosses EMA14) for assister signals.

Runs the engine, then overlays both assister methods on the same
ledger and compares forward returns + signal counts.

Usage:
    python scripts/test_dual_ema_assister.py RELIANCE --start 2024-01-01
    python scripts/test_dual_ema_assister.py --watchlist "NIFTY 50" --start 2025-01-01
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import sqlite3
from src.divergence_engine.engine import DivergenceEngine
from src.database import DB_PATH


# ─────────────────────────────────────────────────────────────────────────────
# Dual-EMA Assister Logic
# ─────────────────────────────────────────────────────────────────────────────

def generate_dual_ema_assisters(
    cei_raw: np.ndarray,
    cei_ema14: np.ndarray,
    close: np.ndarray,
    cwvap: np.ndarray,
    primary_signals: list[str | None],
    fast_span: int = 5,
    cooldown_bars: int = 5,
) -> list[str | None]:
    """Generate assister signals using EMA(fast) crossing EMA(slow=14).

    Returns a new signal list with primary signals preserved and
    dual-EMA assisters overlaid.
    """
    n = len(cei_raw)

    # Compute fast EMA from cei_raw
    cei_fast = pd.Series(cei_raw).ewm(span=fast_span, adjust=False).mean().values

    signals = list(primary_signals)  # copy

    last_demand_bar = -cooldown_bars - 1
    last_supply_bar = -cooldown_bars - 1

    for i in range(1, n):
        # Primary signals reset cooldown
        if signals[i] == "Demand":
            last_demand_bar = i
            continue
        elif signals[i] == "Supply":
            last_supply_bar = i
            continue
        if signals[i] is not None:
            continue

        fast_now = cei_fast[i]
        fast_prev = cei_fast[i - 1]
        slow_now = cei_ema14[i]
        slow_prev = cei_ema14[i - 1]

        if np.isnan(fast_now) or np.isnan(fast_prev) or np.isnan(slow_now) or np.isnan(slow_prev):
            continue

        # Demand assister: fast EMA crosses above slow EMA
        if fast_prev <= slow_prev and fast_now > slow_now and (i - last_demand_bar > cooldown_bars):
            signals[i] = "Demand_Assister"
            last_demand_bar = i

        # Supply assister: fast EMA crosses below slow EMA
        elif fast_prev >= slow_prev and fast_now < slow_now and (i - last_supply_bar > cooldown_bars):
            signals[i] = "Supply_Assister"
            last_supply_bar = i

    return signals


def generate_current_assisters(
    cei_raw: np.ndarray,
    cei_ema14: np.ndarray,
    close: np.ndarray,
    cwvap: np.ndarray,
    primary_signals: list[str | None],
    cooldown_bars: int = 5,
) -> list[str | None]:
    """Replicate current assister logic (cei_raw crosses cei EMA) with cooldown fix."""
    n = len(cei_raw)
    signals = list(primary_signals)  # copy

    last_demand_bar = -cooldown_bars - 1
    last_supply_bar = -cooldown_bars - 1

    for i in range(1, n):
        if signals[i] == "Demand":
            last_demand_bar = i
            continue
        elif signals[i] == "Supply":
            last_supply_bar = i
            continue
        if signals[i] is not None:
            continue

        raw_now = cei_raw[i]
        raw_prev = cei_raw[i - 1]
        ema_now = cei_ema14[i]
        ema_prev = cei_ema14[i - 1]

        if np.isnan(raw_now) or np.isnan(raw_prev) or np.isnan(ema_now) or np.isnan(ema_prev):
            continue

        if raw_prev <= ema_prev and raw_now > ema_now and (i - last_demand_bar > cooldown_bars):
            signals[i] = "Demand_Assister"
            last_demand_bar = i
        elif raw_prev >= ema_prev and raw_now < ema_now and (i - last_supply_bar > cooldown_bars):
            signals[i] = "Supply_Assister"
            last_supply_bar = i

    return signals


# ─────────────────────────────────────────────────────────────────────────────
# Forward Return Evaluation
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_signals(
    ledger: pd.DataFrame,
    signals: list[str | None],
    label: str,
    horizons: list[int] = [3, 5, 10],
) -> dict:
    """Compute forward returns for assister signals."""
    close = ledger["close"].values.astype(float)
    n = len(close)

    results = {
        "label": label,
        "demand_assister_count": 0,
        "supply_assister_count": 0,
    }

    for h in horizons:
        results[f"demand_{h}d_returns"] = []
        results[f"supply_{h}d_returns"] = []

    for i in range(n):
        sig = signals[i]
        if sig not in ("Demand_Assister", "Supply_Assister"):
            continue

        is_demand = sig == "Demand_Assister"
        if is_demand:
            results["demand_assister_count"] += 1
        else:
            results["supply_assister_count"] += 1

        for h in horizons:
            if i + h >= n:
                continue
            fwd_ret = (close[i + h] - close[i]) / close[i] * 100.0
            # Supply returns flipped (positive = signal was correct)
            if not is_demand:
                fwd_ret = -fwd_ret
            key = f"{'demand' if is_demand else 'supply'}_{h}d_returns"
            results[key].append(fwd_ret)

    return results


def summarize_results(results: dict, horizons: list[int] = [3, 5, 10]) -> dict:
    """Summarize forward return results into hit rates and means."""
    summary = {
        "label": results["label"],
        "demand_count": results["demand_assister_count"],
        "supply_count": results["supply_assister_count"],
        "total_count": results["demand_assister_count"] + results["supply_assister_count"],
    }

    for direction in ["demand", "supply"]:
        for h in horizons:
            rets = results[f"{direction}_{h}d_returns"]
            if rets:
                summary[f"{direction}_{h}d_hit"] = sum(1 for r in rets if r > 0) / len(rets) * 100
                summary[f"{direction}_{h}d_avg"] = np.mean(rets)
                summary[f"{direction}_{h}d_n"] = len(rets)
            else:
                summary[f"{direction}_{h}d_hit"] = 0.0
                summary[f"{direction}_{h}d_avg"] = 0.0
                summary[f"{direction}_{h}d_n"] = 0

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def run_comparison(symbol: str, start_date: str, end_date: str | None = None,
                   fast_span: int = 5) -> tuple[dict, dict]:
    """Run engine and compare both assister methods."""
    print(f"\n  {symbol}: running engine...", end="", flush=True)

    engine = DivergenceEngine(ticker=symbol)
    result = engine.run()
    ledger = result.ledger.copy()

    # Filter to date range
    ledger["_date_str"] = ledger["date"].astype(str).str[:10]
    mask = ledger["_date_str"] >= start_date
    if end_date:
        mask &= ledger["_date_str"] <= end_date
    ledger = ledger[mask].reset_index(drop=True)

    if len(ledger) < 20:
        print(f" skipped (only {len(ledger)} bars)")
        return {}, {}

    cei_raw = ledger["cei_raw"].values.astype(float)
    cei_ema = ledger["cei"].values.astype(float)
    close = ledger["close"].values.astype(float)
    cwvap = ledger["cwvap"].values.astype(float) if "cwvap" in ledger.columns else np.full(len(ledger), np.nan)

    # Extract primary-only signals (strip existing assisters)
    primary_signals: list[str | None] = []
    for sig in ledger["cei_signal"].tolist():
        if sig in ("Demand", "Supply"):
            primary_signals.append(sig)
        else:
            primary_signals.append(None)

    # Method A: Current (cei_raw crosses EMA14) — with cooldown fix
    signals_current = generate_current_assisters(
        cei_raw, cei_ema, close, cwvap, primary_signals, cooldown_bars=5,
    )
    results_current = evaluate_signals(ledger, signals_current, "Current (raw×EMA14)")
    summary_current = summarize_results(results_current)

    # Method B: Dual-EMA (EMA5 crosses EMA14)
    signals_dual = generate_dual_ema_assisters(
        cei_raw, cei_ema, close, cwvap, primary_signals,
        fast_span=fast_span, cooldown_bars=5,
    )
    results_dual = evaluate_signals(ledger, signals_dual, f"Dual-EMA (EMA{fast_span}×EMA14)")
    summary_dual = summarize_results(results_dual)

    d_count = summary_current["demand_count"]
    s_count = summary_current["supply_count"]
    d_count2 = summary_dual["demand_count"]
    s_count2 = summary_dual["supply_count"]
    print(f" {len(ledger)} bars | Current: {d_count}D/{s_count}S | Dual-EMA: {d_count2}D/{s_count2}S")

    return summary_current, summary_dual


def print_comparison_table(agg_current: dict, agg_dual: dict, horizons=[3, 5, 10]):
    """Print side-by-side comparison table."""
    w = 90
    print(f"\n{'=' * w}")
    print(f"  ASSISTER METHOD COMPARISON")
    print(f"{'=' * w}")

    print(f"\n  {'Method':<30s}  {'Signals':>8s}  ", end="")
    for h in horizons:
        print(f"{'D ' + str(h) + 'd Hit':>10s}  {'S ' + str(h) + 'd Hit':>10s}  ", end="")
    print()
    print(f"  {'-' * 28}  {'--------':>8s}  ", end="")
    for _ in horizons:
        print(f"{'----------':>10s}  {'----------':>10s}  ", end="")
    print()

    for agg in [agg_current, agg_dual]:
        label = agg["label"]
        total = agg["total_count"]
        print(f"  {label:<30s}  {total:>8d}  ", end="")
        for h in horizons:
            d_hit = agg.get(f"demand_{h}d_hit", 0)
            s_hit = agg.get(f"supply_{h}d_hit", 0)
            d_n = agg.get(f"demand_{h}d_n", 0)
            s_n = agg.get(f"supply_{h}d_n", 0)
            print(f"{d_hit:>7.1f}%({d_n:>3d})  {s_hit:>7.1f}%({s_n:>3d})  ", end="")
        print()

    # Average returns row
    print()
    print(f"  {'Avg Return':<30s}  {'':>8s}  ", end="")
    for _ in horizons:
        print(f"{'D Avg Ret':>10s}  {'S Avg Ret':>10s}  ", end="")
    print()
    for agg in [agg_current, agg_dual]:
        label = agg["label"]
        print(f"  {label:<30s}  {'':>8s}  ", end="")
        for h in horizons:
            d_avg = agg.get(f"demand_{h}d_avg", 0)
            s_avg = agg.get(f"supply_{h}d_avg", 0)
            print(f"{d_avg:>+8.2f}%     {s_avg:>+8.2f}%     ", end="")
        print()

    print(f"\n{'=' * w}")


def get_watchlist_symbols(name: str) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT i.symbol FROM watchlist_items i "
            "JOIN watchlists w ON i.watchlist_id = w.id "
            "WHERE w.name = ? ORDER BY i.display_order, i.symbol",
            (name,),
        )
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Dual-EMA Assister Experiment")
    parser.add_argument("symbol", nargs="?", help="NSE symbol")
    parser.add_argument("--watchlist", help="Process all symbols in watchlist")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date")
    parser.add_argument("--fast-ema", type=int, default=5, help="Fast EMA span (default: 5)")
    args = parser.parse_args()

    if not args.symbol and not args.watchlist:
        parser.error("Provide symbol or --watchlist")

    symbols = []
    if args.symbol:
        symbols.append(args.symbol.upper())
    if args.watchlist:
        wl = get_watchlist_symbols(args.watchlist)
        if not wl:
            print(f"  ERROR: Watchlist '{args.watchlist}' not found or empty.")
            sys.exit(1)
        symbols.extend(s for s in wl if s not in symbols)

    print(f"\n  Dual-EMA Assister Experiment — {len(symbols)} symbols, fast EMA={args.fast_ema}")

    # Aggregate across all symbols
    horizons = [3, 5, 10]
    agg_current = {"label": "Current (raw×EMA14)", "total_count": 0, "demand_count": 0, "supply_count": 0}
    agg_dual = {"label": f"Dual-EMA (EMA{args.fast_ema}×EMA14)", "total_count": 0, "demand_count": 0, "supply_count": 0}

    # Init aggregation keys
    for direction in ["demand", "supply"]:
        for h in horizons:
            for agg in [agg_current, agg_dual]:
                agg[f"{direction}_{h}d_returns"] = []

    errors = 0
    for symbol in symbols:
        try:
            sc, sd = run_comparison(symbol, args.start, args.end, fast_span=args.fast_ema)
            if not sc or not sd:
                continue

            for agg, s in [(agg_current, sc), (agg_dual, sd)]:
                agg["total_count"] += s.get("total_count", 0)
                agg["demand_count"] += s.get("demand_count", 0)
                agg["supply_count"] += s.get("supply_count", 0)

        except Exception as e:
            print(f"  {symbol}: ERROR — {e}")
            errors += 1

    # Compute aggregate hit rates from per-symbol summaries
    # We need the raw returns for proper aggregation — re-run with collection
    # Actually the summaries already have counts and hits, let's use weighted averages
    # Better approach: collect all returns across symbols

    print(f"\n  Processed {len(symbols) - errors}/{len(symbols)} symbols.")

    # Re-run to collect raw returns for proper aggregate stats
    print("\n  Computing aggregate statistics...")
    all_current_returns: dict[str, list] = {}
    all_dual_returns: dict[str, list] = {}
    for direction in ["demand", "supply"]:
        for h in horizons:
            all_current_returns[f"{direction}_{h}d"] = []
            all_dual_returns[f"{direction}_{h}d"] = []

    for symbol in symbols:
        try:
            engine = DivergenceEngine(ticker=symbol)
            result = engine.run()
            ledger = result.ledger.copy()
            ledger["_date_str"] = ledger["date"].astype(str).str[:10]
            mask = ledger["_date_str"] >= args.start
            if args.end:
                mask &= ledger["_date_str"] <= args.end
            ledger = ledger[mask].reset_index(drop=True)
            if len(ledger) < 20:
                continue

            cei_raw = ledger["cei_raw"].values.astype(float)
            cei_ema = ledger["cei"].values.astype(float)
            close = ledger["close"].values.astype(float)
            cwvap = ledger["cwvap"].values.astype(float) if "cwvap" in ledger.columns else np.full(len(ledger), np.nan)

            primary_signals: list[str | None] = [
                s if s in ("Demand", "Supply") else None
                for s in ledger["cei_signal"].tolist()
            ]

            for method, gen_fn, ret_dict in [
                ("current", lambda: generate_current_assisters(cei_raw, cei_ema, close, cwvap, primary_signals, 5), all_current_returns),
                ("dual", lambda: generate_dual_ema_assisters(cei_raw, cei_ema, close, cwvap, primary_signals, args.fast_ema, 5), all_dual_returns),
            ]:
                sigs = gen_fn()
                for i in range(len(sigs)):
                    sig = sigs[i]
                    if sig not in ("Demand_Assister", "Supply_Assister"):
                        continue
                    is_demand = sig == "Demand_Assister"
                    direction = "demand" if is_demand else "supply"
                    for h in horizons:
                        if i + h >= len(close):
                            continue
                        fwd = (close[i + h] - close[i]) / close[i] * 100.0
                        if not is_demand:
                            fwd = -fwd
                        ret_dict[f"{direction}_{h}d"].append(fwd)

        except Exception:
            continue

    # Build final aggregates
    for agg, rets in [(agg_current, all_current_returns), (agg_dual, all_dual_returns)]:
        for direction in ["demand", "supply"]:
            for h in horizons:
                r = rets[f"{direction}_{h}d"]
                if r:
                    agg[f"{direction}_{h}d_hit"] = sum(1 for x in r if x > 0) / len(r) * 100
                    agg[f"{direction}_{h}d_avg"] = np.mean(r)
                    agg[f"{direction}_{h}d_n"] = len(r)
                else:
                    agg[f"{direction}_{h}d_hit"] = 0.0
                    agg[f"{direction}_{h}d_avg"] = 0.0
                    agg[f"{direction}_{h}d_n"] = 0

    print_comparison_table(agg_current, agg_dual, horizons)


if __name__ == "__main__":
    main()
