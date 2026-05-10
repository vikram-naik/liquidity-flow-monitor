"""
Extracts features by running each signal entry path in isolation (siloed) over historical data.
Captures the state of the indicators at the signal bar and the resulting PnL from actual system exits.
"""

from __future__ import annotations

import sys
import argparse
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.divergence_engine.engine import DivergenceEngine
from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag

EXCLUDE_COLS = [
    "date", "symbol", "regime", "gradient_shape", 
    "oracle_trough", "oracle_peak", "oracle_smooth",
    "entry_signal", "entry_reason", "exit_signal", "exit_reason", "cooldown"
]

def main():
    parser = argparse.ArgumentParser(description="Extract features from historical trades for ML training.")
    parser.add_argument("--watchlist", type=str, default="NIFTY 100", help="Watchlist to run backtests on.")
    parser.add_argument("--threshold", type=float, default=2.5, help="PnL threshold to label a trade as 'Good' (1). Default: 2.5%")
    args = parser.parse_args()

    watchlist = args.watchlist
    threshold = args.threshold
    print(f"Fetching symbols for {watchlist}...")
    symbols = get_watchlist_symbols(watchlist)
    
    signal = SignalFactory.get_signal("savgol_cts")
    start_date = "2019-01-01"
    end_date = today_str()

    all_features = []

    # Map of all paths in SavgolCTSEntryConfig
    entry_paths = [
        "universal_cross"
    ]

    print(f"Running siloed backtests (Threshold: {threshold}%) for each entry path to generate dataset...")

    for target_path in entry_paths:
        print(f"\n--- Processing path: {target_path} ---")
        
        entry_cfg = SavgolCTSEntryConfig()
        exit_cfg = SavgolCTSExitConfig()

        # Disable all paths (Only one now)
        for path in entry_paths:
            getattr(entry_cfg, path).enabled = False
            
        # Enable ONLY the target path
        target_cfg = getattr(entry_cfg, target_path)
        target_cfg.enabled = True
        
        # Disable ML Guard to capture raw mechanical setups (it will still trigger on inflections)
        target_cfg.min_ml_score = 0.0

        # Run backtest
        print(f"Running backtest for {target_path}...")
        trades = run_period(symbols, start_date, end_date, entry_cfg, exit_cfg, "ALL", signal)
        print(f"Generated {len(trades)} trades.")

        if not trades:
            continue

        # Group trades by symbol for efficient ledger fetching
        trades_by_sym = {}
        for t in trades:
            trades_by_sym.setdefault(t.symbol, []).append(t)

        for sym, sym_trades in trades_by_sym.items():
            try:
                engine = DivergenceEngine(sym)
                res = engine.run()
                df = res.ledger
                
                if df is None or df.empty:
                    continue
                    
                df['date_str'] = df['date'].astype(str).str[:10]
                
                for t in sym_trades:
                    # Find entry date in ledger
                    entry_matches = df[df['date_str'] == t.entry_date]
                    if entry_matches.empty:
                        continue
                        
                    entry_idx = entry_matches.index[0]
                    signal_idx = entry_idx - 1
                    
                    if signal_idx < 0:
                        continue
                        
                    sig_row = df.loc[signal_idx]
                    
                    # We have our trade and our feature row
                    pnl_pct = t.pnl_pct
                    
                    # Labeled based on CLI threshold
                    label = 1 if pnl_pct >= threshold else 0
                    
                    feature_row = {
                        "symbol": sym,
                        "date": str(sig_row["date"])[:10],
                        "entry_path": target_path,
                        "pnl_pct": round(pnl_pct, 2),
                        "mfe_pct": round(t.mfe_pct, 2) if hasattr(t, 'mfe_pct') else 0.0,
                        "label": label,
                    }
                    
                    for col in df.columns:
                        if col not in EXCLUDE_COLS and col != 'date_str' and pd.api.types.is_numeric_dtype(df[col]):
                            feature_row[col] = sig_row[col]
                            
                    all_features.append(feature_row)

            except Exception as e:
                # Silently skip errors for individual stocks to keep moving
                pass

    if all_features:
        out_df = pd.DataFrame(all_features)
        out_dir = PROJECT_ROOT / "output" / "ml"
        out_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        out_path = out_dir / f"dataset_trade_{date_str}.csv"
        out_df.to_csv(out_path, index=False)
        
        print(f"\nExtraction complete! Dataset saved to {out_path}")
        print(f"Total labeled trades: {len(out_df)}")
        print(f"Class Balance - Good Trades (1): {out_df['label'].sum()}, Bad Trades (0): {len(out_df) - out_df['label'].sum()}")
    else:
        print("\nNo trades were generated across any paths.")

if __name__ == "__main__":
    main()
