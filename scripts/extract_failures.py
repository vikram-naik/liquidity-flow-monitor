#!/usr/bin/env python3
"""
Extract all failing (losing) trades from the TEST period of the CDVL/CTS Study
for in-depth failure analysis.
"""

import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.study_cdvl_cts import CDVLCTSSignal, simulate_trades_custom, get_watchlist_symbols, TEST_START
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts import SavgolCTSExitConfig

def main():
    watchlist_name = "NSE F&O"
    print(f"Loading symbols from '{watchlist_name}'...", flush=True)
    symbols = get_watchlist_symbols(watchlist_name)
    
    exit_cfg = SavgolCTSExitConfig()
    signal = CDVLCTSSignal()
    
    all_setups = []
    
    print("Running backtest for TEST period and collecting all setups...", flush=True)
    for sym in symbols:
        try:
            # Always run with full history for warm-up
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            result = engine.run()
            trades = simulate_trades_custom(sym, result.ledger, exit_cfg, signal)
            
            ledger_df = result.ledger.copy()
            ledger_df["date_str"] = ledger_df["date"].astype(str).str[:10]
            
            # Filter for TEST period setups
            for t in trades:
                if str(t.entry_date) >= TEST_START:
                    sig_date_str = str(t.entry_date)[:10]
                    matching = ledger_df[ledger_df["date_str"] == sig_date_str]
                    if not matching.empty:
                        idx = matching.index[0]
                        row_data = ledger_df.loc[idx]
                        
                        cts_val = row_data.get("cts", None)
                        cts_bt_val = row_data.get("cts_buy_threshold", None)
                        rp10_val = row_data.get("range_pos_10", None)
                        rp22_val = row_data.get("range_pos_22", None)
                        rp63_val = row_data.get("range_pos_63", None)
                        rp252_val = row_data.get("range_pos_252", None)
                        
                        cts_accel = row_data.get("cts_accel", None)
                        cts_accel_thr = row_data.get("cts_accel_threshold", None)
                        cwc_val = row_data.get("cwc", None)
                        
                        if idx > 0:
                            prev_cts_accel = ledger_df.loc[idx - 1].get("cts_accel", None)
                        else:
                            prev_cts_accel = None
                            
                        if cts_accel is not None and prev_cts_accel is not None and not pd.isna(cts_accel) and not pd.isna(prev_cts_accel):
                            accel_rising = "yes" if float(cts_accel) > float(prev_cts_accel) else "no"
                        else:
                            accel_rising = "no"
                    else:
                        cts_val = None
                        cts_bt_val = None
                        rp10_val = None
                        rp22_val = None
                        rp63_val = None
                        rp252_val = None
                        cts_accel = None
                        cts_accel_thr = None
                        cwc_val = None
                        accel_rising = "no"

                    all_setups.append({
                        "symbol": t.symbol,
                        "entry_date": t.entry_date,
                        "cts": round(float(cts_val), 6) if cts_val is not None and not pd.isna(cts_val) else None,
                        "cts_buy_threshold": round(float(cts_bt_val), 6) if cts_bt_val is not None and not pd.isna(cts_bt_val) else None,
                        "cts_accel": round(float(cts_accel), 6) if cts_accel is not None and not pd.isna(cts_accel) else None,
                        "cts_accel_threshold": round(float(cts_accel_thr), 6) if cts_accel_thr is not None and not pd.isna(cts_accel_thr) else None,
                        "cts_accel_rising": accel_rising,
                        "cwc": round(float(cwc_val), 6) if cwc_val is not None and not pd.isna(cwc_val) else None,
                        "range_pos_10": round(float(rp10_val), 4) if rp10_val is not None and not pd.isna(rp10_val) else None,
                        "range_pos_22": round(float(rp22_val), 4) if rp22_val is not None and not pd.isna(rp22_val) else None,
                        "range_pos_63": round(float(rp63_val), 4) if rp63_val is not None and not pd.isna(rp63_val) else None,
                        "range_pos_252": round(float(rp252_val), 4) if rp252_val is not None and not pd.isna(rp252_val) else None,
                        "pnl_pct": t.pnl_pct,
                        "mfe_pct": t.mfe_pct,
                        "mae_pct": t.mae_pct,
                        "duration": t.duration,
                        "exit_reason": t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason),
                    })
        except Exception as e:
            print(f"Failed {sym}: {repr(e)}")
            
    # Convert to DataFrame
    df = pd.DataFrame(all_setups)
    
    if df.empty:
        print("No setups found in the TEST period!")
        return
        
    # Sort chronologically by entry date, then by symbol
    df = df.sort_values(by=["entry_date", "symbol"], ascending=[True, True])
    
    # Save to CSV
    output_dir = Path(__file__).resolve().parent.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "cdvl_cts_test_setups.csv"
    df.to_csv(csv_path, index=False)
    print(f"\nSuccessfully extracted {len(df)} setups.")
    print(f"CSV saved to: {csv_path}")
    
    # Generate a markdown table preview of the first 50 setups
    md_preview_path = output_dir / "cdvl_cts_test_setups_preview.md"
    preview_df = df.head(50)
    
    with open(md_preview_path, "w") as f:
        f.write("# CDVL/CTS Study - TEST Period All Setups Preview (First 50 setups)\n\n")
        f.write(preview_df.to_markdown(index=False))
        f.write("\n")
        
    print(f"Markdown preview saved to: {md_preview_path}")

if __name__ == "__main__":
    main()
