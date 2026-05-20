
import sys
import os
import joblib
import pandas as pd
import numpy as np
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.divergence_engine.engine import DivergenceEngine
from scripts.walk_forward import get_watchlist_symbols

def run_calibration_study():
    # Load Model
    model_path = PROJECT_ROOT / "src" / "trading" / "signals" / "savgol_cts" / "models" / "model_clf_20260516.joblib"
    if not model_path.exists():
        print(f"Model not found at {model_path}")
        return
    
    data = joblib.load(model_path)
    model = data['model']
    feature_cols = data['feature_cols']

    symbols = get_watchlist_symbols("NIFTY 50")
    
    print(f"Running probability calibration study for {len(symbols)} symbols...")
    
    results = []
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            df = res.ledger
            if df.empty: continue
            
            # Reconstruct triggers
            df['date'] = pd.to_datetime(df['date'])
            df['trigger_prt'] = (df['prt_slope'] > 0) & (df['prt_slope'].shift(1) <= 0)
            df['trigger_fas'] = (df['fas'] > df['fas_buy_threshold']) & (df['fas'].shift(1) <= df['fas_buy_threshold'])
            df['trigger_cts'] = (df['cts'] > df['cts_buy_threshold']) & (df['cts'].shift(1) <= df['cts_buy_threshold'])
            df['trigger_accel'] = (df['cts_accel'] > df['cts_accel_threshold']) & (df['cts_accel'].shift(1) <= df['cts_accel_threshold'])
            
            # Filter for test period
            df_test = df[df['date'] >= '2024-01-01'].copy()
            
            cross_mask = df_test['trigger_prt'] | df_test['trigger_fas'] | df_test['trigger_cts'] | df_test['trigger_accel']
            cross_events = df_test[cross_mask]
            
            for idx, row in cross_events.iterrows():
                # Inference
                features = pd.DataFrame([row[feature_cols].fillna(0)])
                prob_bad = model.predict_proba(features)[0][1]
                
                # Simplified PnL
                entry_idx = df.index.get_loc(idx) + 1
                if entry_idx >= len(df): continue
                entry_price = df.iloc[entry_idx]['open']
                exit_price = df.iloc[-1]['close']
                pnl = (exit_price / entry_price - 1) * 100
                
                results.append({"prob_bad": prob_bad, "pnl": pnl})
        except:
            continue

    # Analysis
    res_df = pd.DataFrame(results)
    
    # Bin probabilities
    res_df['bin'] = pd.cut(res_df['prob_bad'], bins=np.linspace(0, 1, 21))
    analysis = res_df.groupby('bin', observed=False).agg(
        avg_pnl=('pnl', 'mean'),
        count=('pnl', 'count'),
        win_rate=('pnl', lambda x: (x > 0).mean() * 100)
    )
    
    print("\n--- Probability Calibration Report ---")
    print(analysis.to_string())

if __name__ == "__main__":
    run_calibration_study()
