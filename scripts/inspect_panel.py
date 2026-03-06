#!/usr/bin/env python3
"""Inspect panel features for a given symbol and date.

Usage:
    python scripts/inspect_panel.py --symbol RELIANCE --date 2025-03-04
    python scripts/inspect_panel.py --symbol RELIANCE --date 2025-03-04 --context 3
    python scripts/inspect_panel.py --symbol RELIANCE --date 2025-03-04 --cols close,cwvap,cwvap_dist,atr_20,rdv
    python scripts/inspect_panel.py --symbol RELIANCE --date 2025-03-04 --group cwvap
"""

import argparse
from pathlib import Path

import pandas as pd
from tabulate import tabulate

PANEL_PATH = Path(__file__).resolve().parents[1] / "data" / "panel.parquet"

# Feature groups for targeted inspection
FEATURE_GROUPS = {
    "price": [
        "date", "open", "high", "low", "close", "volume",
        "delivery_qty", "delivery_pct",
    ],
    "atr": [
        "date", "close", "true_high", "true_low", "tr", "atr_20",
    ],
    "cwvap": [
        "date", "close", "cwvap", "cwvap_dist", "cwvap_slope", "cwvap_slope_norm",
        "cwvap_lag_3d", "cwvap_lag_5d", "cwvap_lag_10d",
        "cwvap_slope_3d", "cwvap_slope_5d", "cwvap_slope_10d", "cwvap_acceleration",
    ],
    "cpoc": [
        "date", "close", "cpoc", "cpoc_dist", "poc_spread",
        "cvah", "cval", "va_width", "price_location",
    ],
    "dvl": [
        "date", "close", "dvl_10", "dvl_rate_10", "dvl_30", "dvl_rate_30",
        "dvl_60", "dvl_rate_60", "dvl_120", "dvl_rate_120",
    ],
    "rdv": [
        "date", "close", "rdv", "rdv_slope_z",
        "rdv_slope_3d", "rdv_slope_5d", "rdv_slope_10d", "rdv_consistency",
    ],
    "mcs": [
        "date", "close", "mcs", "mcs_mfm", "mcs_delta",
        "mcs_composite", "mcs_composite_slope",
    ],
    "mfm": [
        "date", "close", "mfm", "mfm_slope_3d", "mfm_slope_10d",
        "mfm_vs_10d_avg", "mfm_acceleration",
    ],
    "coherence": [
        "date", "close", "coherence", "price_slope_z", "rdv_slope_z",
        "price_slope_angle", "rdv_slope_angle",
        "coherence_trend_3d", "coherence_trend_5d", "coherence_trend_10d",
        "coherence_volatility",
    ],
    "velocity": [
        "date", "close",
        "velocity_10", "velocity_10_norm", "velocity_30", "velocity_30_norm",
        "velocity_60", "velocity_60_norm", "velocity_120", "velocity_120_norm",
    ],
    "pdd": [
        "date", "close", "pdd_10", "pdd_30", "pdd_60", "pdd_120",
        "pdd_weighted_avg", "price_distance_10", "price_distance_30",
        "price_distance_60", "price_distance_120",
    ],
    "setup": [
        "date", "close", "cwvap_dist", "rdv", "mcs_composite", "coherence", "mfm",
        "all_aligned_3d", "setup_duration_3d",
        "all_aligned_5d", "setup_duration_5d",
        "all_aligned_10d", "setup_duration_10d",
    ],
    "dvwap": [
        "date", "close", "dvwap_10", "dvwap_30", "dvwap_60", "dvwap_120",
    ],
    "poc": [
        "date", "close", "poc_10", "poc_30", "poc_60", "poc_120",
    ],
    "cwc": [
        "date", "close", "cwc", "c_10_30", "c_30_60", "c_60_120",
        "cwc_delta", "cwc_slope", "gradient_shape",
    ],
}


def load_panel():
    if not PANEL_PATH.exists():
        print(f"Panel not found at {PANEL_PATH}. Run panel_builder.py first.")
        raise SystemExit(1)
    return pd.read_parquet(PANEL_PATH)


