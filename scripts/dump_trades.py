"""Dump trades filtered by entry type for inspection and study.

Usage:
    venv/bin/python3 scripts/dump_trades.py --entry slope-bottom
    venv/bin/python3 scripts/dump_trades.py --entry inst-floor --period train
    venv/bin/python3 scripts/dump_trades.py                     # all entry types
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag

ENTRY_ALIASES = {
    "slope-bottom": EntryTag.SLOPE_BOTTOM.value,
    "inst-floor":   EntryTag.INSTITUTIONAL_FLOOR.value,
    "accel-cross":  EntryTag.ACCEL.value,
}

def main():
    parser = argparse.ArgumentParser(description="Dump trades by entry type")
    parser.add_argument("--entry", choices=list(ENTRY_ALIASES.keys()),
                        help="Filter by entry type (default: all)")
    parser.add_argument("--period", default="test", choices=["train", "test", "all"],
                        help="Period to dump (default: test)")
    parser.add_argument("--reason", help="Filter by exit reason (substring match)")
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--sort", default="entry_date",
                        choices=["entry_date", "pnl", "symbol", "mfe", "mae", "duration", "score"],
                        help="Sort column (default: entry_date)")
    args = parser.parse_args()

    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    symbols = get_watchlist_symbols(args.watchlist)
    test_end = today_str()

    all_trades = []
    if args.period in ("train", "all"):
        print(f"Running TRAIN period...", flush=True)
        all_trades.extend(run_period(symbols, "2019-01-01", "2023-12-31",
                                     entry_cfg, exit_cfg, "TRAIN", signal))
    if args.period in ("test", "all"):
        print(f"Running TEST period...", flush=True)
        all_trades.extend(run_period(symbols, "2024-01-01", test_end,
                                     entry_cfg, exit_cfg, "TEST", signal))

    # Filter by entry type
    if args.entry:
        tag_val = ENTRY_ALIASES[args.entry]
        filtered = [t for t in all_trades if t.entry_tag == tag_val]
        label = args.entry.upper()
    else:
        filtered = all_trades
        label = "ALL"

    # Filter by reason
    if args.reason:
        filtered = [t for t in filtered if args.reason.lower() in (t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)).lower()]

    # Enrich with Signal Day data for study
    enriched_data = {}
    if filtered:
        print(f"\nEnriching {len(filtered)} trades with Signal Day data...")
        by_sym = {}
        for t in filtered:
            by_sym.setdefault(t.symbol, []).append(t)
        
        for sym, trades in by_sym.items():
            try:
                engine = DivergenceEngine(sym)
                res = engine.run()
                ledger = res.ledger
                for t in trades:
                    sig_idx = t.entry_idx - 1
                    if sig_idx >= 5:
                        row = ledger.iloc[sig_idx]
                        prev_row = ledger.iloc[sig_idx - 1]
                        
                        is_above_bt = row.get("cts", 0.0) >= row.get("cts_buy_threshold", 0.0)
                        acc_rise = row.get("cts_accel", 0.0) > prev_row.get("cts_accel", 0.0)
                        acc_str = row.get("cts_accel", 0.0) > row.get("cts_accel_threshold", 0.0)
                        slp_rise = row.get("cts_slope", 0.0) > prev_row.get("cts_slope", 0.0)

                        # Simplified Study Conviction Score (-4 to +4)
                        score = sum([1 if cond else -1 for cond in [
                            is_above_bt, slp_rise, acc_rise, acc_str
                        ]])

                        enriched_data[id(t)] = {
                            "study_score": score,
                            "above_bt": is_above_bt,
                            "slp_rise": slp_rise,
                            "acc_rise": acc_rise,
                            "acc_str": acc_str
                        }
            except Exception as e:
                print(f"  Error enriching {sym}: {e}")

    # Sort
    sort_map = {
        "entry_date": lambda t: t.entry_date,
        "pnl": lambda t: t.pnl_pct,
        "symbol": lambda t: t.symbol,
        "mfe": lambda t: t.mfe_pct,
        "mae": lambda t: t.mae_pct,
        "duration": lambda t: t.duration,
        "score": lambda t: enriched_data.get(id(t), {}).get("study_score", 0),
    }
    reverse = args.sort in ("pnl", "mfe", "score")
    filtered.sort(key=sort_map[args.sort], reverse=reverse)

    # Print
    print(f"\n--- {label} TRADES: STUDY REPORT ({args.period.upper()}) ---")
    header = (f"{'#':>3} | {'Symbol':<12} | {'Date':<10} | {'PnL%':>7} | "
              f"{'SCORE':>5} | {'CT>=BT'} | {'SLP^'} | {'ACC^'} | {'STR^'} | {'Exit Reason'}")
    print(header)
    print("-" * len(header))
    for i, t in enumerate(filtered, 1):
        extra = enriched_data.get(id(t), {
            "study_score": 0, "above_bt": False, "slp_rise": False, 
            "acc_rise": False, "acc_str": False
        })
        
        above_bt = "YES" if extra["above_bt"] else "no"
        slp_rise = "YES" if extra["slp_rise"] else "no"
        acc_rise = "YES" if extra["acc_rise"] else "no"
        acc_strong = "YES" if extra["acc_str"] else "no"
        
        reason = t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)
        
        print(f"{i:>3} | {t.symbol:<12} | {t.entry_date:<10} | {t.pnl_pct:>7.2f} | "
              f"{extra['study_score']:>+5} | {above_bt:<6} | {slp_rise:<4} | {acc_rise:<4} | {acc_strong:<4} | {reason}")


if __name__ == "__main__":
    main()
