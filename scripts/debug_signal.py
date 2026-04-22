import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.signal import SavgolCTSSignal
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig

def debug_entry(sym, date):
    start_date = pd.to_datetime(date) - pd.Timedelta(days=200)
    end_date = pd.to_datetime(date) + pd.Timedelta(days=5)
    engine = DivergenceEngine(sym, start_date=start_date.strftime("%Y-%m-%d"), end_date=end_date.strftime("%Y-%m-%d"))
    df = engine.run().ledger
    
    signal = SavgolCTSSignal()
    cfg = SavgolCTSEntryConfig()
    
    df['date_str'] = df['date'].dt.strftime('%Y-%m-%d')
    idx = df[df['date_str'] == date].index
    if idx.empty:
        print(f"Date {date} not found for {sym}")
        return
    
    i = idx[0]
    row = df.iloc[i].to_dict()
    prev = df.iloc[i-1].to_dict()
    
    print(f"--- Debugging {sym} on {date} ---")
    print(f"CTS: {row.get('cts')}, CTS_BT: {row.get('cts_buy_threshold')}")
    print(f"CTS Accel: {row.get('cts_accel')}, Accel Threshold: {row.get('cts_accel_threshold')}")
    print(f"PSZ V: {row.get('psz_v')}, Prev PSZ V: {prev.get('psz_v')}")
    ok, intensity, meta = signal.check_entry(row, prev, cfg, df.to_dict('records'), i)
    print(f"Result: {ok}, Intensity: {intensity}, Meta: {meta}")

debug_entry("TRENT", "2025-01-31")
