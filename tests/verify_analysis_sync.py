import pandas as pd
import numpy as np
import sys
import os

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.divergence_engine.analysis import compute_trend_participation

def test_real_engine_data():
    print("Testing with real engine data (BHARTIARTL)...")
    from src.divergence_engine.engine import DivergenceEngine
    
    engine = DivergenceEngine("BHARTIARTL")
    # Execute full pipeline
    results = engine.run()
    
    df = results.ledger
    
    if df is None or df.empty:
        print("ℹ️ No data returned from engine.")
        return

    # Run the updated analysis logic
    results = compute_trend_participation(df)
    
    # Check for promoted signals
    promoted = results[
        ((results['regime'] == "ACCUMULATION") & (results['cps_flag'] == "STEALTH_ACCUM")) |
        ((results['regime'] == "DISTRIBUTION") & (results['cps_flag'] == "STEALTH_DIST"))
    ]
    
    if len(promoted) > 0:
        print(f"✅ SUCCESS: Found {len(promoted)} promoted stealth signals.")
        print(promoted[['date', 'regime', 'cps_flag', 'regime_conf', 'cps_conf']].tail(10))
    else:
        # If no promotion, check if there were any stealth signals at all
        stealths = results[results['cps_flag'] != ""]
        if len(stealths) > 0:
            print(f"ℹ️ Found {len(stealths)} stealth signals, but none were in NEUTRAL regime for promotion.")
            print(stealths[['date', 'regime', 'cps_flag']].tail(5))
        else:
            print("ℹ️ No stealth signals found in the data.")

if __name__ == "__main__":
    try:
        test_real_engine_data()
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
