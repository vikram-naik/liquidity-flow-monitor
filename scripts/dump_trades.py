"""Dump trades filtered by entry type for inspection and study.

Usage:
    venv/bin/python3 scripts/dump_trades.py --entry slope-bottom
    venv/bin/python3 scripts/dump_trades.py --entry inst-floor --watchlist "NIFTY 500"
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
from src.trading.signals.savgol_cts.exits.institutional_floor import exit_institutional_floor
from src.trading.signals.enums import EntryTag, ExitReason

ENTRY_ALIASES = {
    "slope-bottom": EntryTag.SLOPE_BOTTOM.value,
    "inst-floor":   EntryTag.INSTITUTIONAL_FLOOR.value,
}

def simulate_new_exit(trade, ledger, cfg):
    """Simulate the Two-Phase exit logic on an existing trade."""
    records = ledger.to_dict("records")
    state_val = 0  # SavgolCTSExitState bitfield
    pending_exit_reason = None
    
    # Simulation starts checking from entry_idx + 1 (EOD-lag)
    for i in range(trade.entry_idx + 1, len(records)):
        row = records[i]
        prev = records[i-1]
        
        # Execute pending exit on this bar's open
        if pending_exit_reason:
            exit_px = row.get("open", row.get("close", trade.entry_price))
            pnl = (exit_px / trade.entry_price - 1) * 100.0
            return pnl, pending_exit_reason, i - trade.entry_idx, str(row.get("date", ""))[:10]
            
        res, state_val = exit_institutional_floor(
            row, prev, trade, 0.0, i - trade.entry_idx, state_val, cfg, records, i
        )
        if res:
            pending_exit_reason = res

    # End of data
    last = records[-1]
    pnl = (last.get("close", trade.entry_price) / trade.entry_price - 1) * 100.0
    return pnl, "END_OF_DATA", len(records) - 1 - trade.entry_idx, str(last.get("date", ""))[:10]

def main():
    parser = argparse.ArgumentParser(description="Dump trades by entry type")
    parser.add_argument("--entry", choices=list(ENTRY_ALIASES.keys()),
                        help="Filter by entry type (default: all)")
    parser.add_argument("--period", default="test", choices=["train", "test", "all"],
                        help="Period to dump (default: test)")
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

    # Enrich with Signal Day data and New Exit simulation
    enriched_data = {}
    if filtered:
        print(f"\nEnriching {len(filtered)} trades and simulating New Exit...")
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
                        
                        # RDV Metrics
                        v = [ledger.iloc[sig_idx - j].get("rdv_slope_z", 0.0) for j in range(1, 4)]
                        rdv_mean = sum(v) / 3.0
                        rdv_trend = "rising" if v[0] > v[1] > v[2] else ("falling" if v[0] < v[1] < v[2] else "can't say")
                        
                        rdv_z = row.get("rdv_slope_z", 0.0)
                        is_above_bt = row.get("cts", 0.0) > row.get("cts_buy_threshold", 0.0)
                        acc_rise = row.get("cts_accel", 0.0) > prev_row.get("cts_accel", 0.0)
                        acc_str = row.get("cts_accel", 0.0) > row.get("cts_accel_threshold", 0.0)
                        slp_rise = row.get("cts_slope", 0.0) > prev_row.get("cts_slope", 0.0)

                        score = sum([1 if cond else -1 for cond in [
                            rdv_z > 0, rdv_z > rdv_mean, rdv_trend == "rising",
                            is_above_bt, slp_rise, acc_rise, acc_str
                        ]])

                        # What-if Exit Simulation
                        new_pnl, new_reason, new_dur, new_exit_date = simulate_new_exit(t, ledger, exit_cfg)

                        enriched_data[id(t)] = {
                            "study_score": score,
                            "new_pnl": new_pnl,
                            "new_reason": new_reason,
                            "new_dur": new_dur,
                            "new_exit_date": new_exit_date
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
    print(f"\n--- {label} TRADES: BASELINE vs NEW EXIT ({args.period.upper()}) ---")
    header = (f"{'#':>3} | {'Symbol':<12} | {'Entry':<10} | {'Exit (B)':<10} | {'Exit (N)':<10} | "
              f"{'PnL(B)%':>8} | {'PnL(N)%':>8} | {'Delta':>6} | {'Exit (Baseline)':<22} | {'Exit (New)'}")
    print(header)
    print("-" * len(header))
    for i, t in enumerate(filtered, 1):
        extra = enriched_data.get(id(t), {"study_score": 0, "new_pnl": 0.0, "new_reason": "n/a", "new_dur": 0, "new_exit_date": "n/a"})

        base_pnl = t.pnl_pct
        new_pnl = extra["new_pnl"]
        delta = new_pnl - base_pnl

        base_reason = t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)
        new_reason = extra["new_reason"].value if hasattr(extra["new_reason"], "value") else str(extra["new_reason"])

        print(f"{i:>3} | {t.symbol:<12} | {t.entry_date:<10} | {t.exit_date:<10} | {extra['new_exit_date']:<10} | "
              f"{base_pnl:>8.2f} | {new_pnl:>8.2f} | {delta:>+6.2f} | {base_reason[:22]:<22} | {new_reason}")

    # Summary
    if filtered:
        b_pnl = sum(t.pnl_pct for t in filtered) / len(filtered)
        n_pnl = sum(enriched_data.get(id(t), {}).get("new_pnl", 0.0) for t in filtered) / len(filtered)
        print(f"\n  Summary Comparison:")
        print(f"  Avg PnL (Baseline): {b_pnl:+.2f}%")
        print(f"  Avg PnL (New Exit): {n_pnl:+.2f}%")
        print(f"  Improvement:       {n_pnl - b_pnl:+.2f}%")


if __name__ == "__main__":
    main()
