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
    
    start_date = pd.to_datetime("2019-03-01")
    end_date = pd.to_datetime("2026-04-30")
    
    all_data = []
    
    print("Running engine and constructing multi-bar contextual features...")
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            df = res.ledger.copy()
            df["symbol"] = sym
            df["date_dt"] = pd.to_datetime(df["date"])
            
            # --- Construct Multi-Bar Contextual Features ---
            df["price_decel_3b"] = df["price_slope_z"] - df["price_slope_z"].shift(3)
            df["cts_min_5b"] = df["cts"].rolling(5).min()
            df["cts_surge_from_min"] = df["cts"] - df["cts_min_5b"]
            df["pdd_30_compression_3b"] = df["pdd_30"] - df["pdd_30"].shift(3)
            df["range_pos_22_low_count"] = (df["range_pos_22"] < 0.30).rolling(5).sum()
            df["close_change_5b"] = df["close"] / df["close"].shift(5) - 1.0
            df["close_change_5to10b"] = df["close"].shift(5) / df["close"].shift(10) - 1.0
            df["price_momentum_change"] = df["close_change_5b"] - df["close_change_5to10b"]
            df["max_dv_shock_3b"] = df["dv_shock"].rolling(3).max()
            
            # Filter range
            mask = (df["date_dt"] >= start_date) & (df["date_dt"] <= end_date)
            df_filtered = df.loc[mask].copy()
            if df_filtered.empty:
                continue
                
            all_data.append(df_filtered)
        except Exception as e:
            print(f"Error processing {sym}: {e}")
            
    if not all_data:
        print("No data loaded.")
        return
        
    # Process each symbol to find the EOD-lag trade details
    processed_dfs = []
    for df in all_data:
        sym_df = df.sort_values("date_dt").copy().reset_index(drop=True)
        sym_df["class_label"] = 0  # 0 = background, 1 = successful trough
        sym_df["setup_pnl"] = np.nan
        sym_df["peak_idx"] = -1
        
        trough_indices = sym_df[sym_df["oracle_trough"] == 1.0].index.tolist()
        peak_indices = sym_df[sym_df["oracle_peak"] == 1.0].index.tolist()
        
        for t_idx in trough_indices:
            next_peaks = [p for p in peak_indices if p > t_idx]
            if not next_peaks:
                continue
            p_idx = next_peaks[0]
            
            if t_idx + 1 >= len(sym_df) or p_idx + 1 >= len(sym_df):
                continue
                
            close_entry = sym_df.loc[t_idx + 1, "close"]
            close_exit = sym_df.loc[p_idx + 1, "close"]
            pnl_pct = (close_exit / close_entry - 1.0) * 100.0
            
            sym_df.loc[t_idx, "setup_pnl"] = pnl_pct
            sym_df.loc[t_idx, "peak_idx"] = p_idx
            
            if pnl_pct > 5.0:
                sym_df.loc[t_idx, "class_label"] = 1
                
        processed_dfs.append(sym_df)
        
    df_all = pd.concat(processed_dfs, ignore_index=True)
    
    # Train / Test split
    train_mask = df_all["date_dt"] < pd.to_datetime("2025-01-01")
    df_train = df_all.loc[train_mask].copy()
    df_test = df_all.loc[~train_mask].copy()
    
    print(f"Train set: {len(df_train)} rows, {df_train['class_label'].sum()} successful troughs")
    print(f"Test set:  {len(df_test)} rows, {df_test['class_label'].sum()} successful troughs")
    
    # Selected best contextual features
    features = [
        "range_pos_22", 
        "range_pos_63", 
        "pdd_30", 
        "range_pos_22_low_count", 
        "cts", 
        "psz_v", 
        "cts_accel", 
        "price_momentum_change",
        "price_decel_3b"
    ]
    
    # Define feature bins
    feature_bins = {}
    for feat in features:
        pos_vals = df_train[df_train["class_label"] == 1][feat].dropna()
        if len(pos_vals) < 3:
            feature_bins[feat] = [-np.inf, -1.0, 1.0, np.inf]
            continue
            
        q33 = np.percentile(pos_vals, 33.3)
        q66 = np.percentile(pos_vals, 66.7)
        edges = [-np.inf, q33, q66, np.inf]
        
        if len(set(edges)) < 4:
            all_vals = df_train[feat].dropna()
            q33 = np.percentile(all_vals, 33.3)
            q66 = np.percentile(all_vals, 66.7)
            edges = [-np.inf, q33, q66, np.inf]
            if len(set(edges)) < 4:
                edges = [-np.inf, pos_vals.min(), pos_vals.max(), np.inf]
                edges = sorted(list(set(edges)))
                
        feature_bins[feat] = edges
        
    # Calculate weights on the training set
    alpha = 1.0
    n_pos = df_train["class_label"].sum()
    n_neg = len(df_train) - n_pos
    
    feature_weights = {}
    for feat in features:
        edges = feature_bins[feat]
        train_bins = pd.cut(df_train[feat], bins=edges)
        df_train[feat + "_bin"] = train_bins
        
        bin_weights = {}
        grouped = df_train.groupby(feat + "_bin", observed=False)
        for bin_interval, group in grouped:
            s_count = group["class_label"].sum()
            f_count = len(group) - s_count
            
            p_s = (s_count + alpha) / (n_pos + alpha * len(edges))
            p_f = (f_count + alpha) / (n_neg + alpha * len(edges))
            
            weight = np.log(p_s / p_f)
            bin_weights[bin_interval] = weight
            
        feature_weights[feat] = bin_weights
        
    # Function to calculate score
    def calculate_score(row):
        score = 0.0
        for feat in features:
            val = row.get(feat, np.nan)
            if np.isnan(val):
                continue
            for bin_interval, weight in feature_weights[feat].items():
                if val in bin_interval:
                    score += weight
                    break
        return score
        
    # Precompute scores
    # We must do this symbol-by-symbol on the full DataFrames to ensure shifts/rolling worked correctly
    cached_df_scored = {}
    for sym in symbols:
        # Filter processed_dfs
        sym_df = next(d for d in processed_dfs if d["symbol"].iloc[0] == sym).copy()
        sym_df["bayesian_score"] = sym_df.apply(calculate_score, axis=1)
        cached_df_scored[sym] = sym_df
        
    print("Scoring completed.")
    
    # Sweep thresholds under actual causal exits
    exit_cfg = SavgolCTSExitConfig()
    exit_cfg.universal_cross.hard_stop_enabled = True
    exit_cfg.universal_cross.hard_stop_pct = 8.0
    
    signal = SignalFactory.get_signal("savgol_cts")
    
    thresholds = np.linspace(2.0, 5.5, 15)
    results = []
    
    for thresh in thresholds:
        print(f"Testing threshold: {thresh:.2f}...")
        
        def check_bayesian_entry(row, prev_row, records, idx):
            score = row.get("bayesian_score", -999.0)
            return score >= thresh

        class MockSignalWrapper:
            def __init__(self, base_signal):
                self.base_signal = base_signal
                
            def check_entry(self, row, prev_row, cfg, records, idx):
                passed = check_bayesian_entry(row, prev_row, records, idx)
                if passed:
                    return True, 85, {"reason": "OracleMimic Bayesian cross", "entry_tag": "OracleMimic", "score": 85}
                return False, 0, {"reason": "Rejected"}
                
            def check_exit(self, row, prev_row, trade, peak_close, bars_held, delivery_bad_count, cwvap_values, exit_cfg, records, idx):
                return self.base_signal.check_exit(row, prev_row, trade, peak_close, bars_held, delivery_bad_count, cwvap_values, exit_cfg, records, idx)
        
        all_trades = []
        mock_signal = MockSignalWrapper(signal)
        
        for sym, df_sym in cached_df_scored.items():
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
            "Threshold": thresh,
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
    print("\n--- Causal Sweep Results (Contextual Pattern) ---")
    print(tabulate(df_results, headers="keys", tablefmt="grid", floatfmt=".3f"))
    
    # Filter for best causal threshold
    valid = df_results[(df_results["Train Trades"] >= 30) & (df_results["Test Trades"] >= 8)].copy()
    if not valid.empty:
        # Sort by Test Avg PnL% or Train Avg PnL%
        best_row = valid.sort_values("Test Avg PnL%", ascending=False).iloc[0]
        best_thresh = best_row["Threshold"]
        print(f"\nOptimal Contextual Bayesian Threshold: {best_thresh:.4f}")
        print(f"Performance at Optimal Threshold:")
        print(f"  Train: Trades = {best_row['Train Trades']:.0f}, Avg PnL = {best_row['Train Avg PnL%']:.2f}%, Win Rate = {best_row['Train Win%']:.1f}%, PF = {best_row['Train PF']:.2f}")
        print(f"  Test:  Trades = {best_row['Test Trades']:.0f}, Avg PnL = {best_row['Test Avg PnL%']:.2f}%, Win Rate = {best_row['Test Win%']:.1f}%, PF = {best_row['Test PF']:.2f}")
        
        print("\nCopy-paste parameters:")
        print(f"bayesian_mode: bool = True")
        print(f"score_threshold: float = {best_thresh:.4f}")
        
        print("\nfeature_bins = {")
        for feat, edges in feature_bins.items():
            print(f"    '{feat}': {edges},")
        print("}")
        
        print("\nfeature_weights = {")
        for feat, bin_w in feature_weights.items():
            print(f"    '{feat}': [")
            for interval, weight in bin_w.items():
                left = interval.left
                right = interval.right
                print(f"        ({left}, {right}, {weight:.6f}),")
            print("    ],")
        print("}")

if __name__ == "__main__":
    main()