def get_rows(panel, symbol, date_str, context):
    """Get the target row and optional surrounding context rows."""
    sym_df = panel[panel["symbol"] == symbol].sort_values("date").reset_index(drop=True)
    if sym_df.empty:
        print(f"Symbol '{symbol}' not found in panel.")
        print(f"Available symbols (first 20): {sorted(panel['symbol'].unique())[:20]}")
        raise SystemExit(1)

    mask = sym_df["date"].astype(str).str.startswith(date_str)
    if not mask.any():
        # Find nearest date
        target = pd.Timestamp(date_str)
        sym_df["_delta"] = (sym_df["date"] - target).abs()
        nearest = sym_df.sort_values("_delta").iloc[0]
        sym_df = sym_df.drop(columns=["_delta"])
        print(f"Date '{date_str}' not found for {symbol}. Nearest: {str(nearest['date'])[:10]}")
        raise SystemExit(1)

    idx = sym_df.index[mask][0]
    start = max(0, idx - context)
    end = min(len(sym_df), idx + context + 1)
    return sym_df.iloc[start:end], idx - start


def format_value(v):
    if pd.isna(v):
        return "NaN"
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"{v:,.2f}"
        return f"{v:.4f}"
    return str(v)


def print_single_row(row, symbol, date_str):
    """Print all features for a single row in vertical table format."""
    print(f"\n{'='*60}")
    print(f"  Symbol: {symbol}  |  Date: {date_str}")
    print(f"{'='*60}\n")

    table = []
    for col in row.index:
        if col == "symbol":
            continue
        table.append([col, format_value(row[col])])

    print(tabulate(table, headers=["Feature", "Value"], tablefmt="simple_outline"))
    print(f"\nTotal features: {len(table)}")


def print_context_rows(df, target_idx, cols, symbol, date_str):
    """Print selected columns for a range of rows with the target row highlighted."""
    # Filter to columns that exist
    available = [c for c in cols if c in df.columns]
    missing = [c for c in cols if c not in df.columns]

    sub = df[available].copy()
    sub["date"] = sub["date"].astype(str).str[:10]

    # Format values
    formatted = sub.copy()
    for col in formatted.columns:
        if col == "date":
            continue
        formatted[col] = formatted[col].apply(format_value)

    # Mark the target row
    markers = [""] * len(formatted)
    markers[target_idx] = " ◄"
    formatted.insert(0, "", markers)

    print(f"\n{'='*60}")
    print(f"  Symbol: {symbol}  |  Target: {date_str}")
    print(f"{'='*60}\n")

    print(tabulate(
        formatted.values.tolist(),
        headers=[""] + list(formatted.columns[1:]),
        tablefmt="simple_outline",
    ))

    if missing:
        print(f"\nColumns not in panel: {', '.join(missing)}")


def main():
    parser = argparse.ArgumentParser(
        description="Inspect panel features for a symbol + date",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Feature groups: " + ", ".join(sorted(FEATURE_GROUPS.keys())),
    )
    parser.add_argument("--symbol", required=True, help="Stock symbol (e.g. RELIANCE)")
    parser.add_argument("--date", required=True, help="Date to inspect (YYYY-MM-DD)")
    parser.add_argument(
        "--context", type=int, default=0,
        help="Number of rows before/after the target date to show (default: 0 = single row)",
    )
    parser.add_argument(
        "--cols", default=None,
        help="Comma-separated column names to display (e.g. close,cwvap,rdv)",
    )
    parser.add_argument(
        "--group", default=None,
        choices=sorted(FEATURE_GROUPS.keys()),
        help="Show a predefined feature group",
    )
    parser.add_argument(
        "--panel", default=str(PANEL_PATH),
        help=f"Path to panel parquet (default: {PANEL_PATH})",
    )
    args = parser.parse_args()

    panel = pd.read_parquet(args.panel)
    rows, target_idx = get_rows(panel, args.symbol.upper(), args.date, args.context)

    if args.cols:
        cols = ["date"] + [c.strip() for c in args.cols.split(",") if c.strip() != "date"]
        print_context_rows(rows, target_idx, cols, args.symbol.upper(), args.date)
    elif args.group:
        print_context_rows(rows, target_idx, FEATURE_GROUPS[args.group], args.symbol.upper(), args.date)
    elif args.context > 0:
        # With context but no specific cols/group, show the setup-relevant features
        print_context_rows(rows, target_idx, FEATURE_GROUPS["setup"], args.symbol.upper(), args.date)
    else:
        # Single row — show everything vertically
        print_single_row(rows.iloc[target_idx], args.symbol.upper(), args.date)


if __name__ == "__main__":
    main()
