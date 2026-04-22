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
    "accel-0-cross":     EntryTag.ACCEL_ZERO_CROSS.value,
    "prt-slope-zero-cross": EntryTag.PRT_SLOPE_ZERO_CROSS.value,
    "fas-zero-cross": EntryTag.FAS_ZERO_CROSS.value,
    "fas-floor-reversion": EntryTag.FAS_FLOOR_REVERSION.value,
    "fas-buy-cross": EntryTag.FAS_BUY_CROSS.value,
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

    # Initialize signal day info defaults
    for t in all_trades:
        t.signal_date = t.entry_date
        t.fas_signal = 0.0
        t.cts_signal = 0.0
        t.pdd_signal = 0.0
        t.regime_signal = "N/A"
        t.coherence_signal = 0.0

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

    # Calculate MFE logic and signal context (in-script computation)
    if filtered:
        print(f"\nCalculating MFE and Signal Context for {len(filtered)} trades...")
        by_sym = {}
        for t in filtered:
            by_sym.setdefault(t.symbol, []).append(t)
        
        for sym, trades in by_sym.items():
            try:
                engine = DivergenceEngine(sym)
                res = engine.run()
                ledger = res.ledger
                if ledger is None or ledger.empty:
                    print(f"  DEBUG: {sym} ledger is empty!")
                    continue
                # print(f"  DEBUG: {sym} ledger columns: {ledger.columns.tolist()[:10]}")
                # Ensure date is string for matching
                ledger['date_str'] = ledger['date'].astype(str).str[:10]
                
                for t in trades:
                    # Find the index of the entry date
                    matches = ledger[ledger['date_str'] == t.entry_date]
                    if matches.empty:
                        continue
                        
                    entry_idx_in_ledger = matches.index[0]
                    sig_idx = entry_idx_in_ledger - 1
                    
                    if sig_idx >= 0:
                        sig_row = ledger.iloc[sig_idx]
                        old_sig_date = t.signal_date
                        t.signal_date = str(sig_row["date"])[:10]
                        t.fas_signal = sig_row.get("fas", 0.0)
                        t.cts_signal = sig_row.get("cts", 0.0)
                        
                        pdd = sig_row.get("pdd_120", np.nan)
                        if np.isnan(pdd): pdd = sig_row.get("pdd_60", np.nan)
                        if np.isnan(pdd): pdd = sig_row.get("pdd_30", np.nan)
                        t.pdd_signal = pdd
                        
                        t.regime_signal = sig_row.get("regime", "N/A")
                        t.coherence_signal = sig_row.get("coherence", 0.0)

                    # Trade active from entry_idx to entry_idx + duration
                    # We use entry_idx_in_ledger to ensure we are in the right spot
                    start_idx = entry_idx_in_ledger
                    end_idx = min(entry_idx_in_ledger + t.duration, len(ledger) - 1)
                    trade_period = ledger.iloc[start_idx : end_idx + 1]
                    if not trade_period.empty:
                        max_high = trade_period["high"].max()
                        t.mfe_pct = (max_high / t.entry_price - 1) * 100
            except Exception:
                pass

    # Sort
    sort_map = {
        "entry_date": lambda t: t.signal_date,
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
    header = (f"{'#':>3} | {'Symbol':<12} | {'Sig Date':<10} | {'PnL%':>7} | {'MFE%':>7} | "
              f"{'FAS':>7} | {'CTS':>7} | {'PDD':>7} | {'Regime':<10} | {'COH':>5} | {'SCORE':>5} | {'Exit Reason'}")
    print(header)
    print("-" * len(header))
    for i, t in enumerate(filtered, 1):
        reason = t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)
        print(f"{i:>3} | {t.symbol:<12} | {t.signal_date:<10} | {t.pnl_pct:>7.2f} | "
              f"{t.mfe_pct:>7.2f} | {t.fas_signal:>7.3f} | {t.cts_signal:>7.3f} | {t.pdd_signal:>7.2f} | "
              f"{t.regime_signal:<10} | {t.coherence_signal:>5.2f} | {t.conviction_score:>+5} | {reason}")


if __name__ == "__main__":
    main()
