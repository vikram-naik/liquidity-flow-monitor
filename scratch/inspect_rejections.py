import re
import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from scratch.analyze_trends import parse_trends, clean_date_str

def main():
    trends = parse_trends()
    signal = SignalFactory.get_signal("savgol_cts")
    entry_cfg = SavgolCTSEntryConfig()
    
    # We want to check all active paths:
    # 1. cdvl_cts
    # 2. universal_cross
    # 3. trend_pullback
    # 4. flow_momentum
    # 5. coherent_pullback
    # 6. anchor_shock_pullback
    
    # Let's inspect the target dates and a 15-day window after them.
    for symbol, raw_date in trends:
        if symbol == "ASIANPAINTS":
            symbol = "ASIANPAINT"
        target_date = clean_date_str(raw_date)
        
        try:
            engine = DivergenceEngine(symbol)
            res = engine.run()
            df = res.ledger
            df['date'] = pd.to_datetime(df['date'])
        except Exception as e:
            continue
            
        match_df = df[df['date'] <= target_date]
        if match_df.empty:
            continue
        idx = match_df.index[-1]
        
        start_idx = max(0, idx - 2)
        end_idx = min(len(df) - 1, idx + 15)
        
        print(f"\n======================================================================")
        print(f"REJECTION LOG: {symbol} | Target Date: {target_date.strftime('%Y-%m-%d')} (idx {idx})")
        print(f"======================================================================")
        
        records = df.to_dict('records')
        for i in range(start_idx, end_idx + 1):
            row = records[i]
            prev_row = records[i-1] if i > 0 else row
            date_str = row['date'].strftime('%Y-%m-%d')
            close = row['close']
            
            # Run entry checks and print details if any path was close or why it failed
            # We want to print if a path passes, or print the reason it failed.
            passed, intensity, meta = signal.check_entry(row, prev_row, entry_cfg, records, i)
            prefix = "-->" if i == idx else "   "
            if passed:
                print(f"  {prefix} {date_str} (idx {i}): SUCCESS! {meta.get('entry_tag')} | Reason: {meta.get('reason')} | Close: {close:.2f}")
            else:
                # Let's inspect individual paths to see their rejection reasons
                rejections = []
                
                # 1. cdvl_cts
                from src.trading.signals.savgol_cts.entries.cdvl_cts import entry_cdvl_cts
                p_cdvl, _, m_cdvl = entry_cdvl_cts(row, prev_row, entry_cfg, records, i)
                if not p_cdvl: rejections.append(f"CDVL: {m_cdvl.get('reason')}")
                
                # 2. universal_cross
                from src.trading.signals.savgol_cts.entries.universal_cross import entry_universal_cross
                p_uc, _, m_uc = entry_universal_cross(row, prev_row, entry_cfg, records, i)
                if not p_uc: rejections.append(f"UC: {m_uc.get('reason')}")
                
                # 3. trend_pullback
                from src.trading.signals.savgol_cts.entries.trend_pullback import entry_trend_pullback
                p_tp, _, m_tp = entry_trend_pullback(row, prev_row, entry_cfg, records, i)
                if not p_tp: rejections.append(f"TP: {m_tp.get('reason')}")
                
                # 4. flow_momentum
                from src.trading.signals.savgol_cts.entries.flow_momentum import entry_flow_momentum
                p_fm, _, m_fm = entry_flow_momentum(row, prev_row, entry_cfg, records, i)
                if not p_fm: rejections.append(f"FM: {m_fm.get('reason')}")
                
                # 5. coherent_pullback
                from src.trading.signals.savgol_cts.entries.coherent_pullback import entry_coherent_pullback
                p_cp, _, m_cp = entry_coherent_pullback(row, prev_row, entry_cfg, records, i)
                if not p_cp: rejections.append(f"CP: {m_cp.get('reason')}")
                
                # 6. anchor_shock_pullback
                from src.trading.signals.savgol_cts.entries.anchor_shock_pullback import entry_anchor_shock_pullback
                p_asp, _, m_asp = entry_anchor_shock_pullback(row, prev_row, entry_cfg, records, i)
                if not p_asp: rejections.append(f"ASP: {m_asp.get('reason')}")
                
                print(f"  {prefix} {date_str} (idx {i}): REJECT | Close: {close:.2f}")
                for rej in rejections:
                    print(f"       * {rej}")

if __name__ == "__main__":
    main()
