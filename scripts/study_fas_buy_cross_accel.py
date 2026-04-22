import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag

def main():
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    symbols = get_watchlist_symbols("NIFTY 50")
    
    print("Running TRAIN and TEST periods to collect fas-buy-cross trades...")
    train_trades = run_period(symbols, "2019-01-01", "2023-12-31", entry_cfg, exit_cfg, "TRAIN", signal)
    test_trades = run_period(symbols, "2024-01-01", today_str(), entry_cfg, exit_cfg, "TEST", signal)
    
    all_trades = [t for t in (train_trades + test_trades) if t.entry_tag == EntryTag.FAS_BUY_CROSS.value]
    
    print(f"\nFound {len(all_trades)} FAS_BUY_CROSS trades. Extracting cts_accel details...")
    
    results = []
    
    by_sym = {}
    for t in all_trades:
        by_sym.setdefault(t.symbol, []).append(t)
        
    for sym, trades in by_sym.items():
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            ledger = res.ledger
            if ledger is None or ledger.empty: continue
            
            ledger['date_str'] = ledger['date'].astype(str).str[:10]
            
            for t in trades:
                matches = ledger[ledger['date_str'] == t.entry_date]
                if matches.empty: continue
                
                entry_idx = matches.index[0]
                sig_idx = entry_idx - 1
                
                if sig_idx >= 0:
                    sig_row = ledger.iloc[sig_idx]
                    cts_accel = sig_row.get("cts_accel", np.nan)
                    cts_accel_threshold = sig_row.get("cts_accel_threshold", np.nan)
                    
                    delta = cts_accel - cts_accel_threshold if not np.isnan(cts_accel) and not np.isnan(cts_accel_threshold) else np.nan
                    
                    results.append({
                        "symbol": t.symbol,
                        "entry_date": t.entry_date,
                        "pnl": t.pnl_pct,
                        "exit_reason": t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason),
                        "cts_accel": cts_accel,
                        "cts_accel_threshold": cts_accel_threshold,
                        "delta": delta,
                        "cts_bt": sig_row.get("cts_buy_threshold", np.nan)
                    })
        except Exception as e:
            print(f"Error processing {sym}: {e}")
            
    df = pd.DataFrame(results)
    if df.empty:
        print("No data extracted.")
        return
        
    df = df.dropna(subset=["delta"])
    df = df.sort_values("delta")
    
    print("\n--- Distribution of PnL by cts_accel Delta ---")
    bins = [-np.inf, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, np.inf]
    labels = ["<= 0.001", "0.001 - 0.002", "0.002 - 0.005", "0.005 - 0.01", "0.01 - 0.02", "0.02 - 0.05", "> 0.05"]
    df['delta_bin'] = pd.cut(df['delta'], bins=bins, labels=labels)
    
    summary = df.groupby('delta_bin', observed=False).agg(
        count=('pnl', 'size'),
        win_rate=('pnl', lambda x: (x > 0).mean() * 100),
        avg_pnl=('pnl', 'mean'),
        hard_stops=('exit_reason', lambda x: (x == "Hard Stop Hit").sum())
    ).round(2)
    
    print(summary)
    
    print("\n--- Trades with negligible delta (< 0.002) ---")
    small_delta = df[df['delta'] < 0.002].sort_values("delta")
    for _, r in small_delta.iterrows():
        print(f"{r['symbol']:<10} {r['entry_date']} | Delta: {r['delta']:.5f} | Accel: {r['cts_accel']:.4f} | Thresh: {r['cts_accel_threshold']:.4f} | cts_bt: {r['cts_bt']:.4f} | PnL: {r['pnl']:+6.2f}% | Exit: {r['exit_reason']}")

if __name__ == "__main__":
    main()
