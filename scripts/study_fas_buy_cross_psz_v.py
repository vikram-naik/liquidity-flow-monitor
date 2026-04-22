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
from src.trading.signals.savgol_cts.entries.prt_slope_zero_cross import is_flattish_line_adaptive

def main():
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    symbols = get_watchlist_symbols("NIFTY 50")
    
    print("Running TRAIN and TEST periods to collect fas-buy-cross trades...")
    train_trades = run_period(symbols, "2019-01-01", "2023-12-31", entry_cfg, exit_cfg, "TRAIN", signal)
    test_trades = run_period(symbols, "2024-01-01", today_str(), entry_cfg, exit_cfg, "TEST", signal)
    
    all_trades = [t for t in (train_trades + test_trades) if t.entry_tag == EntryTag.FAS_BUY_CROSS.value]
    
    print(f"\nFound {len(all_trades)} FAS_BUY_CROSS trades. Extracting psz_v details...")
    
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
                
                if sig_idx >= 2:
                    sig_row = ledger.iloc[sig_idx]
                    prev_row = ledger.iloc[sig_idx - 1]
                    prev2_row = ledger.iloc[sig_idx - 2]
                    
                    psz_v = sig_row.get("psz_v", np.nan)
                    prev_psz_v = prev_row.get("psz_v", np.nan)
                    prev2_psz_v = prev2_row.get("psz_v", np.nan)
                    
                    lookback_size = 10
                    if sig_idx >= lookback_size:
                        psz_v_lookback = np.array([
                            ledger.iloc[sig_idx - n].get("psz_v", np.nan) 
                            for n in range(lookback_size, 0, -1)
                        ])
                        
                        flat_check = is_flattish_line_adaptive(
                            psz_v, prev_psz_v, prev2_psz_v, 
                            psz_v_lookback, sensitivity=0.15
                        )
                        is_flat = flat_check["is_valid"]
                        range_spread = flat_check["range_spread"]
                    else:
                        is_flat = False
                        range_spread = np.nan
                        
                    is_rising = psz_v > prev_psz_v
                    is_negative = psz_v < 0
                    
                    results.append({
                        "symbol": t.symbol,
                        "entry_date": t.entry_date,
                        "pnl": t.pnl_pct,
                        "exit_reason": t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason),
                        "psz_v": psz_v,
                        "is_flat": is_flat,
                        "is_rising": is_rising,
                        "is_negative": is_negative,
                        "range_spread": range_spread
                    })
        except Exception as e:
            print(f"Error processing {sym}: {e}")
            
    df = pd.DataFrame(results)
    if df.empty:
        print("No data extracted.")
        return
        
    print("\n--- Distribution of PnL by psz_v Flatness ---")
    summary_flat = df.groupby('is_flat').agg(
        count=('pnl', 'size'),
        win_rate=('pnl', lambda x: (x > 0).mean() * 100),
        avg_pnl=('pnl', 'mean'),
        hard_stops=('exit_reason', lambda x: (x == "Hard Stop Hit").sum())
    ).round(2)
    print(summary_flat)

    print("\n--- Distribution of PnL by psz_v Rising ---")
    summary_rising = df.groupby('is_rising').agg(
        count=('pnl', 'size'),
        win_rate=('pnl', lambda x: (x > 0).mean() * 100),
        avg_pnl=('pnl', 'mean'),
        hard_stops=('exit_reason', lambda x: (x == "Hard Stop Hit").sum())
    ).round(2)
    print(summary_rising)

    print("\n--- Distribution of PnL by psz_v Sign (Negative/Positive) ---")
    summary_sign = df.groupby('is_negative').agg(
        count=('pnl', 'size'),
        win_rate=('pnl', lambda x: (x > 0).mean() * 100),
        avg_pnl=('pnl', 'mean'),
        hard_stops=('exit_reason', lambda x: (x == "Hard Stop Hit").sum())
    ).round(2)
    print(summary_sign)
    
    print("\n--- Flat psz_v Trades ---")
    flat_trades = df[df['is_flat'] == True]
    for _, r in flat_trades.iterrows():
        print(f"{r['symbol']:<10} {r['entry_date']} | psz_v: {r['psz_v']:.4f} | spread: {r['range_spread']:.4f} | PnL: {r['pnl']:+6.2f}% | Exit: {r['exit_reason']}")

    print("\n--- Falling psz_v Trades ---")
    falling_trades = df[df['is_rising'] == False]
    for _, r in falling_trades.iterrows():
        print(f"{r['symbol']:<10} {r['entry_date']} | psz_v: {r['psz_v']:.4f} | flat: {r['is_flat']} | PnL: {r['pnl']:+6.2f}% | Exit: {r['exit_reason']}")

if __name__ == "__main__":
    main()
