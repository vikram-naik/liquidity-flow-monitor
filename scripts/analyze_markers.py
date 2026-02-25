#!/usr/bin/env python3
"""
Marker Debug Analyzer — introspects why markers fired (or didn't) for a
given symbol and date range.

Uses the MarkerRegistry and each marker's ``debug_info()`` method so that
**zero** marker-specific logic lives in this script.

Usage:
    python3 scripts/analyze_markers.py SYMBOL START_DATE [END_DATE] [--marker NAME]

Examples:
    python3 scripts/analyze_markers.py GESHIP 2025-12-20 2026-01-15
    python3 scripts/analyze_markers.py GESHIP 2026-01-12 --marker confirm_bear
"""

import sys
import os
import argparse
import pandas as pd
from tabulate import tabulate

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.analysis.data import get_stock_data
from src.analysis.markers import MarkerRegistry


def main():
    parser = argparse.ArgumentParser(
        description='Analyze marker triggers for a symbol in a date range.'
    )
    parser.add_argument('symbol', type=str, help='Stock symbol (e.g. GESHIP)')
    parser.add_argument('start_date', type=str, help='Start date YYYY-MM-DD')
    parser.add_argument('end_date', type=str, nargs='?', default=None,
                        help='End date YYYY-MM-DD (optional, defaults to start_date)')
    parser.add_argument('--marker', '-m', type=str, default=None,
                        help='Filter to a specific marker by name (e.g. coil, confirm_bear)')
    args = parser.parse_args()

    symbol = args.symbol.upper()
    start = args.start_date
    end = args.end_date or start

    # ── Fetch data ──────────────────────────────────────────────────────
    print(f'Fetching data for {symbol}...')
    df, anchor, _ = get_stock_data(symbol, lookback_days=500)

    if df.empty:
        print(f'No data found for {symbol}')
        sys.exit(1)

    df = df.set_index('record_date')

    # ── Slice date range ────────────────────────────────────────────────
    mask = (df.index >= start) & (df.index <= end)
    sub = df[mask]

    if sub.empty:
        print(f'No data for {symbol} between {start} and {end}')
        sys.exit(1)

    # ── General info ────────────────────────────────────────────────────
    print(f'\n{"="*90}')
    print(f'  {symbol}  |  Anchor: {anchor.get("anchor_date", "?")} ({anchor.get("anchor_type", "?")})')
    print(f'  Date range: {start} → {end}  ({len(sub)} trading days)')
    print(f'{"="*90}')

    gen_cols = ['price_open', 'price_high', 'price_low', 'price_close',
                'volume_total', 'delivery_qty', 'dvl', 'davwap', 'atr_50']
    gen_cols = [c for c in gen_cols if c in sub.columns]
    gen = sub[gen_cols].copy()
    gen.index = gen.index.strftime('%Y-%m-%d')
    # Round numeric columns for readability
    for c in gen.columns:
        if gen[c].dtype in ('float64', 'float32'):
            gen[c] = gen[c].round(2)
    print(tabulate(gen, headers='keys', tablefmt='psql', showindex=True))

    # ── Marker debug ────────────────────────────────────────────────────
    registry = MarkerRegistry()
    markers = registry.get_all()

    if args.marker:
        markers = [m for m in markers if m.name() == args.marker]
        if not markers:
            print(f'\n⚠  No marker found with name "{args.marker}". Available:')
            for m in registry.get_all():
                meta = m.metadata()
                label = meta.get('label', m.name())
                print(f'  - {m.name():20s}  ({label})')
            sys.exit(1)

    # Only show chart markers + the filtered marker (skip screener-only by default)
    if not args.marker:
        markers = [m for m in markers if m.metadata().get('is_chart_marker', False)]

    for marker in markers:
        meta = marker.metadata()
        label = meta.get('label', marker.name())
        flag_key = meta.get('flag_key', '')

        # Check if this marker is active on any row in the range
        active_dates = []
        if flag_key and flag_key in sub.columns:
            for date, row in sub.iterrows():
                val = row.get(flag_key, False)
                if (isinstance(val, bool) and val) or (isinstance(val, (int, float)) and val > 0):
                    active_dates.append(date.strftime('%Y-%m-%d'))

        header = f'  {label} ({marker.name()})'
        if active_dates:
            header += f'  ✓ Active: {", ".join(active_dates)}'
        else:
            header += '  ✗ Not triggered'

        print(f'\n{"─"*90}')
        print(header)
        print(f'{"─"*90}')

        # Get debug_info for each row
        for date in sub.index:
            # Find the position in the original df (not sub)
            row_idx = df.index.get_loc(date)
            checks = marker.debug_info(df, row_idx)

            if not checks:
                continue  # marker doesn't implement debug_info

            date_str = date.strftime('%Y-%m-%d')

            # Determine if this row has the marker active
            row = df.iloc[row_idx]
            is_active = False
            if flag_key and flag_key in df.columns:
                val = row.get(flag_key, False)
                is_active = (isinstance(val, bool) and val) or (isinstance(val, (int, float)) and val > 0)

            status = '✓' if is_active else '·'
            print(f'\n  {status} {date_str}  (close={row["price_close"]:.2f})')

            table = []
            for c in checks:
                passed_icon = '✓' if c['passed'] else '✗'
                table.append([
                    f'  {passed_icon}',
                    c['label'],
                    str(c['value']),
                    str(c['threshold']),
                    str(c['detail']),
                ])

            print(tabulate(table, headers=['', 'Check', 'Value', 'Threshold', 'Detail'],
                           tablefmt='simple', stralign='left', numalign='left',
                           disable_numparse=True))


if __name__ == '__main__':
    main()
