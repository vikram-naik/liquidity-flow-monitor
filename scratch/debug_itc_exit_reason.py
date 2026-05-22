import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross
from src.trading.signals.savgol_cts.exits.cwvap_guard import apply_cwvap_guard
from src.trading.signals import Trade
from src.trading.signals.enums import EntryTag

def main():
    sym = "ITC"
    engine = DivergenceEngine(sym)
    df = engine.run().ledger
    
    df['date_dt'] = pd.to_datetime(df['date'])
    mask = (df['date_dt'] >= '2025-11-18') & (df['date_dt'] <= '2026-02-15')
    df_filtered = df.loc[mask].copy()
    records = df.to_dict('records')
    
    # Locate indices
    idx_map = {str(r['date'])[:10]: i for i, r in enumerate(records)}
    
    trade = Trade(
        symbol="ITC", entry_date="2025-11-18", entry_price=405.85,
        entry_idx=idx_map["2025-11-18"], atr_at_entry=8.0,
        entry_tag=EntryTag.UNIVERSAL_CROSS.value
    )
    
    cfg = SavgolCTSExitConfig()
    
    state_val = 0
    peak_close = 405.85
    bars_held = 0
    
    print("Date       | Close  | Proposed Exit       | CWVAP Guard Exit    | Exit Suppressed? | Suppressed This Bar?")
    print("-" * 115)
    
    # We walk starting from index of 2025-11-18 (entry was open of 18, exit check starts on 19)
    start_idx = idx_map["2025-11-18"] + 1
    end_idx = idx_map["2026-02-13"] + 1
    
    for idx in range(start_idx, end_idx):
        row = records[idx]
        prev_row = records[idx - 1]
        dt_str = str(row['date'])[:10]
        
        close = row.get("close", np.nan)
        if close > peak_close:
            peak_close = close
            
        bars_held += 1
        
        # Path specific exit check
        res, state_val = exit_universal_cross(
            row, prev_row, trade, peak_close, bars_held, state_val, cfg.universal_cross, records, idx
        )
        
        # Apply CWVAP guard
        final_res, state_val = apply_cwvap_guard(
            row, trade, res, state_val, cfg, records, idx, tag=EntryTag.UNIVERSAL_CROSS.value
        )
        
        from src.trading.signals.savgol_cts.state import SavgolCTSExitState
        st = SavgolCTSExitState.from_int(state_val)
        
        print(f"{dt_str} | {close:6.2f} | {str(res):19} | {str(final_res):19} | {str(st.exit_suppressed):16} | {str(st.suppressed_this_bar)}")
        
        if final_res is not None:
            print(f"==> EXIT TRIGGERED ON {dt_str} DUE TO {final_res}")
            # Reset state for demonstration/continuation
            state_val = 0

if __name__ == "__main__":
    main()
