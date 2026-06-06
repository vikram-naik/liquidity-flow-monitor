import sys
import os
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag, ExitReason
from scripts.walk_forward import simulate_trades

# Bins and weights from the optimization output
feature_bins = {
    'range_pos_22': [-np.inf, 0.1621347175611818, 0.30754132734516737, np.inf],
    'range_pos_63': [-np.inf, 0.1609814811284787, 0.32191849484000795, np.inf],
    'pdd_30': [-np.inf, -3.7199239219778977, -1.676826520243526, np.inf],
    'range_pos_22_low_count': [-np.inf, 3.0, 5.0, np.inf],
    'cts': [-np.inf, -0.7729008116730189, -0.26902766095853614, np.inf],
    'psz_v': [-np.inf, 0.016087133999999996, 0.06934025800000002, np.inf],
    'cts_accel': [-np.inf, -0.0017448884504695657, 0.02484232957857699, np.inf],
    'price_momentum_change': [-np.inf, -0.002274781941957703, 0.04307968503508724, np.inf],
    'price_decel_3b': [-np.inf, -0.0009566000000000117, 0.1267522, np.inf],
}

feature_weights = {
    'range_pos_22': [
        (-np.inf, 0.162, 0.934461),
        (0.162, 0.308, 0.976389),
        (0.308, np.inf, -0.804577),
    ],
    'range_pos_63': [
        (-np.inf, 0.161, 1.200473),
        (0.161, 0.322, 1.008560),
        (0.322, np.inf, -0.849904),
    ],
    'pdd_30': [
        (-np.inf, -3.72, 1.230515),
        (-3.72, -1.677, 0.978940),
        (-1.677, np.inf, -0.848164),
    ],
    'range_pos_22_low_count': [
        (-np.inf, 3.0, -0.700466),
        (3.0, 5.0, 1.181135),
        (5.0, np.inf, 4.746606),
    ],
    'cts': [
        (-np.inf, -0.773, 1.079407),
        (-0.773, -0.269, 0.674435),
        (-0.269, np.inf, -0.767056),
    ],
    'price_slope_z': [
        (-np.inf, -0.276, 0.708460),
        (-0.276, -0.123, 0.614644),
        (-0.123, np.inf, -0.679039),
    ],
    'psz_v': [
        (-np.inf, 0.0161, -0.582190),
        (0.0161, 0.0693, 0.678209),
        (0.0693, np.inf, 0.348266),
    ],
    'cts_accel': [
        (-np.inf, -0.00174, -0.350465),
        (-0.00174, 0.0248, -0.027507),
        (0.0248, np.inf, 0.588710),
    ],
    'price_momentum_change': [
        (-np.inf, -0.00227, -0.384382),
        (-0.00227, 0.0431, 0.058305),
        (0.0431, np.inf, 0.525397),
    ],
    'price_decel_3b': [
        (-np.inf, -0.000957, -0.399786),
        (-0.000957, 0.127, 0.159492),
        (0.127, np.inf, 0.414627),
    ],
}

def get_watchlist_symbols(name="NIFTY 50"):
    from src.database import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    symbols = [r[0] for r in conn.execute(
        "SELECT symbol FROM watchlist_items WHERE watchlist_id = (SELECT id FROM watchlists WHERE name = ?) ORDER BY display_order",
        (name,)
    ).fetchall()]
    conn.close()
    return symbols

