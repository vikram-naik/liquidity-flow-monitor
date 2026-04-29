import sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeClassifier, export_text

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
    
    test_trades = run_period(symbols, "2024-01-01", today_str(), entry_cfg, exit_cfg, "TEST", signal)
    all_trades = [t for t in test_trades if t.entry_tag == EntryTag.FAS_BUY_CROSS.value]
    
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
                    
                    is_hard_stop = (t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)) == "Hard Stop Hit"
                    is_good = t.pnl_pct >= 4.0
                    
                    if is_hard_stop:
                        label = 1
                    elif is_good:
                        label = 0
                    else:
                        continue # Only train on extremes
                        
                    results.append({
                        "label": label,
                        "pnl": t.pnl_pct,
                        "coherence": sig_row.get("coherence", np.nan),
                        "cwc": sig_row.get("cwc", np.nan),
                        "cwc_slope": sig_row.get("cwc_slope", np.nan),
                        "mcs_slope": sig_row.get("mcs_composite_slope", np.nan),
                        "vel_10_norm": sig_row.get("velocity_10_norm", np.nan),
                        "pdd_10": sig_row.get("pdd_10", np.nan),
                        "pdd_60": sig_row.get("pdd_60", np.nan),
                        "pdd_120": sig_row.get("pdd_120", np.nan),
                        "rsz_v": sig_row.get("rsz_v", np.nan),
                        "atr_pct": sig_row.get("atr_20", np.nan) / sig_row.get("close", np.nan),
                        "dist_high_10": sig_row.get("dist_high_10", np.nan),
                        "price_slope_z": sig_row.get("price_slope_z", np.nan),
                        "cwvap_dist": sig_row.get("close") / sig_row.get("cwvap") - 1,
                        "dvwap_60_dist": sig_row.get("close") / sig_row.get("dvwap_60") - 1,
                        "fas": sig_row.get("fas", np.nan),
                        "cts_accel": sig_row.get("cts_accel", np.nan),
                        "delta_cts_accel": sig_row.get("cts_accel", np.nan) - sig_row.get("cts_accel_threshold", np.nan),
                        "accum_div": sig_row.get("accum_div", np.nan)
                    })
        except Exception as e:
            pass
            
    df = pd.DataFrame(results).dropna()
    X = df.drop(columns=['label', 'pnl'])
    y = df['label']
    
    clf = DecisionTreeClassifier(max_depth=3, random_state=42, class_weight={0: 100, 1: 1})
    clf.fit(X, y)
    
    print(export_text(clf, feature_names=list(X.columns)))
    
if __name__ == "__main__":
    main()
