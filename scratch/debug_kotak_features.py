import sys
import numpy as np
import copy
sys.path.insert(0, "/home/vn/python-projects/liquidity-flow-monitor")

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import get_symbol_entry_config, get_symbol_exit_config
from scratch.train_symbol_weights_parallel import label_candidate_bars_sim, train_bayesian_model

def main():
    sym = "KOTAKBANK"
    engine = DivergenceEngine(sym, start_date=None, end_date=None)
    res = engine.run()
    ledger = res.ledger
    records = ledger.to_dict("records")
    
    # Find the row for 2023-03-21
    idx = -1
    for i, r in enumerate(records):
        if str(r.get("date", ""))[:10] == "2023-03-21":
            idx = i
            break
            
    if idx == -1:
        print("Could not find row for 2023-03-21")
        return
        
    row = records[idx]
    prev_row = records[idx - 1]
    
    print(f"Date: {row.get('date')} (idx {idx})")
    print(f"Close: {row.get('close')}")
    
    # Let's extract feature values
    feat_vals = {
        "cwc": row.get("cwc", 0.0),
        "psz_v": row.get("psz_v", 0.0),
        "fas": row.get("fas", 0.0),
        "cts_accel": row.get("cts_accel", 0.0),
        "pdd_30": row.get("pdd_30", 0.0),
        "pdd_120": row.get("pdd_120", 0.0),
        "range_pos_10": row.get("range_pos_10", 0.0),
        "range_pos_63": row.get("range_pos_63", 0.0),
        "range_pos_252": row.get("range_pos_252", 0.0),
        "dv_shock": row.get("dv_shock", 0.0),
        "esr": row.get("esr", 0.0),
        "base_tightness": row.get("base_tightness", 1.0),
        "cts": row.get("cts", 0.0),
    }
    
    # 1. Config from symbol_configs.py (verification config)
    cfg_ver = get_symbol_entry_config(sym)
    weights_ver = cfg_ver.custom_bayesian.feature_weights
    
    # 2. Config from training
    signal = SignalFactory.get_signal("savgol_cts")
    candidates, labels = label_candidate_bars_sim(sym, ledger, signal, None)
    fb_train, fw_train = train_bayesian_model(ledger, candidates, labels, num_bins=3)
    
    # Let's print details for both
    print("\nFeature Comparison on 2023-03-21:")
    score_ver = 0.0
    score_train = 0.0
    
    for feat, val in feat_vals.items():
        # Ver weight
        w_ver = 0.0
        if feat in weights_ver:
            for left, right, w in weights_ver[feat]:
                if left < val <= right:
                    w_ver = w
                    break
        
        # Train weight
        w_train = 0.0
        if feat in fw_train:
            for left, right, w in fw_train[feat]:
                if left < val <= right:
                    w_train = w
                    break
                    
        # Skip range_pos_252 for score sum if it is not in entry_custom_bayesian list (which has 12 items)
        is_in_signal_feat = feat != "range_pos_252"
        
        if is_in_signal_feat:
            score_ver += w_ver
            score_train += w_train
            
        print(f"{feat:<15} | Val: {val:8.4f} | Ver W: {w_ver:8.4f} | Train W: {w_train:8.4f} | In Signal: {is_in_signal_feat}")
        
    print(f"\nSum score (12 feats) - Ver: {score_ver:.6f} | Train: {score_train:.6f}")

if __name__ == "__main__":
    main()
