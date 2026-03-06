#!/usr/bin/env python3
"""
Test script for the Feature Engineer module.

Runs the divergence engine for a stock, applies feature engineering,
and validates all 22 new columns are computed correctly.

Usage:
    python scripts/test_feature_engineer.py
    python scripts/test_feature_engineer.py --symbol HDFCBANK
"""

from __future__ import annotations

import sys
import argparse
from pathlib import Path
from tabulate import tabulate

# Ensure project root is in PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.divergence_engine.engine import DivergenceEngine
from src.feature_engineer import engineer_features

# ── Expected columns added by engineer_features ──────────────────────────

EXPECTED_COLUMNS = [
    # CWVAP (7)
    "cwvap_lag_3d", "cwvap_lag_5d", "cwvap_lag_10d",
    "cwvap_slope_3d", "cwvap_slope_5d", "cwvap_slope_10d",
    "cwvap_acceleration",
    # RDV (4)
    "rdv_slope_3d", "rdv_slope_5d", "rdv_slope_10d",
    "rdv_consistency",
    # Coherence (4)
    "coherence_trend_3d", "coherence_trend_5d", "coherence_trend_10d",
    "coherence_volatility",
    # MFM (4)
    "mfm_slope_3d", "mfm_slope_10d",
    "mfm_vs_10d_avg", "mfm_acceleration",
    # PDD (1)
    "pdd_weighted_avg",
    # Alignment (6)
    "all_aligned_3d", "all_aligned_5d", "all_aligned_10d",
    "setup_duration_3d", "setup_duration_5d", "setup_duration_10d",
]


def main():
    parser = argparse.ArgumentParser(description="Test Feature Engineer")
    parser.add_argument("--symbol", default="RELIANCE", help="NSE Ticker")
    parser.add_argument("--days", type=int, default=15, help="Days to display")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    print(f"═══ Feature Engineer Test — {symbol} ═══\n")

    # ── Step 1: Run engine ────────────────────────────────────────────
    print(f"[1/3] Running DivergenceEngine for {symbol}...")
    engine = DivergenceEngine(ticker=symbol)
    result = engine.run()
    ledger = result.ledger
    print(f"      Engine ledger: {len(ledger)} rows, {len(ledger.columns)} columns\n")

    # ── Step 2: Apply feature engineering ─────────────────────────────
    print("[2/3] Applying engineer_features()...")
    df = engineer_features(ledger)
    new_col_count = len(df.columns) - len(ledger.columns)
    print(f"      Result: {len(df)} rows, {len(df.columns)} columns (+{new_col_count} new)\n")

    # ── Step 3: Validation checks ─────────────────────────────────────
    print("[3/3] Running validation checks...\n")
    checks = []
    all_pass = True

    # Check 1: All expected columns exist
    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    passed = len(missing) == 0
    checks.append(("All 22 columns exist", "PASS" if passed else f"FAIL — missing: {missing}"))
    all_pass &= passed

    # Check 2: No NaN in last 100 rows (warmup excluded)
    tail = df.tail(100)
    for col in EXPECTED_COLUMNS:
        if col in df.columns:
            nan_count = tail[col].isna().sum()
            if nan_count > 0:
                checks.append((f"NaN check: {col}", f"FAIL — {nan_count}/100 NaN"))
                all_pass = False
    if all(tail[c].notna().all() for c in EXPECTED_COLUMNS if c in df.columns):
        checks.append(("No NaN in last 100 rows", "PASS"))

    # Check 3: CWVAP lags match shifted values
    if "cwvap_lag_3d" in df.columns:
        lag_match = (df["cwvap_lag_3d"].dropna() == df["cwvap"].shift(3).dropna()).all()
        checks.append(("cwvap_lag_3d matches cwvap.shift(3)", "PASS" if lag_match else "FAIL"))
        all_pass &= lag_match

    # Check 4: setup_duration resets correctly
    for period in (3, 5, 10):
        align_col = f"all_aligned_{period}d"
        dur_col = f"setup_duration_{period}d"
        if align_col in df.columns and dur_col in df.columns:
            # Where aligned is 0, duration must be 0
            false_mask = df[align_col] == 0
            dur_at_false = df.loc[false_mask, dur_col]
            reset_ok = (dur_at_false == 0).all()
            checks.append((f"setup_duration_{period}d resets on False", "PASS" if reset_ok else "FAIL"))
            all_pass &= reset_ok

    # Check 5: pdd_weighted_avg range
    if "pdd_weighted_avg" in df.columns:
        pdd_range = df["pdd_weighted_avg"].dropna()
        in_range = (pdd_range.abs() < 20).all()  # reasonable bound
        checks.append((f"pdd_weighted_avg range (max={pdd_range.abs().max():.2f})", "PASS" if in_range else "FAIL"))
        all_pass &= in_range

    # Print check results
    print(tabulate(checks, headers=["Check", "Result"], tablefmt="rounded_grid"))
    print()

    # ── Display last N days ───────────────────────────────────────────
    display_cols = ["date"] + EXPECTED_COLUMNS
    display_cols = [c for c in display_cols if c in df.columns]
    tail_df = df[display_cols].tail(args.days).copy()

    # Format numeric columns
    for col in tail_df.columns:
        if col == "date":
            tail_df[col] = tail_df[col].astype(str).str[:10]
        elif col.startswith("all_aligned"):
            tail_df[col] = tail_df[col].astype(int)
        elif col.startswith("setup_duration"):
            tail_df[col] = tail_df[col].astype(int)
        else:
            tail_df[col] = tail_df[col].apply(lambda x: f"{x:.4f}" if not (x != x) else "NaN")

    print(f"Last {args.days} days — CWVAP Features:")
    cwvap_cols = ["date", "cwvap_lag_3d", "cwvap_slope_3d", "cwvap_slope_5d", "cwvap_slope_10d", "cwvap_acceleration"]
    print(tabulate(tail_df[[c for c in cwvap_cols if c in tail_df.columns]], headers="keys", tablefmt="rounded_grid", showindex=False))

    print(f"\nLast {args.days} days — RDV + Coherence + MFM Features:")
    rcm_cols = ["date", "rdv_slope_5d", "rdv_consistency", "coherence_trend_5d", "coherence_volatility", "mfm_slope_3d", "mfm_vs_10d_avg"]
    print(tabulate(tail_df[[c for c in rcm_cols if c in tail_df.columns]], headers="keys", tablefmt="rounded_grid", showindex=False))

    print(f"\nLast {args.days} days — Alignment Features:")
    align_cols = ["date", "pdd_weighted_avg", "all_aligned_3d", "setup_duration_3d", "all_aligned_5d", "setup_duration_5d", "all_aligned_10d", "setup_duration_10d"]
    print(tabulate(tail_df[[c for c in align_cols if c in tail_df.columns]], headers="keys", tablefmt="rounded_grid", showindex=False))

    # ── Final verdict ─────────────────────────────────────────────────
    print()
    if all_pass:
        print("✅ ALL CHECKS PASSED — Feature Engineer is working correctly.")
    else:
        print("❌ SOME CHECKS FAILED — Review the output above.")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
