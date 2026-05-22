import sys
import shutil
import pandas as pd
from pathlib import Path

# Add project root
sys.path.insert(0, '/home/vn/python-projects/liquidity-flow-monitor')

from scratch.study_state_based_bypass import modify_entry_file, backup_file, restore_file
from src.divergence_engine.engine import DivergenceEngine

backup_file()
try:
    modify_entry_file(0.02)
    
    # Let's load the engine data manually
    engine = DivergenceEngine(ticker="APOLLOHOSP")
    result = engine.run()
    df = result.ledger
    df['date_str'] = df['date'].astype(str).str[:10]
    matches = df[df['date_str'] == '2026-01-30']
    
    idx = matches.index[0]
    records = df.to_dict('records')
    row = records[idx]
    prev_row = records[idx - 1]
    
    # Print the values inside row/prev_row
    fas = row.get("fas", 0)
    fas_bt = row.get("fas_buy_threshold", -0.8)
    psz_v = row.get("psz_v", 0)
    
    print("\n--- Telemetry print inside test script ---")
    print(f"fas: {fas} (type: {type(fas)})")
    print(f"fas_bt: {fas_bt} (type: {type(fas_bt)})")
    print(f"psz_v: {psz_v} (type: {type(psz_v)})")
    print(f"fas > fas_bt: {fas > fas_bt}")
    print(f"psz_v > 0.02: {psz_v > 0.02}")
    
    # Import the modified function
    # Note: to reload the module after we write it, we need to import or reload
    import importlib
    import src.trading.signals.savgol_cts.entries.universal_cross as uc
    importlib.reload(uc)
    
    from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.min_ml_score = 85.0
    
    passed, intensity, meta = uc.entry_universal_cross(
        row=row,
        prev_row=prev_row,
        cfg=cfg,
        records=records,
        idx=idx
    )
    
    print("\n--- Running entry_universal_cross ---")
    print(f"Passed: {passed}")
    print(f"Meta: {meta}")
    
finally:
    restore_file()
