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
    "accel-0-cross":     EntryTag.ACCEL_ZERO_CROSS.value,
    "prt-slope-zero-cross": EntryTag.PRT_SLOPE_ZERO_CROSS.value,
    "fas-zero-cross": EntryTag.FAS_ZERO_CROSS.value,
    "fas-floor-reversion": EntryTag.FAS_FLOOR_REVERSION.value,
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

    # Calculate MFE logic (in-script computation)
    if filtered:
        print(f"\nCalculating MFE for {len(filtered)} trades...")
        by_sym = {}
        for t in filtered:
            by_sym.setdefault(t.symbol, []).append(t)
        
        for sym, trades in by_sym.items():
            try:
                engine = DivergenceEngine(sym)
                res = engine.run()
                ledger = res.ledger
                for t in trades:
                    # Trade active from entry_idx to entry_idx + duration
                    # We look for max high from the bar after entry until exit
                    start_idx = t.entry_idx
                    end_idx = min(t.entry_idx + t.duration, len(ledger) - 1)
                    trade_period = ledger.iloc[start_idx : end_idx + 1]
                    if not trade_period.empty:
                        max_high = trade_period["high"].max()
                        t.mfe_pct = (max_high / t.entry_price - 1) * 100
            except Exception as e:
                print(f"  Error calculating MFE for {sym}: {e}")

    # Sort
    sort_map = {
        "entry_date": lambda t: t.entry_date,
        "pnl": lambda t: t.pnl_pct,
        "symbol": lambda t: t.symbol,
        "mfe": lambda t: t.mfe_pct,
        "mae": lambda t: t.mae_pct,
        "duration": lambda t: t.duration,
        "score": lambda t: t.conviction_score,
    }
    reverse = args.sort in ("pnl", "mfe", "score")
    filtered.sort(key=sort_map[args.sort], reverse=reverse)

    # Print
    print(f"\n--- {label} TRADES: STUDY REPORT ({args.period.upper()}) ---")
    header = (f"{'#':>3} | {'Symbol':<12} | {'Date':<10} | {'PnL%':>7} | {'MFE%':>7} | "
              f"{'SCORE':>5} | {'Exit Reason'}")
    print(header)
    print("-" * len(header))
    for i, t in enumerate(filtered, 1):
        reason = t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)
        print(f"{i:>3} | {t.symbol:<12} | {t.entry_date:<10} | {t.pnl_pct:>7.2f} | "
              f"{t.mfe_pct:>7.2f} | {t.conviction_score:>+5} | {reason}")


if __name__ == "__main__":
    main()
