#!/usr/bin/env python3
"""
Screener — Zero-Logic Orchestrator.

Scans the NIFTY 500 + CORE universe and delegates all signal detection to the
marker registry.  This script only handles:
  1. DB operations (init watchlists, insert matches, print summary)
  2. Iterating symbols and calling ``marker.screen()``

No functional/signal logic lives here — adding a new screener is as simple as
creating a new marker class with a ``screener_name`` in its ``metadata()``.
"""

import sqlite3
import sys
import os
import pandas as pd
from typing import Dict, List, Set

# Add the project root to sys.path to allow imports from src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.database import get_db_connection, DB_PATH
from src.analysis.data import get_stock_data
from src.analysis.markers import MarkerRegistry

_registry = MarkerRegistry()


def init_screener_watchlists(cursor: sqlite3.Cursor) -> Dict[str, int]:
    """
    Ensures screener watchlists exist and clears them for a fresh scan.

    Watchlist names are auto-discovered from registered marker metadata —
    no hardcoded list required.

    Returns a mapping of watchlist names to their IDs.
    """
    screener_names = _registry.get_screener_names()

    watchlist_ids = {}
    for name in screener_names:
        # Ensure watchlist exists
        cursor.execute("INSERT OR IGNORE INTO watchlists (name) VALUES (?)", (name,))
        cursor.execute("SELECT id FROM watchlists WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row:
            w_id = row[0]
            watchlist_ids[name] = w_id
            # Clear existing items for a fresh slate
            cursor.execute("DELETE FROM watchlist_items WHERE watchlist_id = ?", (w_id,))

    return watchlist_ids


def get_scan_universe(cursor: sqlite3.Cursor) -> List[str]:
    """
    Fetches the symbols to scan from NIFTY 500 and CORE watchlists.
    """
    symbols: Set[str] = set()

    # 1. Fetch NIFTY 500 symbols
    cursor.execute("SELECT id FROM watchlists WHERE name LIKE 'NIFTY 500'")
    nifty_row = cursor.fetchone()
    if nifty_row:
        nifty_id = nifty_row[0]
        cursor.execute("SELECT symbol FROM watchlist_items WHERE watchlist_id = ?", (nifty_id,))
        symbols.update(row[0] for row in cursor.fetchall())
    else:
        print("Warning: 'NIFTY 500' watchlist not found.")

    # 2. Fetch CORE symbols
    cursor.execute("SELECT id FROM watchlists WHERE name LIKE 'CORE'")
    core_row = cursor.fetchone()
    if core_row:
        core_id = core_row[0]
        cursor.execute("SELECT symbol FROM watchlist_items WHERE watchlist_id = ?", (core_id,))
        symbols.update(row[0] for row in cursor.fetchall())
    else:
        print("Warning: 'CORE' watchlist not found.")

    return sorted(list(symbols))


def run_screener():
    """Main function to run the screener."""
    # Use a longer timeout to handle potential locks from other processes
    conn = sqlite3.connect(DB_PATH, timeout=30)
    cursor = conn.cursor()

    try:
        # Initialize watchlists and clear old data
        watchlist_map = init_screener_watchlists(cursor)
        conn.commit()  # Commit here to release the lock after initialization

        # Get universe of stocks
        universe = get_scan_universe(cursor)
        if not universe:
            print("No symbols found in scan universe. Exiting.")
            return

        print(f"Starting scan for {len(universe)} symbols...")

        # Collect all markers that participate in screening
        screen_markers = [
            m for m in _registry.get_all()
            if m.metadata().get('screener_name')
        ]

        results_counts = {name: 0 for name in watchlist_map.keys()}

        for symbol in universe:
            sys.stdout.write(f"Scanning {symbol: <15}")
            sys.stdout.flush()

            try:
                # lookback_days=5 is enough to get today's status
                df, _, _ = get_stock_data(symbol, lookback_days=5, agg_period='daily')

                if df.empty:
                    sys.stdout.write(" [EMPTY]\n")
                    continue

                latest = df.iloc[-1]
                prev = df.iloc[-2] if len(df) >= 2 else None

                assigned = []

                # Delegate all screening to marker.screen()
                for marker in screen_markers:
                    meta = marker.metadata()
                    screener_name = meta['screener_name']

                    if marker.screen(df, latest, prev):
                        w_id = watchlist_map[screener_name]
                        results_counts[screener_name] += 1
                        cursor.execute(
                            "INSERT OR IGNORE INTO watchlist_items "
                            "(watchlist_id, symbol, display_order) VALUES (?, ?, ?)",
                            (w_id, symbol, results_counts[screener_name])
                        )
                        # Build a short display label from screener name
                        label = screener_name.replace('SCR: ', '')
                        # Add grind level detail if applicable
                        if meta.get('flag_key') == 'grind_level':
                            level = int(latest.get('grind_level', 0))
                            label = f"GRIND G{level}"
                        assigned.append(label)

                if not assigned:
                    sys.stdout.write(" [SKIP]\n")
                else:
                    sys.stdout.write(f" [{', '.join(assigned)}]\n")
                    conn.commit()  # Commit each insertion to keep transaction short

            except Exception as e:
                sys.stdout.write(f" [ERROR: {str(e)}]\n")

        # Summary
        print("\n" + "=" * 30)
        print("SCREENER SUMMARY")
        print("=" * 30)
        for name, count in results_counts.items():
            print(f"{name: <20}: {count}")
        print("=" * 30)

        conn.commit()
    except Exception as e:
        print(f"An error occurred: {e}")
        conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    run_screener()
