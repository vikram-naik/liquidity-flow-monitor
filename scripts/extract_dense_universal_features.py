"""
Dense Feature Extraction for the Universal ML Model.

Unlike standard simulation, this script DOES NOT use the `in_trade` blocking logic.
It captures *every single instance* of a PRT, FAS, CTS, or Accel cross, spawning
overlapping Virtual Trades to capture maximum dataset variance without signal shadowing.

All generated setups are resolved using the standardized Universal-Cross exit logic
to ensure a consistent target variable (PnL) for the XGBoost model.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.divergence_engine.engine import DivergenceEngine
from scripts.walk_forward import get_watchlist_symbols, today_str
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag, ExitReason

EXCLUDE_COLS = [
    "date", "symbol", "regime", "gradient_shape", 
    "oracle_trough", "oracle_peak", "oracle_smooth",
    "entry_signal", "entry_reason", "exit_signal", "exit_reason", "cooldown"
]

class VirtualTrade:
    def __init__(self, symbol: str, entry_idx: int, entry_price: float, sig_row: dict, triggers: dict):
        self.symbol = symbol
        self.entry_idx = entry_idx
        self.entry_price = entry_price
        self.sig_row = sig_row
        self.triggers = triggers  # dict of boolean triggers (e.g. {'trigger_prt': 1, ...})
        
        # Standard tracking variables
        self.mfe_pct = 0.0
        self.mae_pct = 0.0
        self.peak_close = entry_price
        self.delivery_bad_count = 0
        
        # Outcome
        self.closed = False
        self.pnl_pct = 0.0

def main():
    parser = argparse.ArgumentParser(description="Dense feature extraction for Universal ML Model.")
    parser.add_argument("--watchlist", type=str, default="NIFTY 50", help="Watchlist to process.")
    parser.add_argument("--threshold", type=float, default=4.0, help="PnL threshold for Good Trade (1). Default: 4.0%")
    args = parser.parse_args()

    watchlist = args.watchlist
    threshold = args.threshold
    print(f"Fetching symbols for {watchlist}...")
    symbols = get_watchlist_symbols(watchlist)
    
    # We only need the signal logic for check_exit
    signal = SignalFactory.get_signal("savgol_cts")
    
    # Standardize ALL exits to Universal-Cross logic (Pure CTS Trailing + CWVAP + Hard Stop)
    exit_cfg = SavgolCTSExitConfig()
    
    start_date = "2019-01-01"
    end_date = today_str()

    all_features = []
    
    print(f"Running dense extraction (Threshold: {threshold}%) capturing overlapping crosses...")

    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            df = res.ledger
            
            if df is None or df.empty:
                continue
                
            records = df.to_dict("records")
            n = len(records)
            cwvap_values = []
            
            active_trades: list[VirtualTrade] = []
            completed_trades: list[VirtualTrade] = []

            for i in range(1, n):
                row = records[i]
                prev = records[i - 1]
                close = row.get("close", np.nan)
                
                if np.isnan(close):
                    continue
                    
                date_str = str(row.get("date", ""))[:10]
                cwvap_values.append(row.get("cwvap", np.nan))
                
                # We only extract data generated within the allowed timeframe
                in_period = start_date <= date_str <= end_date
                
                # 1. Evaluate Active Virtual Trades
                for vt in active_trades:
                    if close > vt.peak_close:
                        vt.peak_close = close

                    bars_held = i - vt.entry_idx
                    mfe = max(vt.mfe_pct, (close / vt.entry_price - 1) * 100)
                    mae_val = (close / vt.entry_price - 1) * 100
                    mae = min(-vt.mae_pct, mae_val)
                    vt.mfe_pct = mfe
                    if -mae > vt.mae_pct:
                        vt.mae_pct = -mae

                    # We must mock a standard Trade object for the signal logic to consume
                    mock_trade = Trade(
                        symbol=vt.symbol,
                        entry_date="",
                        entry_price=vt.entry_price,
                        entry_idx=vt.entry_idx,
                        atr_at_entry=0.0,
                        soft_filters_passed=0,
                        entry_tag=EntryTag.UNIVERSAL_CROSS.value,
                        mfe_pct=vt.mfe_pct,
                        mae_pct=vt.mae_pct
                    )

                    reason, new_dbc = signal.check_exit(
                        row, prev, mock_trade, vt.peak_close, bars_held,
                        vt.delivery_bad_count, cwvap_values, exit_cfg,
                        records, i
                    )
                    
                    vt.delivery_bad_count = new_dbc

                    if reason:
                        # Trade closes on the NEXT open (EOD-Lag simulation)
                        # For dense extraction, we approximate next open using current close to simplify tracking,
                        # or if we are at end of data we just use close.
                        # Real simulator uses next bar open, we'll use current close as approximation for the ML label
                        # since it's the bar the decision was made.
                        vt.pnl_pct = (close / vt.entry_price - 1) * 100
                        vt.closed = True
                
                # Move closed trades out
                completed_trades.extend([t for t in active_trades if t.closed])
                active_trades = [t for t in active_trades if not t.closed]

                # 2. Scan for New Crosses (Signal Evaluation)
                if in_period and i < n - 1:
                    # Signals happen on bar i, execution on bar i+1
                    sig_row = row
                    sig_prev = prev
                    
                    # PRT Cross
                    prev_prt = sig_prev.get("prt_slope", np.nan)
                    prt = sig_row.get("prt_slope", np.nan)
                    trigger_prt = 1 if (not np.isnan(prev_prt) and prev_prt <= 0 and prt > 0) else 0
                    
                    # FAS Cross
                    fas_bt = sig_row.get("fas_buy_threshold", np.nan)
                    prev_fas = sig_prev.get("fas", np.nan)
                    fas = sig_row.get("fas", np.nan)
                    trigger_fas = 1 if (not any(np.isnan([prev_fas, fas, fas_bt])) and prev_fas <= fas_bt and fas > fas_bt) else 0
                    
                    # CTS Cross
                    cts_bt = sig_row.get("cts_buy_threshold", np.nan)
                    prev_cts = sig_prev.get("cts", np.nan)
                    cts = sig_row.get("cts", np.nan)
                    trigger_cts = 1 if (not any(np.isnan([prev_cts, cts, cts_bt])) and prev_cts <= cts_bt and cts > cts_bt) else 0
                    
                    # Accel Cross
                    accel_bt = sig_row.get("cts_accel_threshold", np.nan)
                    prev_accel = sig_prev.get("cts_accel", np.nan)
                    accel = sig_row.get("cts_accel", np.nan)
                    trigger_accel = 1 if (not any(np.isnan([prev_accel, accel, accel_bt])) and prev_accel <= accel_bt and accel > accel_bt) else 0
                    
                    if any([trigger_prt, trigger_fas, trigger_cts, trigger_accel]):
                        # Entry happens on NEXT bar (i+1)
                        next_row = records[i+1]
                        entry_price = next_row.get("open", np.nan)
                        if np.isnan(entry_price):
                            entry_price = next_row.get("close", np.nan)
                            
                        if not np.isnan(entry_price):
                            triggers = {
                                "trigger_prt": trigger_prt,
                                "trigger_fas": trigger_fas,
                                "trigger_cts": trigger_cts,
                                "trigger_accel": trigger_accel
                            }
                            vt = VirtualTrade(sym, i+1, entry_price, sig_row, triggers)
                            active_trades.append(vt)

            # EOD Cleanup for open trades
            for vt in active_trades:
                vt.pnl_pct = (records[-1].get("close", vt.entry_price) / vt.entry_price - 1) * 100
                completed_trades.append(vt)

            # Convert completed trades to feature rows
            for vt in completed_trades:
                label = 1 if vt.pnl_pct >= threshold else 0
                
                feature_row = {
                    "symbol": vt.symbol,
                    "date": str(vt.sig_row.get("date", ""))[:10],
                    "pnl_pct": round(vt.pnl_pct, 2),
                    "mfe_pct": round(vt.mfe_pct, 2),
                    "label": label,
                }
                
                # Add triggers
                feature_row.update(vt.triggers)
                
                # Add technical features
                for col in df.columns:
                    if col not in EXCLUDE_COLS and col != 'date_str' and pd.api.types.is_numeric_dtype(df[col]):
                        feature_row[col] = vt.sig_row[col]
                        
                all_features.append(feature_row)

            print(f"  {sym}: Generated {len(completed_trades)} dense setups")

        except Exception as e:
            print(f"  Failed {sym}: {e}")

    if all_features:
        out_df = pd.DataFrame(all_features)
        out_dir = PROJECT_ROOT / "output" / "ml"
        out_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        out_path = out_dir / f"dataset_dense_{date_str}.csv"
        out_df.to_csv(out_path, index=False)
        
        print(f"\nExtraction complete! Dataset saved to {out_path}")
        print(f"Total labeled setups: {len(out_df)}")
        print(f"Class Balance - Good Trades (1): {out_df['label'].sum()}, Bad Trades (0): {len(out_df) - out_df['label'].sum()}")
        
        print("\nTrigger Distribution:")
        print(f"  Universal Inflection (PRT): {out_df['trigger_prt'].sum()}")
        print(f"  Universal Inflection (FAS): {out_df['trigger_fas'].sum()}")
        print(f"  Universal Inflection (CTS): {out_df['trigger_cts'].sum()}")
        print(f"  Universal Inflection (Accel): {out_df['trigger_accel'].sum()}")
    else:
        print("\nNo setups were generated.")

if __name__ == "__main__":
    main()
