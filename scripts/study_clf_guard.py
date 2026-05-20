
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
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag

def run_study():
    # Load Model
    model_path = PROJECT_ROOT / "src" / "trading" / "signals" / "savgol_cts" / "models" / "model_clf_20260516.joblib"
    if not model_path.exists():
        print(f"Model not found at {model_path}")
        return
    
    data = joblib.load(model_path)
    model = data['model']
    feature_cols = data['feature_cols']

    symbols = get_watchlist_symbols("NIFTY 50")
    start_date = "2026-01-01"
    
    signal = SignalFactory.get_signal("savgol_cts")
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    entry_cfg.universal_cross.enabled = True
    entry_cfg.universal_cross.min_ml_score = 0.0

    print(f"Running simulation from {start_date} for {len(symbols)} symbols...")
    
    results = []

    for sym in symbols:
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            df = res.ledger
            if df.empty: continue
            
            # Find cross events
            # Reconstruct triggers matching dense extraction logic
            df['trigger_prt'] = (df['prt_slope'] > 0) & (df['prt_slope'].shift(1) <= 0)
            df['trigger_fas'] = (df['fas'] > df['fas_buy_threshold']) & (df['fas'].shift(1) <= df['fas_buy_threshold'])
            df['trigger_cts'] = (df['cts'] > df['cts_buy_threshold']) & (df['cts'].shift(1) <= df['cts_buy_threshold'])
            df['trigger_accel'] = (df['cts_accel'] > df['cts_accel_threshold']) & (df['cts_accel'].shift(1) <= df['cts_accel_threshold'])
            
            cross_mask = df['trigger_prt'] | df['trigger_fas'] | df['trigger_cts'] | df['trigger_accel']
            cross_events = df[cross_mask & (df['date'].astype(str) >= start_date)]
            
            for idx, row in cross_events.iterrows():
                # Inference
                features = pd.DataFrame([row[feature_cols].fillna(0)])
                is_bad = model.predict(features)[0]
                
                # Simulate simple trade
                entry_idx = df.index.get_loc(idx) + 1
                if entry_idx >= len(df): continue
                
                entry_price = df.iloc[entry_idx]['open']
                exit_price = df.iloc[-1]['close'] # Simplified exit
                pnl = (exit_price / entry_price - 1) * 100
                
                results.append({
                    "symbol": sym,
                    "is_blocked": bool(is_bad),
                    "pnl": pnl
                })
        except Exception as e:
            print(f"Failed {sym}: {e}")

    # Analysis
    if not results:
        print("No setups found.")
        return
        
    res_df = pd.DataFrame(results)
    blocked = res_df[res_df['is_blocked']]
    taken = res_df[~res_df['is_blocked']]
    
    print("\n--- ML Guard Performance (Avoidance Filter) ---")
    print(f"Total setups found: {len(res_df)}")
    print(f"Setups blocked by Guard: {len(blocked)} ({len(blocked)/len(res_df)*100:.1f}%)")
    
    print(f"\nBlocked Setups Avg PnL: {blocked['pnl'].mean():.2f}%")
    print(f"Taken Setups Avg PnL:    {taken['pnl'].mean():.2f}%")

if __name__ == "__main__":
    run_study()
