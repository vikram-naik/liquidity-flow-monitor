#!/usr/bin/env python3
"""
Study: Analyze performance of Custom Bayesian strategy trades,
specifically focusing on the model=momentum vs model=accumulation trade setups.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from scripts.walk_forward import simulate_trades, get_watchlist_symbols, summarize, SEP, THIN_SEP

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

def main():
    parser = argparse.ArgumentParser(description="Study: Bayesian Momentum vs Accumulation")
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--start-date", default="2024-01-01", help="Test period start")
    parser.add_argument("--end-date", default="2026-07-14", help="Test period end")
    args = parser.parse_args()

    print(f"=== Bayesian Strategy Study ===")
    print(f"Watchlist: {args.watchlist}")
    print(f"Period:    {args.start_date} to {args.end_date}")

    symbols = get_watchlist_symbols(args.watchlist)
    if not symbols:
        print("No symbols found in watchlist.")
        return

    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    all_trades = []
    print(f"Simulating trades across {len(symbols)} symbols...", flush=True)
    
    for i, sym in enumerate(symbols):
        try:
            engine = DivergenceEngine(sym)
            result = engine.run()
            
            from src.trading.signals.savgol_cts import get_symbol_entry_config, get_symbol_exit_config
            sym_entry_cfg = get_symbol_entry_config(sym, entry_cfg)
            sym_exit_cfg = get_symbol_exit_config(sym, exit_cfg)
            
            trades = simulate_trades(sym, result.ledger, sym_entry_cfg, sym_exit_cfg, signal)
            
            # Filter trades to the date range
            period_trades = [
                t for t in trades 
                if args.start_date <= str(t.entry_date) <= args.end_date
            ]
            all_trades.extend(period_trades)
            
            if (i + 1) % 10 == 0 or (i + 1) == len(symbols):
                print(f"  Processed {i + 1}/{len(symbols)} symbols...")
        except Exception as e:
            print(f"  Failed {sym}: {repr(e)}")

    print(f"Total trades generated: {len(all_trades)}")

    # Filter to Custom-Bayesian trades only
    bayesian_trades = [
        t for t in all_trades 
        if t.entry_tag == "SavgolCTS Custom-Bayesian"
    ]
    print(f"Total Custom-Bayesian trades: {len(bayesian_trades)}")

    # Segment into Momentum and Accumulation models
    # model=momentum -> regime_at_entry is not downtrend/notrend
    momentum_trades = [
        t for t in bayesian_trades
        if t.regime_at_entry not in ["downtrend", "notrend"]
    ]
    # model=accumulation -> regime_at_entry is downtrend/notrend
    accumulation_trades = [
        t for t in bayesian_trades
        if t.regime_at_entry in ["downtrend", "notrend"]
    ]

    out = io.StringIO()
    def w(line: str = ""):
        out.write(line + "\n")

    w(SEP)
    w(f"  BAYESIAN SUB-MODEL ANALYSIS REPORT")
    w(f"  Watchlist: {args.watchlist} | {args.start_date} to {args.end_date}")
    w(SEP)
    w()
    w(f"  Custom-Bayesian Trades Breakdown:")
    w(f"    - Momentum Model (Uptrend entries):      {len(momentum_trades)} trades")
    w(f"    - Accumulation Model (Non-uptrend):      {len(accumulation_trades)} trades")
    w()

    summarize(momentum_trades, "MOMENTUM MODEL (regime=uptrend)", args.start_date, args.end_date, out)
    summarize(accumulation_trades, "ACCUMULATION MODEL (regime=downtrend/notrend)", args.start_date, args.end_date, out)

    report_content = out.getvalue()
    print(report_content)

    # Save to output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT_DIR / "momentum_bayesian_study.txt"
    with open(report_path, "w") as f:
        f.write(report_content)
    
    print(f"Report saved to {report_path}")

if __name__ == "__main__":
    main()
