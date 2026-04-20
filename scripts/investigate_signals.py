import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine

def dump(sym, start, end):
    print(f"\n--- {sym} ---")
    try:
        df = DivergenceEngine(sym).run().ledger
        df['date_str'] = df['date'].dt.strftime('%Y-%m-%d')
        mask = (df['date_str'] >= start) & (df['date_str'] <= end)
        cols = ['date_str', 'close', 'cts', 'fas', 'price_slope_z', 'psz_v', 'coherence', 'regime', 'dist_high_252']
        print(df[mask][cols].to_string(index=False))
    except: pass

dump("JIOFIN", "2024-01-24", "2024-02-05")
dump("SUNPHARMA", "2023-10-25", "2023-11-10")
dump("BAJAJFINSV", "2026-01-25", "2026-02-05")
