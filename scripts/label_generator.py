#!/usr/bin/env python3
"""Zone-aware directional Triple Barrier labeling for supervised learning targets.

Flow:
  1. Pre-filter: determine if each row is in a demand zone (below CWVAP) or
     supply zone (above CWVAP) within the configured ATR distance band.
  2. Directional barrier: only check the reversal direction for that zone —
     upper barrier for demand zone entries, lower barrier for supply zone.
  3. Optional RDV conviction gate: require raw delivery volume above a threshold.
  4. Cooldown deduplication per symbol.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def directional_barrier(prices, start_idx, atr_value, direction, profit_mult=2.0, stop_mult=2.0, horizon=10):
    """Check barrier in one direction only.

    direction="up"   → only check upper barrier (reversal from demand zone)
    direction="down" → only check lower barrier (reversal from supply zone)

    Returns label string or None (timeout / wrong-direction move).
    """
    entry = prices.iloc[start_idx]
    window = prices.iloc[start_idx + 1 : start_idx + horizon + 1]

    if direction == "up":
        target = entry + (profit_mult * atr_value)
        for price in window:
            if price >= target:
                return "STRONG_UP"
    else:
        target = entry - (stop_mult * atr_value)
        for price in window:
            if price <= target:
                return "STRONG_DOWN"

    return None  # timeout — barrier not hit within horizon


def classify_zone(close, cwvap, atr, dist_min, dist_max):
    """Determine if a row is in a demand or supply zone using numerical distance.

    Returns:
        "demand" — close is below CWVAP, within [dist_min, dist_max] of it
        "supply" — close is above CWVAP, within [dist_min, dist_max] of it
        None     — outside the distance band or data missing
    """
    if pd.isna(cwvap) or pd.isna(close) or pd.isna(atr) or atr == 0:
        return None

    gap = close - cwvap  # negative = below CWVAP, positive = above

    if gap <= 0:
        # Below CWVAP — potential demand zone
        abs_gap = abs(gap)
        if dist_min <= abs_gap <= dist_max:
            return "demand"
    else:
        # Above CWVAP — potential supply zone
        if dist_min <= gap <= dist_max:
            return "supply"

    return None


def label_symbol_group(
    group, profit_mult, stop_mult, horizon, cooldown,
    cwvap_filter, cwvap_atr_min, cwvap_atr_max,
    cwvap_pct_min, cwvap_pct_max, min_rdv,
    label_mode="direction",
    max_cwc=None, min_rdv_consistency=None,
):
    """Apply zone-aware labeling to a single symbol group.

    label_mode:
      "direction"     — STRONG_UP / STRONG_DOWN (timeouts dropped)
      "followthrough"  — FOLLOW_THROUGH / TIMEOUT (predict setup quality)

    Steps:
      1. For each row, determine zone (demand/supply) from CWVAP distance band.
      2. Only check the reversal barrier for that zone direction.
      3. Apply conviction gates (rdv, rdv_consistency, cwc).
      4. Apply cooldown deduplication.
    """
    group = group.sort_values("date").reset_index(drop=True)
    prices = group["close"]
    atrs = group["atr_20"]
    has_cwvap = cwvap_filter and "cwvap" in group.columns
    cwvaps = group["cwvap"] if has_cwvap else None
    has_rdv = min_rdv is not None and "rdv" in group.columns
    rdvs = group["rdv"] if has_rdv else None
    has_cwc = max_cwc is not None and "cwc" in group.columns
    cwcs = group["cwc"] if has_cwc else None
    has_rdv_cons = min_rdv_consistency is not None and "rdv_consistency" in group.columns
    rdv_cons = group["rdv_consistency"] if has_rdv_cons else None
    use_pct = cwvap_pct_min is not None and cwvap_pct_max is not None
    n = len(group)

    labels = [None] * n
    directions = [None] * n  # track which barrier was checked
    for i in range(n):
        atr = atrs.iloc[i]
        if pd.isna(atr) or atr == 0:
            continue
        if i + horizon >= n:
            continue

        close = prices.iloc[i]

        # --- RDV conviction gate ---
        if has_rdv:
            rdv = rdvs.iloc[i]
            if pd.isna(rdv) or rdv < min_rdv:
                continue

        # --- RDV consistency gate ---
        if has_rdv_cons:
            rc = rdv_cons.iloc[i]
            if pd.isna(rc) or rc < min_rdv_consistency:
                continue

        # --- CWC gate (max cross-window coherence) ---
        if has_cwc:
            cwc_val = cwcs.iloc[i]
            if pd.isna(cwc_val) or cwc_val > max_cwc:
                continue

        # --- Zone classification & directional barrier ---
        if has_cwvap:
            cwvap = cwvaps.iloc[i]
            if use_pct:
                dist_min = cwvap_pct_min * close
                dist_max = cwvap_pct_max * close
            else:
                dist_min = cwvap_atr_min * atr
                dist_max = cwvap_atr_max * atr

            zone = classify_zone(close, cwvap, atr, dist_min, dist_max)
            if zone is None:
                continue  # outside band — not eligible

            if zone == "demand":
                barrier_result = directional_barrier(prices, i, atr, "up", profit_mult, stop_mult, horizon)
                directions[i] = "up"
            else:  # supply
                barrier_result = directional_barrier(prices, i, atr, "down", profit_mult, stop_mult, horizon)
                directions[i] = "down"
        else:
            # No CWVAP filter — bidirectional (original behavior)
            barrier_result = directional_barrier(prices, i, atr, "up", profit_mult, stop_mult, horizon)
            if barrier_result is not None:
                directions[i] = "up"
            else:
                barrier_result = directional_barrier(prices, i, atr, "down", profit_mult, stop_mult, horizon)
                if barrier_result is not None:
                    directions[i] = "down"

        # --- Assign label based on mode ---
        if label_mode == "followthrough":
            if barrier_result is not None:
                labels[i] = "FOLLOW_THROUGH"
            else:
                labels[i] = "TIMEOUT"
        else:  # direction mode
            if barrier_result is None:
                continue  # timeout — drop
            labels[i] = barrier_result

    group["label"] = labels
    group["barrier_direction"] = directions

    # Drop rows without labels (ineligible rows)
    group = group[group["label"].notna()].copy()

    # Cooldown deduplication
    if group.empty:
        return group
    group = group.sort_values("date").reset_index(drop=True)
    keep = []
    last_kept_idx = -cooldown - 1
    for i in range(len(group)):
        if i - last_kept_idx > cooldown:
            keep.append(i)
            last_kept_idx = i
    group = group.iloc[keep]

    return group


def main():
    parser = argparse.ArgumentParser(
        description="Zone-aware directional Triple Barrier label generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  # Default: ATR-adaptive CWVAP band, no RDV gate
  %(prog)s --horizon 5 --cooldown 5

  # With RDV conviction gate
  %(prog)s --horizon 5 --cooldown 5 --min-rdv 1.0

  # Tight near-CWVAP setups only
  %(prog)s --cwvap-atr-max 1.5

  # Deep mean-reversion only
  %(prog)s --cwvap-atr-min 2.0 --cwvap-atr-max 4.0

  # Fixed % distance mode
  %(prog)s --cwvap-pct-min 0.0 --cwvap-pct-max 0.05

  # No CWVAP filter (original bidirectional behavior)
  %(prog)s --no-cwvap-filter
""",
    )
    parser.add_argument("--input", default="data/panel.parquet")
    parser.add_argument("--output", default="data/labeled_panel.parquet")
    parser.add_argument("--profit-mult", type=float, default=2.0,
                        help="Upper barrier = close + N × ATR (default: 2.0)")
    parser.add_argument("--stop-mult", type=float, default=2.0,
                        help="Lower barrier = close - N × ATR (default: 2.0)")
    parser.add_argument("--horizon", type=int, default=10,
                        help="Max forward trading days to check barriers (default: 10)")
    parser.add_argument("--cooldown", type=int, default=10,
                        help="Min rows between labels per symbol (default: 10)")
    parser.add_argument(
        "--label-mode", choices=["direction", "followthrough"], default="direction",
        help="direction: STRONG_UP/DOWN (timeouts dropped). "
             "followthrough: FOLLOW_THROUGH/TIMEOUT (predict setup quality). Default: direction",
    )

    cwvap_grp = parser.add_argument_group("CWVAP zone filter")
    cwvap_grp.add_argument(
        "--no-cwvap-filter", action="store_true",
        help="Disable CWVAP zone filter — reverts to bidirectional barrier check",
    )
    cwvap_grp.add_argument(
        "--cwvap-atr-min", type=float, default=0.0,
        help="Min distance from CWVAP in ATR units (default: 0.0 = touching CWVAP counts)",
    )
    cwvap_grp.add_argument(
        "--cwvap-atr-max", type=float, default=3.5,
        help="Max distance from CWVAP in ATR units (default: 3.5)",
    )
    cwvap_grp.add_argument(
        "--cwvap-pct-min", type=float, default=None,
        help="Min distance as %% of close (e.g. 0.0). Both pct args must be set to use %% mode.",
    )
    cwvap_grp.add_argument(
        "--cwvap-pct-max", type=float, default=None,
        help="Max distance as %% of close (e.g. 0.05 for 5%%). Both pct args must be set to use %% mode.",
    )

    conv_grp = parser.add_argument_group("Conviction gates")
    conv_grp.add_argument(
        "--min-rdv", type=float, default=None,
        help="Min raw relative delivery volume at entry (e.g. 1.0 = above 20-day avg). Default: OFF.",
    )
    conv_grp.add_argument(
        "--min-rdv-consistency", type=float, default=None,
        help="Min RDV consistency (days in last 5 with above-avg delivery, e.g. 2). Default: OFF.",
    )
    conv_grp.add_argument(
        "--max-cwc", type=float, default=None,
        help="Max cross-window coherence (lower = windows disagreeing = setup building, e.g. 0.65). Default: OFF.",
    )

    args = parser.parse_args()

    panel = pd.read_parquet(args.input)
    cwvap_filter = not args.no_cwvap_filter
    use_pct = args.cwvap_pct_min is not None and args.cwvap_pct_max is not None

    print(f"Input: {len(panel)} rows, {panel['symbol'].nunique()} symbols")
    print(f"Label mode: {args.label_mode}")
    if cwvap_filter:
        if use_pct:
            print(f"CWVAP filter: ON  |  distance = [{args.cwvap_pct_min*100:.1f}%, {args.cwvap_pct_max*100:.1f}%] of close (fixed)")
        else:
            print(f"CWVAP filter: ON  |  distance = [{args.cwvap_atr_min}×, {args.cwvap_atr_max}×] ATR20 (symbol-adaptive)")
    else:
        print("CWVAP filter: OFF (bidirectional mode)")
    if args.min_rdv is not None:
        print(f"RDV gate: ON  |  min rdv = {args.min_rdv}")
    else:
        print("RDV gate: OFF")
    if args.min_rdv_consistency is not None:
        print(f"RDV consistency gate: ON  |  min rdv_consistency = {args.min_rdv_consistency}")
    else:
        print("RDV consistency gate: OFF")
    if args.max_cwc is not None:
        print(f"CWC gate: ON  |  max cwc = {args.max_cwc}")
    else:
        print("CWC gate: OFF")

    results = []
    for symbol, group in panel.groupby("symbol"):
        labeled = label_symbol_group(
            group, args.profit_mult, args.stop_mult, args.horizon, args.cooldown,
            cwvap_filter=cwvap_filter,
            cwvap_atr_min=args.cwvap_atr_min,
            cwvap_atr_max=args.cwvap_atr_max,
            cwvap_pct_min=args.cwvap_pct_min if use_pct else None,
            cwvap_pct_max=args.cwvap_pct_max if use_pct else None,
            min_rdv=args.min_rdv,
            label_mode=args.label_mode,
            max_cwc=args.max_cwc,
            min_rdv_consistency=args.min_rdv_consistency,
        )
        if not labeled.empty:
            results.append(labeled)

    if not results:
        print("WARNING: No labels generated — check filter settings.")
        return

    output = pd.concat(results, ignore_index=True)

    # Stats
    dist = output["label"].value_counts().to_dict()
    stats = {
        "input_rows": len(panel),
        "labeled_rows": len(output),
        "symbols": int(output["symbol"].nunique()),
        "class_distribution": dist,
        "params": {
            "profit_mult": args.profit_mult,
            "stop_mult": args.stop_mult,
            "horizon": args.horizon,
            "cooldown": args.cooldown,
            "cwvap_filter": cwvap_filter,
            "cwvap_atr_min": args.cwvap_atr_min,
            "cwvap_atr_max": args.cwvap_atr_max,
            "cwvap_pct_min": args.cwvap_pct_min,
            "cwvap_pct_max": args.cwvap_pct_max,
            "min_rdv": args.min_rdv,
            "min_rdv_consistency": args.min_rdv_consistency,
            "max_cwc": args.max_cwc,
            "label_mode": args.label_mode,
        },
    }
    print(f"Output: {len(output)} labeled rows across {output['symbol'].nunique()} symbols")
    for k, v in dist.items():
        pct = v / len(output) * 100
        print(f"  {k}: {v} ({pct:.1f}%)")

    output.to_parquet(args.output, index=False)
    stats_path = Path(args.output).parent / "label_stats.json"
    stats_path.write_text(json.dumps(stats, indent=2))
    print(f"Saved: {args.output}, {stats_path}")


if __name__ == "__main__":
    main()