def main():
    symbols = get_watchlist_symbols("NIFTY 50")
    print(f"Loaded {len(symbols)} symbols from NIFTY 50.")
    
    # Load and cache records
    cache_records = {}
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            df = res.ledger.copy()
            
            # Add multi-bar contextual features
            df["price_decel_3b"] = df["price_slope_z"] - df["price_slope_z"].shift(3)
            df["cts_min_5b"] = df["cts"].rolling(5).min()
            df["cts_surge_from_min"] = df["cts"] - df["cts_min_5b"]
            df["pdd_30_compression_3b"] = df["pdd_30"] - df["pdd_30"].shift(3)
            df["range_pos_22_low_count"] = (df["range_pos_22"] < 0.30).rolling(5).sum()
            df["close_change_5b"] = df["close"] / df["close"].shift(5) - 1.0
            df["close_change_5to10b"] = df["close"].shift(5) / df["close"].shift(10) - 1.0
            df["price_momentum_change"] = df["close_change_5b"] - df["close_change_5to10b"]
            
            def get_score(row):
                score = 0.0
                for feat in feature_bins.keys():
                    val = row.get(feat, np.nan)
                    if np.isnan(val):
                        continue
                    for left, right, weight in feature_weights[feat]:
                        if left < val <= right:
                            score += weight
                            break
                return score
                
            df["bayesian_score"] = df.apply(get_score, axis=1)
            cache_records[sym] = df
        except Exception as e:
            print(f"Error loading {sym}: {e}")
            
    print("Data loading completed.")
    
    # Import the actual basic gates
    from src.trading.signals.savgol_cts.entries.universal_cross import entry_universal_cross
    
    # We will sweep the post-filter threshold
    thresholds = [1.50, 2.00, 2.50, 3.00, 3.50, 4.00, 4.50]
    results = []
    
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    
    for thresh in thresholds:
        print(f"Testing post-filter threshold: {thresh:.2f}...")
        
        # We configure a custom MockSignalWrapper that runs the actual universal cross basic gates,
        # but overrides the post-filter score check to use our new contextual Bayesian score.
        class MockSignalWrapper:
            def __init__(self, base_signal):
                self.base_signal = base_signal
                
            def check_entry(self, row, prev_row, cfg, records, idx):
                # 1. Run the base universal cross entry check first (without Bayesian mode to let basic gates run)
                cfg_copy = SavgolCTSEntryConfig()
                cfg_copy.universal_cross.bayesian_mode = False
                passed, intensity, meta = entry_universal_cross(row, prev_row, cfg_copy, records, idx)
                
                if passed:
                    # 2. Check our new contextual Bayesian score
                    score = row.get("bayesian_score", -999.0)
                    if score >= thresh:
                        return True, 85, {"reason": f"Universal-Cross with contextual Bayesian filter (Score: {score:.2f})", "entry_tag": EntryTag.UNIVERSAL_CROSS.value, "score": 85}
                return False, 0, {"reason": "Rejected"}
                
            def check_exit(self, row, prev_row, trade, peak_close, bars_held, delivery_bad_count, cwvap_values, exit_cfg, records, idx):
                return self.base_signal.check_exit(row, prev_row, trade, peak_close, bars_held, delivery_bad_count, cwvap_values, exit_cfg, records, idx)
        
        all_trades = []
        mock_signal = MockSignalWrapper(signal)
        
        for sym, df_sym in cache_records.items():
            records = df_sym.to_dict('records')
            trades = simulate_trades(sym, df_sym, None, exit_cfg, mock_signal)
            all_trades.extend(trades)
            
        train_trades = [t for t in all_trades if str(t.entry_date) < "2025-01-01" and str(t.entry_date) >= "2019-03-01"]
        test_trades = [t for t in all_trades if str(t.entry_date) >= "2025-01-01" and str(t.entry_date) <= "2026-04-30"]
        
        def get_stats(trades):
            if not trades:
                return 0, np.nan, np.nan, np.nan
            pnls = [t.pnl_pct for t in trades]
            avg_pnl = np.mean(pnls)
            win_rate = sum(1 for p in pnls if p > 0) / len(pnls) * 100.0
            
            gross_win = sum(p for p in pnls if p > 0)
            gross_loss = abs(sum(p for p in pnls if p <= 0))
            pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
            
            return len(trades), avg_pnl, win_rate, pf
            
        tr_cnt, tr_pnl, tr_win, tr_pf = get_stats(train_trades)
        te_cnt, te_pnl, te_win, te_pf = get_stats(test_trades)
        
        results.append({
            "PostFilterThresh": thresh,
            "Train Trades": tr_cnt,
            "Train Avg PnL%": tr_pnl,
            "Train Win%": tr_win,
            "Train PF": tr_pf,
            "Test Trades": te_cnt,
            "Test Avg PnL%": te_pnl,
            "Test Win%": te_win,
            "Test PF": te_pf,
        })
        
    df_results = pd.DataFrame(results)
    print("\n--- Universal Cross with Contextual Bayesian Post-Filter Results ---")
    print(tabulate(df_results, headers="keys", tablefmt="grid", floatfmt=".3f"))

if __name__ == "__main__":
    main()
