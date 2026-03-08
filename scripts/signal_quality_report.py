#!/usr/bin/env python3
"""
signal_quality_report.py — Signal Quality Analysis
----------------------------------------------------------------------
Runs the Divergence Engine on all eligible symbols, extracts every
Demand/Supply signal, computes forward returns (3, 5, 10 trading days),
MFE/MAE, and stores a feature snapshot per signal.

Produces:
  1. SQLite table `signal_quality` with one row per signal
  2. Console summary report with hit rates, feature distributions
     for hits vs misses, and candidate filter suggestions

Usage:
    python scripts/signal_quality_report.py                # full run
    python scripts/signal_quality_report.py --limit 20     # test with 20 symbols
    python scripts/signal_quality_report.py --min-days 500 # require 500 calendar days
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import csv

from src.database import get_db_connection, DB_PATH
from src.divergence_engine.engine import DivergenceEngine
from src.divergence_engine import config_manager

# Quality gate: max allowed single-day price change (absolute %).
# Stocks exceeding this are likely unadjusted for corporate actions.
MAX_DAILY_PRICE_CHANGE_PCT = 50.0

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HORIZONS = [3, 5, 10]
MIN_CALENDAR_DAYS = 365

FEATURE_COLS = [
    "cwc", "rdv", "rdv_consistency", "cwvap_dist", "delivery_pct",
    "pdd_30", "coherence", "price_slope_z", "rdv_slope_z",
    "mcs_composite", "atr_20", "conviction_score", "accum_score", "diverg_score",
    "price_distance_30", "velocity_30_norm", "cwc_slope",
    "psz_delta_3d", "psz_delta_5d", "rdv_sz_delta_3d", "rdv_sz_delta_5d",
]

REPORT_TABLE = "signal_quality"

# Delivery value tiers (avg daily delivery_qty * close)
# Boundaries in crores (1cr = 10M)
TIER_BOUNDARIES = {
    "Large": 50,    # >= 50cr avg daily delivery value
    "Mid": 10,      # >= 10cr
    "Small": 2,     # >= 2cr
    "Micro": 0,     # < 2cr
}


# ---------------------------------------------------------------------------
# DB Setup
# ---------------------------------------------------------------------------

def _init_signal_quality_table(conn: sqlite3.Connection) -> None:
    conn.execute(f"""
    CREATE TABLE IF NOT EXISTS {REPORT_TABLE} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        signal_date TEXT NOT NULL,
        signal_type TEXT NOT NULL,
        conviction_score REAL,
        entry_close REAL,
        avg_del_val REAL,
        volume_tier TEXT,
        ret_3d REAL, ret_5d REAL, ret_10d REAL,
        mfe_3d REAL, mfe_5d REAL, mfe_10d REAL,
        mae_3d REAL, mae_5d REAL, mae_10d REAL,
        hit_3d INTEGER, hit_5d INTEGER, hit_10d INTEGER,
        cwc REAL, rdv REAL, rdv_consistency INTEGER,
        cwvap_dist REAL, delivery_pct REAL, pdd_30 REAL,
        coherence REAL, price_slope_z REAL, rdv_slope_z REAL,
        mcs_composite REAL, atr_20 REAL,
        price_distance_30 REAL, velocity_30_norm REAL, cwc_slope REAL,
        accum_score REAL, diverg_score REAL,
        psz_delta_3d REAL, psz_delta_5d REAL,
        rdv_sz_delta_3d REAL, rdv_sz_delta_5d REAL,
        run_date TEXT NOT NULL,
        thresholds_hash TEXT NOT NULL,
        UNIQUE(symbol, signal_date, signal_type, thresholds_hash)
    );
    """)
    conn.execute(f"""
    CREATE INDEX IF NOT EXISTS idx_sq_symbol ON {REPORT_TABLE} (symbol);
    """)
    conn.execute(f"""
    CREATE INDEX IF NOT EXISTS idx_sq_run ON {REPORT_TABLE} (run_date);
    """)
    conn.execute(f"""
    CREATE INDEX IF NOT EXISTS idx_sq_tier ON {REPORT_TABLE} (volume_tier);
    """)
    # Migrate: add new columns to existing tables
    for col in ("accum_score", "diverg_score", "psz_delta_3d", "psz_delta_5d", "rdv_sz_delta_3d", "rdv_sz_delta_5d"):
        try:
            conn.execute(f"ALTER TABLE {REPORT_TABLE} ADD COLUMN {col} REAL")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_thresholds_hash(conn: sqlite3.Connection) -> str:
    config = config_manager.get_config(conn)
    th = config["thresholds"]
    raw = str(sorted(th.items()))
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _eligible_symbols(conn: sqlite3.Connection, min_days: int) -> list[str]:
    """Symbols with at least `min_days` calendar days of data. Excludes indices."""
    cursor = conn.execute("""
        SELECT symbol, MIN(record_date) AS first, MAX(record_date) AS last
        FROM nse_delivery_log
        WHERE COALESCE(instrument_type, 'STOCK') = 'STOCK'
        GROUP BY symbol
        HAVING julianday(last) - julianday(first) >= ?
        ORDER BY symbol
    """, (min_days,))
    return [row[0] for row in cursor.fetchall()]


def _check_quality_gate(conn: sqlite3.Connection, symbol: str) -> tuple[bool, str | None]:
    """Check if a symbol has suspicious price jumps indicating unadjusted corporate actions.
    Returns (passes, rejection_reason)."""
    cursor = conn.execute("""
        SELECT record_date, price_change_pct
        FROM nse_delivery_log
        WHERE symbol = ? AND ABS(price_change_pct) > ?
        ORDER BY ABS(price_change_pct) DESC
        LIMIT 1
    """, (symbol, MAX_DAILY_PRICE_CHANGE_PCT))
    row = cursor.fetchone()
    if row:
        return False, f"price_change_pct={row[1]:.1f}% on {row[0]} (threshold {MAX_DAILY_PRICE_CHANGE_PCT}%)"
    return True, None


def _classify_tier(avg_del_val_cr: float) -> str:
    """Classify symbol into volume tier based on avg daily delivery value in crores."""
    if avg_del_val_cr >= TIER_BOUNDARIES["Large"]:
        return "Large"
    elif avg_del_val_cr >= TIER_BOUNDARIES["Mid"]:
        return "Mid"
    elif avg_del_val_cr >= TIER_BOUNDARIES["Small"]:
        return "Small"
    return "Micro"


def _compute_avg_delivery_value(ledger: pd.DataFrame) -> float:
    """Compute average daily delivery value in crores."""
    del_value = ledger["delivery_qty"] * ledger["close"]
    return float(del_value.mean() / 1e7)  # 1cr = 1e7


def _extract_signals(ledger: pd.DataFrame, symbol: str) -> list[dict]:
    """Extract all Demand/Supply signal rows with feature snapshots."""
    signals = []
    mask = ledger["integrated_state"].isin(["Demand", "Supply"])
    signal_rows = ledger[mask]

    avg_del_val = _compute_avg_delivery_value(ledger)
    tier = _classify_tier(avg_del_val)

    for idx, row in signal_rows.iterrows():
        sig = {
            "symbol": symbol,
            "signal_date": str(row["date"])[:10],
            "signal_type": row["integrated_state"],
            "conviction_score": float(row["conviction_score"]) if pd.notna(row["conviction_score"]) else None,
            "entry_close": float(row["close"]),
            "avg_del_val": round(avg_del_val, 2),
            "volume_tier": tier,
            "_idx": idx,
        }
        for col in FEATURE_COLS:
            val = row.get(col)
            sig[col] = float(val) if pd.notna(val) else None
        signals.append(sig)

    return signals


def _compute_forward_metrics(
    ledger: pd.DataFrame,
    signals: list[dict],
) -> list[dict]:
    """Compute forward returns, MFE, MAE for each signal."""
    closes = ledger["close"].values
    highs = ledger["high"].values
    lows = ledger["low"].values
    n = len(closes)

    for sig in signals:
        idx = sig.pop("_idx")
        entry = sig["entry_close"]
        is_demand = sig["signal_type"] == "Demand"

        for h in HORIZONS:
            end_idx = min(idx + h, n - 1)
            if idx >= n - 1:
                sig[f"ret_{h}d"] = None
                sig[f"mfe_{h}d"] = None
                sig[f"mae_{h}d"] = None
                sig[f"hit_{h}d"] = None
                continue

            # Forward close return (directional: positive = signal was right)
            fwd_close = closes[end_idx]
            ret = ((fwd_close / entry) - 1) * 100
            if not is_demand:
                ret = -ret
            sig[f"ret_{h}d"] = round(ret, 4)

            # Slice of forward bars (exclusive of signal bar)
            fwd_highs = highs[idx + 1 : end_idx + 1]
            fwd_lows = lows[idx + 1 : end_idx + 1]

            if len(fwd_highs) == 0:
                sig[f"mfe_{h}d"] = None
                sig[f"mae_{h}d"] = None
                sig[f"hit_{h}d"] = None
                continue

            # ret is already directional (positive = signal correct)
            hit = 1 if ret > 0 else 0

            if is_demand:
                # Favorable = price going UP
                mfe = ((fwd_highs.max() / entry) - 1) * 100
                mae = ((fwd_lows.min() / entry) - 1) * 100  # negative = adverse
            else:
                # Supply: favorable = price going DOWN (mfe positive = good drop)
                mfe = ((entry - fwd_lows.min()) / entry) * 100
                mae = ((fwd_highs.max() - entry) / entry) * 100  # positive = adverse

            sig[f"mfe_{h}d"] = round(mfe, 4)
            sig[f"mae_{h}d"] = round(mae, 4)
            sig[f"hit_{h}d"] = hit

    return signals


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _print_report(conn: sqlite3.Connection, th_hash: str, run_date: str) -> None:
    df = pd.read_sql_query(
        f"SELECT * FROM {REPORT_TABLE} WHERE thresholds_hash = ? AND run_date = ?",
        conn, params=(th_hash, run_date),
    )
    if df.empty:
        print("No signals found.")
        return

    total = len(df)
    demand = df[df["signal_type"] == "Demand"]
    supply = df[df["signal_type"] == "Supply"]

    print(f"\n{'=' * 72}")
    print(f"  SIGNAL QUALITY REPORT")
    print(f"  Run: {run_date}  |  Thresholds: {th_hash}  |  Signals: {total}")
    print(f"{'=' * 72}")

    def _print_hit_table(label, subset):
        if subset.empty:
            return
        print(f"\n  --- {label} ({len(subset)} signals) ---")
        for h in HORIZONS:
            col_hit = f"hit_{h}d"
            col_ret = f"ret_{h}d"
            col_mfe = f"mfe_{h}d"
            col_mae = f"mae_{h}d"
            valid = subset[col_hit].dropna()
            if valid.empty:
                continue
            hit_rate = valid.mean() * 100
            avg_ret = subset[col_ret].dropna().mean()
            avg_mfe = subset[col_mfe].dropna().mean()
            avg_mae = subset[col_mae].dropna().mean()
            print(
                f"    {h:>2}d: hit={hit_rate:5.1f}%  avg_ret={avg_ret:+6.2f}%  "
                f"avg_mfe={avg_mfe:+6.2f}%  avg_mae={avg_mae:+6.2f}%  "
                f"(n={len(valid)})"
            )

    for label, subset in [("ALL", df), ("DEMAND", demand), ("SUPPLY", supply)]:
        _print_hit_table(label, subset)

    # Tier breakdown
    print(f"\n  --- BY VOLUME TIER (5d horizon) ---")
    for tier in ["Large", "Mid", "Small", "Micro"]:
        tier_df = df[df["volume_tier"] == tier]
        if tier_df.empty:
            continue
        valid = tier_df["hit_5d"].dropna()
        if valid.empty:
            continue
        hit_rate = valid.mean() * 100
        avg_ret = tier_df["ret_5d"].dropna().mean()
        n_sym = tier_df["symbol"].nunique()
        print(
            f"    {tier:<8s}: hit={hit_rate:5.1f}%  avg_ret={avg_ret:+6.2f}%  "
            f"(n={len(valid)}, {n_sym} symbols)"
        )

    # Feature comparison: hits vs misses at 5d horizon
    print(f"\n  --- FEATURE ANALYSIS (5d horizon, hits vs misses) ---")
    analysis_cols = [c for c in FEATURE_COLS if c in df.columns and c != "conviction_score"]
    valid_5d = df[df["hit_5d"].notna()].copy()
    if valid_5d.empty:
        print("    No valid 5d signals for feature analysis.")
        return

    hits = valid_5d[valid_5d["hit_5d"] == 1]
    misses = valid_5d[valid_5d["hit_5d"] == 0]

    if hits.empty or misses.empty:
        print("    Insufficient hit/miss split for comparison.")
        return

    print(f"    {'Feature':<22s} {'Hit med':>10s} {'Miss med':>10s} {'Effect':>8s}  Direction")
    print(f"    {'─' * 70}")

    effects = []
    for col in analysis_cols:
        h_vals = hits[col].dropna()
        m_vals = misses[col].dropna()
        if len(h_vals) < 5 or len(m_vals) < 5:
            continue
        h_med = h_vals.median()
        m_med = m_vals.median()
        pooled_std = valid_5d[col].dropna().std()
        if pooled_std > 0:
            effect = abs(h_med - m_med) / pooled_std
        else:
            effect = 0
        direction = "higher in hits" if h_med > m_med else "lower in hits"
        effects.append((col, h_med, m_med, effect, direction))

    effects.sort(key=lambda x: -x[3])
    for col, h_med, m_med, eff, direction in effects:
        marker = " ***" if eff >= 0.5 else " **" if eff >= 0.3 else ""
        print(f"    {col:<22s} {h_med:>10.4f} {m_med:>10.4f} {eff:>8.3f}  {direction}{marker}")

    # Candidate filters
    print(f"\n  --- CANDIDATE FILTER SUGGESTIONS ---")
    strong = [(c, h, m, e, d) for c, h, m, e, d in effects if e >= 0.3]
    if not strong:
        print("    No features with effect size >= 0.3. Current gates may be optimal.")
    else:
        for col, h_med, m_med, eff, direction in strong:
            print(f"    Consider: {col} ({direction}, effect={eff:.3f})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Signal Quality Report")
    parser.add_argument("--symbol", type=str, default="", help="Run for a single symbol")
    parser.add_argument("--limit", type=int, default=0, help="Limit symbols (0=all)")
    parser.add_argument("--min-days", type=int, default=MIN_CALENDAR_DAYS, help="Min calendar days of data")
    args = parser.parse_args()

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    _init_signal_quality_table(conn)

    th_hash = _get_thresholds_hash(conn)
    run_date = datetime.now().strftime("%Y-%m-%d")

    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = _eligible_symbols(conn, args.min_days)
        if args.limit > 0:
            symbols = symbols[:args.limit]

    print(f"Signal Quality Report")
    print(f"  Symbols: {len(symbols)}  |  Thresholds: {th_hash}  |  Date: {run_date}")
    print(f"  Horizons: {HORIZONS} trading days")
    print()

    # Flush cached signal quality API responses
    from src.cache import get_cache
    sq_cache = get_cache()
    flushed = sq_cache.delete_pattern("sq:*")
    print(f"  Flushed {flushed} cached signal quality responses")

    # Clear all previous runs — only keep data from the current run
    conn.execute(f"DELETE FROM {REPORT_TABLE}")
    conn.commit()

    total_signals = 0
    errors = []
    rejected = []  # (symbol, date, reason) for quality gate failures
    t_start = time.perf_counter()

    for i, sym in enumerate(symbols, 1):
        try:
            # Quality gate: skip stocks with suspicious price jumps
            passes, reason = _check_quality_gate(conn, sym)
            if not passes:
                rejected.append((sym, run_date, reason))
                _progress(i, len(symbols), sym, 0, t_start, error=f"REJECTED: {reason}")
                continue

            engine = DivergenceEngine(ticker=sym)
            result = engine.run()
            ledger = result.ledger

            signals = _extract_signals(ledger, sym)
            if not signals:
                _progress(i, len(symbols), sym, 0, t_start)
                continue

            signals = _compute_forward_metrics(ledger, signals)

            # Keep only signals with all three horizons fully computed
            signals = [
                s for s in signals
                if all(s.get(f"hit_{h}d") is not None for h in HORIZONS)
            ]
            if not signals:
                _progress(i, len(symbols), sym, 0, t_start)
                continue

            # Batch insert
            for sig in signals:
                sig["run_date"] = run_date
                sig["thresholds_hash"] = th_hash

            _insert_signals(conn, signals)
            total_signals += len(signals)
            _progress(i, len(symbols), sym, len(signals), t_start)

        except Exception as e:
            errors.append((sym, str(e)))
            _progress(i, len(symbols), sym, 0, t_start, error=str(e))

    elapsed = time.perf_counter() - t_start
    print(f"\n\n  Processed {len(symbols)} symbols in {elapsed:.1f}s  |  {total_signals} signals stored")

    # Write rejected stocks CSV
    if rejected:
        rejected_path = os.path.join(os.path.dirname(__file__), "rejected_stocks.csv")
        with open(rejected_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["symbol", "date", "reason"])
            writer.writerows(rejected)
        print(f"  Rejected {len(rejected)} symbols → {rejected_path}")

    if errors:
        print(f"  Errors: {len(errors)}")
        for sym, err in errors[:10]:
            print(f"    {sym}: {err}")

    _print_report(conn, th_hash, run_date)
    conn.close()


def _progress(i, total, sym, n_signals, t_start, error=None):
    elapsed = time.perf_counter() - t_start
    rate = i / elapsed if elapsed > 0 else 0
    eta = (total - i) / rate if rate > 0 else 0
    status = f"ERROR: {error}" if error else f"{n_signals} signals"
    print(
        f"\r  [{i}/{total}] {sym:<20s} {status:<30s} "
        f"({rate:.1f}/s, ETA {eta:.0f}s)",
        end="", flush=True,
    )


def _insert_signals(conn: sqlite3.Connection, signals: list[dict]) -> None:
    cols = [
        "symbol", "signal_date", "signal_type", "conviction_score", "entry_close",
        "avg_del_val", "volume_tier",
        "ret_3d", "ret_5d", "ret_10d",
        "mfe_3d", "mfe_5d", "mfe_10d",
        "mae_3d", "mae_5d", "mae_10d",
        "hit_3d", "hit_5d", "hit_10d",
        "cwc", "rdv", "rdv_consistency",
        "cwvap_dist", "delivery_pct", "pdd_30",
        "coherence", "price_slope_z", "rdv_slope_z",
        "mcs_composite", "atr_20", "accum_score", "diverg_score",
        "price_distance_30", "velocity_30_norm", "cwc_slope",
        "psz_delta_3d", "psz_delta_5d",
        "rdv_sz_delta_3d", "rdv_sz_delta_5d",
        "run_date", "thresholds_hash",
    ]
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    sql = f"INSERT OR REPLACE INTO {REPORT_TABLE} ({col_names}) VALUES ({placeholders})"

    rows = []
    for sig in signals:
        rows.append(tuple(sig.get(c) for c in cols))

    conn.executemany(sql, rows)
    conn.commit()


if __name__ == "__main__":
    main()
