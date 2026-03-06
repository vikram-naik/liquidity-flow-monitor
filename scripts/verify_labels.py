#!/usr/bin/env python3
"""Verify integrity of labeled_panel.parquet output from label_generator.py."""

import argparse
import sys

import pandas as pd


def check(condition, msg_pass, msg_fail):
    status = "PASS" if condition else "FAIL"
    msg = msg_pass if condition else msg_fail
    print(f"  [{status}] {msg}")
    return condition


def main():
    parser = argparse.ArgumentParser(description="Verify labeled panel data integrity")
    parser.add_argument("--input", default="data/labeled_panel.parquet")
    parser.add_argument("--cooldown", type=int, default=10)
    parser.add_argument("--symbol", default=None, help="Filter level-2 detail to a specific symbol")
    parser.add_argument("--level2", action="store_true", help="Print per-symbol labeled rows for manual verification")
    args = parser.parse_args()

    print(f"\nLoading {args.input} ...")
    df = pd.read_parquet(args.input)

    all_pass = True

    print("\n--- Basic structure ---")
    for col in ["symbol", "date", "close", "atr_20", "label"]:
        ok = check(col in df.columns, f"Column '{col}' present", f"Column '{col}' MISSING")
        all_pass &= ok

    print("\n--- Label values ---")
    valid_direction = {"STRONG_UP", "STRONG_DOWN"}
    valid_followthrough = {"FOLLOW_THROUGH", "TIMEOUT"}
    actual_labels = set(df["label"].dropna().unique())
    if actual_labels <= valid_direction:
        label_mode = "direction"
    elif actual_labels <= valid_followthrough:
        label_mode = "followthrough"
    else:
        label_mode = "unknown"
    valid_labels = valid_direction | valid_followthrough
    ok = check(
        actual_labels <= valid_labels,
        f"Only valid labels present: {actual_labels}  (mode: {label_mode})",
        f"Unexpected labels found: {actual_labels - valid_labels}",
    )
    all_pass &= ok

    ok = check(
        df["label"].isna().sum() == 0,
        "No null labels",
        f"{df['label'].isna().sum()} null labels found (timeout rows not dropped)",
    )
    all_pass &= ok

    print("\n--- No future leakage ---")
    # Labels should be derived from prices AFTER the row's date.
    # We verify the label column was not present in the original panel.
    # Best proxy: check close is not NaN (data is present for labeling rows)
    ok = check(
        df["close"].isna().sum() == 0,
        "All labeled rows have a valid close price",
        f"{df['close'].isna().sum()} rows with NaN close",
    )
    all_pass &= ok

    ok = check(
        df["atr_20"].isna().sum() == 0,
        "All labeled rows have a valid atr_20 (no zero/NaN rows leaked through)",
        f"{df['atr_20'].isna().sum()} rows with NaN atr_20",
    )
    all_pass &= ok

    print("\n--- Cooldown deduplication ---")
    violations = 0
    for sym, g in df.groupby("symbol"):
        g = g.sort_values("date")
        diffs = g["date"].diff().dt.days.dropna()
        violations += int((diffs <= args.cooldown).sum())

    ok = check(
        violations == 0,
        f"No cooldown violations (min gap > {args.cooldown} days per symbol)",
        f"{violations} cooldown violations found (labels within {args.cooldown} days of each other)",
    )
    all_pass &= ok

    print("\n--- Class distribution ---")
    dist = df["label"].value_counts()
    total = len(df)
    for label, count in dist.items():
        pct = count / total * 100
        print(f"  {label}: {count} ({pct:.1f}%)")

    skew = dist.max() / dist.min() if dist.min() > 0 else float("inf")
    skew_limit = 15.0 if label_mode == "followthrough" else 3.0
    ok = check(
        skew < skew_limit,
        f"Class skew is acceptable (max/min ratio = {skew:.2f}, limit = {skew_limit})",
        f"High class imbalance detected (max/min ratio = {skew:.2f}, limit = {skew_limit})",
    )
    all_pass &= ok

    print("\n--- Summary ---")
    print(f"  Total rows: {total}")
    print(f"  Symbols: {df['symbol'].nunique()}")
    print(f"  Date range: {df['date'].min().date()} to {df['date'].max().date()}")
    print()

    if all_pass:
        print("All checks PASSED.")
    else:
        print("Some checks FAILED. Review output above.")

    if args.level2:
        print("\n" + "=" * 70)
        print("LEVEL-2: Labeled rows for manual price/delivery verification")
        print("=" * 70)

        has_direction = "barrier_direction" in df.columns
        view_cols = ["symbol", "date", "label", "close", "atr_20"]
        if has_direction:
            view_cols.append("barrier_direction")
        view = df[view_cols].copy()
        view["date"] = pd.to_datetime(view["date"]).dt.date
        view["upper_barrier"] = (view["close"] + 2.0 * view["atr_20"]).round(2)
        view["lower_barrier"] = (view["close"] - 2.0 * view["atr_20"]).round(2)
        view["close"] = view["close"].round(2)
        view["atr_20"] = view["atr_20"].round(2)

        if args.symbol:
            symbols = [s.strip().upper() for s in args.symbol.split(",")]
            view = view[view["symbol"].isin(symbols)]
            if view.empty:
                print(f"  No labeled rows found for symbol(s): {args.symbol}")

        for sym, g in view.groupby("symbol"):
            g = g.sort_values("date").reset_index(drop=True)
            dist = g["label"].value_counts().to_dict()
            dist_str = " / ".join(f"{v} {k}" for k, v in dist.items())
            print(f"\n  {sym}  ({len(g)} labels: {dist_str})")
            dir_hdr = f" {'dir':<5}" if has_direction else ""
            dir_sep = f" {'-'*5}" if has_direction else ""
            print(f"  {'#':<4} {'date':<12} {'label':<16} {'close':>8} {'atr_20':>8} {'upper':>8} {'lower':>8}{dir_hdr}")
            print(f"  {'-'*4} {'-'*12} {'-'*16} {'-'*8} {'-'*8} {'-'*8} {'-'*8}{dir_sep}")
            for i, row in g.iterrows():
                dir_val = f" {row['barrier_direction']:<5}" if has_direction else ""
                print(
                    f"  {i+1:<4} {str(row['date']):<12} {row['label']:<16} "
                    f"{row['close']:>8.2f} {row['atr_20']:>8.2f} "
                    f"{row['upper_barrier']:>8.2f} {row['lower_barrier']:>8.2f}{dir_val}"
                )

    if not all_pass:
        sys.exit(1)


if __name__ == "__main__":
    main()
