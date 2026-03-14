#!/usr/bin/env python3
"""
validate_cei_crossing.py — Broader validation of proposed CEI marker redesign.

Simulates the proposed `cei_raw crosses cei (EMA)` marker logic on a broad
universe (default: Nifty 500 watchlist) and produces a JSON report with
hit rates by direction, zone, volume tier, and gap quartile.

Usage:
    venv/bin/python3 scripts/validate_cei_crossing.py                     # Nifty 500 daily
    venv/bin/python3 scripts/validate_cei_crossing.py --agg weekly        # weekly bars
    venv/bin/python3 scripts/validate_cei_crossing.py --watchlist "NIFTY 50"
    venv/bin/python3 scripts/validate_cei_crossing.py --limit 50          # quick test
    venv/bin/python3 scripts/validate_cei_crossing.py -o results.json     # custom output

Output: JSON file at scripts/cei_crossing_validation.json (default)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import sqlite3
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.database import get_db_connection, DB_PATH
from src.divergence_engine.engine import DivergenceEngine

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HORIZONS = [3, 5, 10]
MIN_CALENDAR_DAYS = 365
MAX_DAILY_PRICE_CHANGE_PCT = 50.0

# Proposed crossing params
COOLDOWN_BARS = 5
MIN_GAP = 0.02

TIER_BOUNDARIES = {"Large": 50, "Mid": 10, "Small": 2, "Micro": 0}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _eligible_symbols(conn, min_days):
    cursor = conn.execute("""
        SELECT symbol FROM nse_delivery_log
        WHERE COALESCE(instrument_type, 'STOCK') = 'STOCK'
        GROUP BY symbol
        HAVING julianday(MAX(record_date)) - julianday(MIN(record_date)) >= ?
        ORDER BY symbol
    """, (min_days,))
    return [r[0] for r in cursor.fetchall()]


def _watchlist_symbols(conn, name):
    rows = conn.execute("""
        SELECT wi.symbol FROM watchlist_items wi
        JOIN watchlists w ON w.id = wi.watchlist_id
        WHERE UPPER(w.name) = UPPER(?)
        ORDER BY wi.symbol
    """, (name,)).fetchall()
    return [r[0] for r in rows]


def _check_quality_gate(conn, symbol):
    row = conn.execute("""
        SELECT 1 FROM nse_delivery_log
        WHERE symbol = ? AND ABS(price_change_pct) > ?
        LIMIT 1
    """, (symbol, MAX_DAILY_PRICE_CHANGE_PCT)).fetchone()
    return row is None


def _classify_tier(avg_del_val_cr):
    if avg_del_val_cr >= TIER_BOUNDARIES["Large"]:
        return "Large"
    elif avg_del_val_cr >= TIER_BOUNDARIES["Mid"]:
        return "Mid"
    elif avg_del_val_cr >= TIER_BOUNDARIES["Small"]:
        return "Small"
    return "Micro"


# ---------------------------------------------------------------------------
# Proposed crossing logic (simulation — does NOT modify cei.py)
# ---------------------------------------------------------------------------

def _simulate_crossing_signals(ledger: pd.DataFrame) -> list[dict]:
    """Simulate cei_raw crosses cei (EMA) with zone classification."""
    if "cei_raw" not in ledger.columns or "cei" not in ledger.columns:
        return []

    cei_raw = ledger["cei_raw"].values.astype(float)
    cei_ema = ledger["cei"].values.astype(float)
    close = ledger["close"].values.astype(float)
    high = ledger["high"].values.astype(float)
    low = ledger["low"].values.astype(float)
    cwvap = ledger["cwvap"].values.astype(float) if "cwvap" in ledger.columns else np.full(len(ledger), np.nan)
    va_high = ledger["va_high"].values.astype(float) if "va_high" in ledger.columns else np.full(len(ledger), np.nan)
    va_low = ledger["va_low"].values.astype(float) if "va_low" in ledger.columns else np.full(len(ledger), np.nan)

    n = len(ledger)
    signals = []
    last_demand_bar = -COOLDOWN_BARS - 1
    last_supply_bar = -COOLDOWN_BARS - 1

    for i in range(1, n):
        raw_now = cei_raw[i]
        raw_prev = cei_raw[i - 1]
        ema_now = cei_ema[i]
        ema_prev = cei_ema[i - 1]

        if np.isnan(raw_now) or np.isnan(raw_prev) or np.isnan(ema_now) or np.isnan(ema_prev):
            continue

        gap = abs(raw_now - ema_now)

        # Demand: cei_raw crosses above cei (EMA)
        if raw_prev <= ema_prev and raw_now > ema_now and gap >= MIN_GAP:
            if i - last_demand_bar > COOLDOWN_BARS:
                zone = _classify_zone("Demand", close[i], va_high[i], va_low[i], cwvap[i])
                signals.append(_build_signal(
                    i, "Demand", zone, gap, close, high, low, cwvap[i], n,
                ))
                last_demand_bar = i

        # Supply: cei_raw crosses below cei (EMA)
        elif raw_prev >= ema_prev and raw_now < ema_now and gap >= MIN_GAP:
            if i - last_supply_bar > COOLDOWN_BARS:
                zone = _classify_zone("Supply", close[i], va_high[i], va_low[i], cwvap[i])
                signals.append(_build_signal(
                    i, "Supply", zone, gap, close, high, low, cwvap[i], n,
                ))
                last_supply_bar = i

    return signals


def _classify_zone(direction: str, c: float, vah: float, val: float, w: float) -> str:
    """Zone classification per handoff.md rules."""
    if np.isnan(vah) or np.isnan(val) or np.isnan(w):
        return "full"  # can't classify → assume full

    if c > vah and c > w:
        return "assister"  # above VA + above CWVAP → always assister

    if c < val:
        return "full"  # below VA → always full

    # Inside VA zone
    if direction == "Supply" and c >= w:
        return "assister"  # Supply needs close < cwvap for full
    return "full"


def _build_signal(
    idx: int, direction: str, zone: str, gap: float,
    close: np.ndarray, high: np.ndarray, low: np.ndarray,
    cwvap_val: float, n: int,
) -> dict:
    entry = close[idx]
    is_demand = direction == "Demand"
    sig = {
        "_idx": idx,
        "direction": direction,
        "zone": zone,
        "gap": round(gap, 4),
        "entry_close": entry,
        "cwvap_dist": round((entry / cwvap_val - 1) * 100, 2) if not np.isnan(cwvap_val) and cwvap_val > 0 else None,
    }

    for h in HORIZONS:
        end_idx = min(idx + h, n - 1)
        if idx >= n - 1:
            sig[f"ret_{h}d"] = None
            sig[f"hit_{h}d"] = None
            continue

        fwd_close = close[end_idx]
        ret = ((fwd_close / entry) - 1) * 100
        if not is_demand:
            ret = -ret
        sig[f"ret_{h}d"] = round(ret, 4)
        sig[f"hit_{h}d"] = 1 if ret > 0 else 0

        fwd_highs = high[idx + 1: end_idx + 1]
        fwd_lows = low[idx + 1: end_idx + 1]
        if len(fwd_highs) > 0:
            if is_demand:
                sig[f"mfe_{h}d"] = round(((fwd_highs.max() / entry) - 1) * 100, 4)
                sig[f"mae_{h}d"] = round(((fwd_lows.min() / entry) - 1) * 100, 4)
            else:
                sig[f"mfe_{h}d"] = round(((entry - fwd_lows.min()) / entry) * 100, 4)
                sig[f"mae_{h}d"] = round(((fwd_highs.max() - entry) / entry) * 100, 4)

    return sig


# ---------------------------------------------------------------------------
# Also simulate CURRENT method (zero-crossing) for comparison
# ---------------------------------------------------------------------------

def _simulate_zero_crossing_signals(ledger: pd.DataFrame) -> list[dict]:
    """Simulate current zero-crossing logic for A/B comparison."""
    if "cei" not in ledger.columns:
        return []

    cei_ema = ledger["cei"].values.astype(float)
    close = ledger["close"].values.astype(float)
    high = ledger["high"].values.astype(float)
    low = ledger["low"].values.astype(float)
    cwvap = ledger["cwvap"].values.astype(float) if "cwvap" in ledger.columns else np.full(len(ledger), np.nan)

    n = len(ledger)
    signals = []
    last_demand_bar = -COOLDOWN_BARS - 1
    last_supply_bar = -COOLDOWN_BARS - 1

    for i in range(1, n):
        now = cei_ema[i]
        prev = cei_ema[i - 1]
        if np.isnan(now) or np.isnan(prev):
            continue

        # Demand: crosses above zero
        if prev <= 0 < now and (i - last_demand_bar > COOLDOWN_BARS):
            sig = _build_signal(i, "Demand", "n/a", 0.0, close, high, low, cwvap[i], n)
            signals.append(sig)
            last_demand_bar = i

        # Supply: crosses below zero (with CWVAP gate)
        elif prev >= 0 > now and (i - last_supply_bar > COOLDOWN_BARS):
            c, w = close[i], cwvap[i]
            if not np.isnan(c) and not np.isnan(w) and c < w:
                sig = _build_signal(i, "Supply", "n/a", 0.0, close, high, low, cwvap[i], n)
                signals.append(sig)
                last_supply_bar = i

    return signals


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _aggregate(signals: list[dict]) -> dict:
    """Aggregate hit rates from a list of signal dicts."""
    if not signals:
        return {"n": 0}
    result = {"n": len(signals)}
    for h in HORIZONS:
        hits = [s[f"hit_{h}d"] for s in signals if s.get(f"hit_{h}d") is not None]
        rets = [s[f"ret_{h}d"] for s in signals if s.get(f"ret_{h}d") is not None]
        mfes = [s[f"mfe_{h}d"] for s in signals if s.get(f"mfe_{h}d") is not None]
        maes = [s[f"mae_{h}d"] for s in signals if s.get(f"mae_{h}d") is not None]
        if hits:
            result[f"hit_{h}d"] = round(sum(hits) / len(hits) * 100, 2)
            result[f"n_{h}d"] = len(hits)
        if rets:
            result[f"avg_ret_{h}d"] = round(sum(rets) / len(rets), 3)
        if mfes:
            result[f"avg_mfe_{h}d"] = round(sum(mfes) / len(mfes), 3)
        if maes:
            result[f"avg_mae_{h}d"] = round(sum(maes) / len(maes), 3)
    return result


def _build_report(all_signals: list[dict], method_label: str) -> dict:
    """Build full report structure from a flat list of signals."""
    # Filter to signals with complete forward data
    complete = [s for s in all_signals if all(s.get(f"hit_{h}d") is not None for h in HORIZONS)]

    report = {
        "method": method_label,
        "total_signals": len(complete),
        "overall": _aggregate(complete),
    }

    # By direction
    report["by_direction"] = {}
    for d in ["Demand", "Supply"]:
        subset = [s for s in complete if s["direction"] == d]
        report["by_direction"][d] = _aggregate(subset)

    # By zone (only for crossing method)
    if any(s.get("zone") not in (None, "n/a") for s in complete):
        report["by_zone"] = {}
        for d in ["Demand", "Supply"]:
            report["by_zone"][d] = {}
            for z in ["full", "assister"]:
                subset = [s for s in complete if s["direction"] == d and s.get("zone") == z]
                if subset:
                    report["by_zone"][d][z] = _aggregate(subset)

    # By volume tier
    report["by_tier"] = {}
    for tier in ["Large", "Mid", "Small", "Micro"]:
        subset = [s for s in complete if s.get("tier") == tier]
        if subset:
            report["by_tier"][tier] = _aggregate(subset)

    # By tier × direction
    report["by_tier_direction"] = {}
    for tier in ["Large", "Mid", "Small", "Micro"]:
        report["by_tier_direction"][tier] = {}
        for d in ["Demand", "Supply"]:
            subset = [s for s in complete if s.get("tier") == tier and s["direction"] == d]
            if subset:
                report["by_tier_direction"][tier][d] = _aggregate(subset)

    # Gap quartile analysis (crossing method only)
    gaps = [s["gap"] for s in complete if s.get("gap", 0) > 0]
    if gaps:
        q25, q50, q75 = np.percentile(gaps, [25, 50, 75])
        report["gap_quartiles"] = {
            "q25": round(q25, 4), "q50": round(q50, 4), "q75": round(q75, 4),
        }
        report["by_gap_quartile"] = {}
        for label, lo, hi in [("Q1", 0, q25), ("Q2", q25, q50), ("Q3", q50, q75), ("Q4", q75, 999)]:
            subset = [s for s in complete if lo <= s.get("gap", 0) < hi]
            if subset:
                report["by_gap_quartile"][label] = _aggregate(subset)

    # Supply zone breakdown (key validation from Nifty 50 investigation)
    report["supply_zone_detail"] = {}
    supply = [s for s in complete if s["direction"] == "Supply"]
    for label, predicate in [
        ("below_va_below_cwvap", lambda s: s.get("zone") == "full" and (s.get("cwvap_dist") or 0) < 0 and _is_below_va(s)),
        ("in_va_below_cwvap", lambda s: s.get("zone") == "full" and (s.get("cwvap_dist") or 0) < 0 and not _is_below_va(s)),
        ("in_va_above_cwvap", lambda s: s.get("zone") == "assister"),
        ("above_va_above_cwvap", lambda s: s.get("zone") == "assister" and (s.get("cwvap_dist") or 0) > 0),
    ]:
        subset = [s for s in supply if predicate(s)]
        if subset:
            report["supply_zone_detail"][label] = _aggregate(subset)

    return report


def _is_below_va(sig):
    """Heuristic: if cwvap_dist is very negative, likely below VA."""
    # We don't have va_low in the signal dict, so use zone == "full" + large negative cwvap_dist
    # This is approximate — full signals with very negative cwvap_dist are likely below VA
    return (sig.get("cwvap_dist") or 0) < -2.0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Validate CEI crossing marker redesign")
    parser.add_argument("--watchlist", type=str, default="NIFTY 500", help="Watchlist name (default: NIFTY 500)")
    parser.add_argument("--limit", type=int, default=0, help="Limit symbols (0=all)")
    parser.add_argument("--min-days", type=int, default=MIN_CALENDAR_DAYS)
    parser.add_argument("--agg", type=str, default="daily", choices=["daily", "weekly", "monthly"],
                        help="Aggregation mode (default: daily)")
    parser.add_argument("-o", "--output", type=str, default="", help="Output JSON path")
    args = parser.parse_args()

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    # Resolve symbols
    symbols = _watchlist_symbols(conn, args.watchlist)
    if not symbols:
        print(f"Watchlist '{args.watchlist}' not found. Falling back to all eligible symbols.")
        symbols = _eligible_symbols(conn, args.min_days)

    if args.limit > 0:
        symbols = symbols[:args.limit]

    agg_mode = args.agg
    default_filename = f"cei_crossing_validation_{agg_mode}.json"
    out_path = args.output or os.path.join(os.path.dirname(__file__), default_filename)

    print(f"CEI Crossing Validation")
    print(f"  Universe: {args.watchlist} ({len(symbols)} symbols)")
    print(f"  Aggregation: {agg_mode}")
    print(f"  Params: cooldown={COOLDOWN_BARS}, min_gap={MIN_GAP}")
    print(f"  Output: {out_path}")
    print()

    all_crossing = []
    all_zero_cross = []
    errors = []
    skipped = 0
    t_start = time.perf_counter()

    for i, sym in enumerate(symbols, 1):
        try:
            if not _check_quality_gate(conn, sym):
                skipped += 1
                _progress(i, len(symbols), sym, 0, 0, t_start, skip=True)
                continue

            engine = DivergenceEngine(ticker=sym, agg_mode=agg_mode)
            result = engine.run()
            ledger = result.ledger

            avg_del_val = float((ledger["delivery_qty"] * ledger["close"]).mean() / 1e7)
            tier = _classify_tier(avg_del_val)

            # Proposed method: cei_raw crosses cei
            crossing_sigs = _simulate_crossing_signals(ledger)
            for s in crossing_sigs:
                s["symbol"] = sym
                s["tier"] = tier
            all_crossing.extend(crossing_sigs)

            # Current method: zero-crossing (for comparison)
            zero_sigs = _simulate_zero_crossing_signals(ledger)
            for s in zero_sigs:
                s["symbol"] = sym
                s["tier"] = tier
            all_zero_cross.extend(zero_sigs)

            _progress(i, len(symbols), sym, len(crossing_sigs), len(zero_sigs), t_start)

        except Exception as e:
            errors.append({"symbol": sym, "error": str(e)})
            _progress(i, len(symbols), sym, 0, 0, t_start, error=str(e)[:40])

    elapsed = time.perf_counter() - t_start
    print(f"\n\nDone: {len(symbols)} symbols in {elapsed:.1f}s, {skipped} skipped, {len(errors)} errors")
    print(f"  Crossing signals: {len(all_crossing)}")
    print(f"  Zero-crossing signals: {len(all_zero_cross)}")

    # Build reports
    report = {
        "meta": {
            "universe": args.watchlist,
            "agg_mode": agg_mode,
            "n_symbols": len(symbols),
            "n_skipped": skipped,
            "n_errors": len(errors),
            "params": {
                "cooldown_bars": COOLDOWN_BARS,
                "min_gap": MIN_GAP,
                "horizons": HORIZONS,
            },
            "run_time_s": round(elapsed, 1),
        },
        "proposed_crossing": _build_report(all_crossing, "cei_raw crosses cei (EMA)"),
        "current_zero_crossing": _build_report(all_zero_cross, "cei zero-crossing (current)"),
        "errors": errors[:20],
    }

    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nReport written to {out_path}")

    # Print quick summary to console
    _print_quick_summary(report)


def _progress(i, total, sym, n_cross, n_zero, t_start, error=None, skip=False):
    elapsed = time.perf_counter() - t_start
    rate = i / elapsed if elapsed > 0 else 0
    eta = (total - i) / rate if rate > 0 else 0
    if error:
        status = f"ERR: {error}"
    elif skip:
        status = "SKIPPED (quality gate)"
    else:
        status = f"cross={n_cross} zero={n_zero}"
    print(f"\r  [{i}/{total}] {sym:<20s} {status:<40s} ({rate:.1f}/s, ETA {eta:.0f}s)", end="", flush=True)


def _print_quick_summary(report):
    print(f"\n{'=' * 72}")
    print(f"  QUICK SUMMARY")
    print(f"{'=' * 72}")
    for method_key, label in [("proposed_crossing", "PROPOSED (raw × EMA)"), ("current_zero_crossing", "CURRENT (zero-cross)")]:
        m = report[method_key]
        print(f"\n  {label}: {m['total_signals']} signals")
        for d in ["Demand", "Supply"]:
            bd = m.get("by_direction", {}).get(d, {})
            if bd.get("n", 0) > 0:
                print(f"    {d:<8s}: 5d hit={bd.get('hit_5d', 0):5.1f}%  10d hit={bd.get('hit_10d', 0):5.1f}%  avg_ret_5d={bd.get('avg_ret_5d', 0):+.3f}%  (n={bd['n']})")
        # Zone breakdown (proposed only)
        bz = m.get("by_zone", {})
        if bz:
            print(f"    --- Zone breakdown (5d) ---")
            for d in ["Demand", "Supply"]:
                for z in ["full", "assister"]:
                    zd = bz.get(d, {}).get(z, {})
                    if zd.get("n", 0) > 0:
                        print(f"      {d} {z:<10s}: hit={zd.get('hit_5d', 0):5.1f}%  (n={zd['n']})")


if __name__ == "__main__":
    main()
