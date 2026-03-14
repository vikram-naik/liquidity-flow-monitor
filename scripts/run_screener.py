#!/usr/bin/env python3
"""
run_screener.py — NIFTY 500 Stock Screener
──────────────────────────────────────────────────────────────────────────
Scans all NIFTY 500 symbols through the Divergence Engine and populates
two screener watchlists based on CEI signals:

    SCR: Long   — CEI Demand signal with intensity >= threshold
    SCR: Short  — CEI Supply signal with intensity >= threshold

Usage:
    python scripts/run_screener.py                  # full NIFTY 500 scan
    python scripts/run_screener.py --limit 20       # test with 20 symbols
    python scripts/run_screener.py --dry-run        # print results, don't write DB
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import sqlite3

# Ensure project root is on the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.database import get_db_connection
from src.divergence_engine.engine import DivergenceEngine

# ─────────────────────────────────────────────────────────────────────────────
# Screener Configuration
# ─────────────────────────────────────────────────────────────────────────────

CEI_INTENSITY_THRESHOLD = 2  # abs(cei_slope) × 1000 >= 2

LONG_WL_NAME = "SCR: Long"
SHORT_WL_NAME = "SCR: Short"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_nifty500_symbols(conn: sqlite3.Connection) -> list[str]:
    """Get NIFTY 500 symbols from the 'NIFTY 500' watchlist, or fall back
    to all unique symbols in nse_delivery_log."""
    cursor = conn.execute(
        "SELECT id FROM watchlists WHERE name = 'NIFTY 500'"
    )
    row = cursor.fetchone()
    if row:
        wl_id = row[0]
        cursor = conn.execute(
            "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY symbol",
            (wl_id,),
        )
        symbols = [r[0] for r in cursor.fetchall()]
        if symbols:
            return symbols

    # Fallback: all symbols with enough data (>= 60 bars)
    cursor = conn.execute(
        "SELECT symbol, COUNT(*) AS cnt FROM nse_delivery_log "
        "GROUP BY symbol HAVING cnt >= 60 ORDER BY symbol"
    )
    return [r[0] for r in cursor.fetchall()]


def _ensure_watchlist(conn: sqlite3.Connection, name: str) -> int:
    """Create watchlist if it doesn't exist, return its ID."""
    cursor = conn.execute("SELECT id FROM watchlists WHERE name = ?", (name,))
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor = conn.execute(
        "INSERT INTO watchlists (name, description) VALUES (?, ?)",
        (name, f"Auto-generated screener watchlist"),
    )
    conn.commit()
    return cursor.lastrowid


def _delete_insert_items(
    conn: sqlite3.Connection,
    wl_id: int,
    symbols: list[str],
) -> None:
    """Delete all existing items in the watchlist and insert new ones."""
    conn.execute("DELETE FROM watchlist_items WHERE watchlist_id = ?", (wl_id,))
    for i, sym in enumerate(symbols):
        conn.execute(
            "INSERT INTO watchlist_items (watchlist_id, symbol, display_order) "
            "VALUES (?, ?, ?)",
            (wl_id, sym, i + 1),
        )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NIFTY 500 Stock Screener")
    parser.add_argument("--limit", type=int, default=0, help="Limit symbols to scan (0 = all)")
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing to DB")
    parser.add_argument("--min-intensity", type=float, default=CEI_INTENSITY_THRESHOLD,
                        help="Minimum CEI intensity (abs(cei)×100)")
    args = parser.parse_args()

    conn = get_db_connection()
    symbols = _fetch_nifty500_symbols(conn)
    if args.limit > 0:
        symbols = symbols[: args.limit]

    print(f"Screening {len(symbols)} symbols (CEI intensity >= {args.min_intensity})...")
    print()

    long_hits: list[tuple[str, float, float]] = []   # (symbol, intensity, cei)
    short_hits: list[tuple[str, float, float]] = []
    errors: list[tuple[str, str]] = []

    t_start = time.perf_counter()

    for i, sym in enumerate(symbols, 1):
        try:
            engine = DivergenceEngine(ticker=sym)
            result = engine.run()
            latest = result.latest

            cei_signal = latest.get("cei_signal")
            cei_slope = latest.get("cei_slope") or 0.0
            intensity = abs(cei_slope) * 1000

            if cei_signal and intensity >= args.min_intensity:
                if cei_signal == "Demand":
                    long_hits.append((sym, intensity, cei_slope))
                elif cei_signal == "Supply":
                    short_hits.append((sym, intensity, cei_slope))

            # Progress indicator
            label = cei_signal or "—"
            elapsed = time.perf_counter() - t_start
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(symbols) - i) / rate if rate > 0 else 0
            print(
                f"\r  [{i}/{len(symbols)}] {sym:<20s} -> {label:<10s} "
                f"slope={cei_slope:+.5f}  ({rate:.1f} sym/s, ETA {eta:.0f}s)",
                end="", flush=True,
            )
        except Exception as e:
            errors.append((sym, str(e)))
            print(f"\r  [{i}/{len(symbols)}] {sym:<20s} -> ERROR: {e}", end="", flush=True)

    elapsed_total = time.perf_counter() - t_start
    print()
    print()

    # -- Results --
    print(f"{'=' * 70}")
    print(f"  SCREENER RESULTS  ({elapsed_total:.1f}s)")
    print(f"{'=' * 70}")

    # Sort by intensity descending
    long_hits.sort(key=lambda x: -x[1])
    short_hits.sort(key=lambda x: -x[1])

    print(f"\n  {LONG_WL_NAME} ({len(long_hits)} stocks)")
    print(f"  {'-' * 50}")
    for sym, intensity, slope in long_hits:
        print(f"    {sym:<20s} slope={slope:+.5f}  intensity={intensity:.1f}")

    print(f"\n  {SHORT_WL_NAME} ({len(short_hits)} stocks)")
    print(f"  {'-' * 50}")
    for sym, intensity, slope in short_hits:
        print(f"    {sym:<20s} slope={slope:+.5f}  intensity={intensity:.1f}")

    if errors:
        print(f"\n  Errors ({len(errors)})")
        print(f"  {'-' * 50}")
        for sym, err in errors:
            print(f"    {sym:<20s} {err}")

    # -- Write to DB --
    if args.dry_run:
        print(f"\n  [DRY RUN] No changes written to database.")
    else:
        long_wl_id = _ensure_watchlist(conn, LONG_WL_NAME)
        short_wl_id = _ensure_watchlist(conn, SHORT_WL_NAME)

        _delete_insert_items(conn, long_wl_id, [s[0] for s in long_hits])
        _delete_insert_items(conn, short_wl_id, [s[0] for s in short_hits])

        print(f"\n  Watchlists updated:")
        print(f"     {LONG_WL_NAME}: {len(long_hits)} symbols (wl_id={long_wl_id})")
        print(f"     {SHORT_WL_NAME}: {len(short_hits)} symbols (wl_id={short_wl_id})")

    conn.close()


if __name__ == "__main__":
    main()
