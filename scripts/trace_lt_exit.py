#!/usr/bin/env python3
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade, SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState
from src.trading.signals.savgol_cts.symbol_configs import get_symbol_entry_config, get_symbol_exit_config
from src.trading.signals.enums import ExitReason, EntryTag
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross
from src.trading.signals.savgol_cts.exits.cwvap_guard import apply_cwvap_guard
from scripts.test_expert_v5 import check_exit_expert_v5

def main():
    ticker = "LT"
    entry_date = "2026-01-27"
    
    engine = DivergenceEngine(ticker, start_date=None, end_date=None)
    result = engine.run()
    ledger = result.ledger
    
    ledger["date_str"] = ledger["date"].astype(str).str[:10]
    records = ledger.to_dict("records")
    
    # Find entry index
    entry_idx = -1
    for idx, r in enumerate(records):
        if r["date_str"] == entry_date:
            entry_idx = idx
            break
            
    if entry_idx == -1:
        print("Entry date not found!")
        return
        
    exit_cfg = get_symbol_exit_config(ticker)
    
    # Initialize trade
    trade = Trade(
        symbol=ticker,
        entry_date=entry_date,
        entry_price=records[entry_idx]["close"],
        entry_idx=entry_idx,
        atr_at_entry=records[entry_idx]["atr_20"],
        conviction_score=0,
        regime_at_entry="downtrend",
        entry_tag="universal_cross"
    )
    
    params = {
        "peak_pnl_trigger": 6.0,
        "uptrend_atr_mult": 2.5,
        "normal_atr_mult": 2.0,
        "uptrend_cwc_min": 0.10,
        "uptrend_cwc_slope_min": -0.06,
        "normal_cwc_min": 0.25,
        "normal_cwc_slope_min": -0.04,
        "normal_psz_v_min": -0.2,
        "overextended_rp_threshold": 0.90,
        "uptrend_low_break_buffer_atr": 0.20,
        "normal_rp_reversion": 0.70
    }
    
    peak_close = trade.entry_price
    delivery_bad_count = 0
    cwvap_values = []
    
    print(f"Tracing LT trade from {entry_date}:")
    print(f"Entry Price: {trade.entry_price}")
    
    for i in range(entry_idx + 1, len(records)):
        row = records[i]
        prev = records[i - 1]
        close = row["close"]
        if np.isnan(close):
            continue
            
        cwvap_values.append(row["cwvap"])
        if close > peak_close:
            peak_close = close
            
        bars_held = i - trade.entry_idx
        trade.mfe_pct = max(trade.mfe_pct, (close / trade.entry_price - 1) * 100)
        
        # Apply Reclaim PRT check
        st = SavgolCTSExitState.from_int(delivery_bad_count)
        if st.exit_suppressed:
            prt = row.get("prt", np.nan)
            prt_st = row.get("prt_sell_threshold", np.nan)
            if not any(np.isnan(x) for x in [prt, prt_st]) and prt >= prt_st:
                st.exit_suppressed = False
                st.suppressed_this_bar = False
                delivery_bad_count = st.to_int()
                print(f"[{row['date_str']}] Suppression RECLAIMED! (prt={prt:.4f} >= threshold={prt_st:.4f})")
                
        reason, delivery_bad_count = check_exit_expert_v5(
            row, prev, trade, peak_close, bars_held,
            delivery_bad_count, cwvap_values, exit_cfg,
            records, i, params
        )
        
        st_after = SavgolCTSExitState.from_int(delivery_bad_count)
        
        if reason:
            print(f"[{row['date_str']}] EXIT TRIGGERED! Reason: {reason}, PnL: {(close/trade.entry_price-1)*100:+.2f}%, close: {close}, peak: {peak_close}, state: {st_after.to_int()}")
            break
        else:
            print(f"[{row['date_str']}] Held. close: {close}, peak: {peak_close}, state: {st_after.to_int()}")

if __name__ == "__main__":
    main()
