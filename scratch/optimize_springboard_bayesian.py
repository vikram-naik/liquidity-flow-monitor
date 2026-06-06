import os
import sys
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from tabulate import tabulate

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade, SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag, ExitReason
from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend

def get_spearman_5(records, idx):
    tps_5 = []
    for k in range(idx - 4, idx + 1):
        r = records[k]
        tp = (r.get("high", 0.0) + r.get("low", 0.0) + r.get("close", 0.0)) / 3.0
        tps_5.append(tp)
    if len(tps_5) >= 5:
        return evaluate_spearman_trend(tps_5)
    return np.nan

def get_min_cts_10(records, idx):
    cts_vals = []
    for k in range(max(0, idx - 9), idx + 1):
        c = records[k].get("cts", np.nan)
        if not np.isnan(c):
            cts_vals.append(c)
    if cts_vals:
        return min(cts_vals)
    return np.nan

def check_original_springboard(row, prev_row, records, idx):
    if idx < 10:
        return False
        
    rp_63 = row.get("range_pos_63", np.nan)
    if np.isnan(rp_63) or rp_63 > 0.40:
        return False
        
    min_cts = get_min_cts_10(records, idx)
    if np.isnan(min_cts) or min_cts > -0.75:
        return False
        
    cts = row.get("cts", np.nan)
    prev_cts = prev_row.get("cts", np.nan)
    if np.isnan(cts) or np.isnan(prev_cts):
        return False
    if cts <= prev_cts or cts == -1.0:
        return False
        
    cwc_slope = row.get("cwc_slope", np.nan)
    if not np.isnan(cwc_slope) and cwc_slope <= -0.05:
        return False
        
    psz_v = row.get("psz_v", np.nan)
    if not np.isnan(psz_v) and psz_v <= -0.16:
        return False
        
    spearman_5 = get_spearman_5(records, idx)
    if np.isnan(spearman_5) or spearman_5 <= -0.95:
        return False
        
    return True

def simulate_chronological_trades(ticker, records, exit_cfg, signal, entry_fn):
    """Simulates sequential trades where no overlapping trades can occur on the same ticker."""
    n = len(records)
    trades = []
    in_trade = False
    trade = None
    peak_close = 0.0
    delivery_bad_count = 0
    cwvap_values = []
    pending_signal_idx = -1
    pending_exit_reason = None
    
    # Temporarily apply our optimized exit overrides for SpringBoard
    exit_cfg.universal_cross.prt_st_cross_enabled = False
    exit_cfg.universal_cross.cts_st_cross_enabled = False
    exit_cfg.universal_cross.cts_near_miss_exit_enabled = False
    exit_cfg.springboard.hard_stop_enabled = True
    exit_cfg.springboard.hard_stop_pct = 12.0
    
    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue
            
        cw = row.get("cwvap", np.nan)
        cwvap_values.append(cw)
        
        # Execute pending exit at today's open
        if pending_exit_reason is not None:
            open_price = row.get("open", np.nan)
            exit_price = open_price if not np.isnan(open_price) else close
            trade.exit_date = str(row.get("date", ""))[:10]
            trade.exit_price = round(exit_price, 2)
            trade.exit_reason = pending_exit_reason
            trade.pnl_pct = round((exit_price / trade.entry_price - 1) * 100, 2)
            trade.duration = i - trade.entry_idx
            trades.append(trade)
            in_trade = False
            trade = None
            delivery_bad_count = 0
            pending_exit_reason = None
            continue
            
        if in_trade:
            if close > peak_close:
                peak_close = close
                
            bars_held = i - trade.entry_idx
            trade.mfe_pct = max(trade.mfe_pct, (close / trade.entry_price - 1) * 100)
            mae_val = (close / trade.entry_price - 1) * 100
            trade.mae_pct = max(trade.mae_pct, -mae_val)
            
            # Apply hard stop override
            pnl_pct = (close / trade.entry_price - 1.0) * 100.0
            if pnl_pct <= -12.0:
                pending_exit_reason = ExitReason.HARD_STOP
                continue
                
            reason, delivery_bad_count = signal.check_exit(
                row, prev, trade, peak_close, bars_held,
                delivery_bad_count, cwvap_values, exit_cfg,
                records, i
            )
            
            if reason:
                pending_exit_reason = reason
                
        elif pending_signal_idx != -1:
            atr = row.get("atr_20", 0.0)
            if atr <= 0 or np.isnan(atr):
                pending_signal_idx = -1
                continue
                
            trade = Trade(
                symbol=ticker,
                entry_date=str(row.get("date", ""))[:10],
                entry_price=close,
                entry_idx=i,
                atr_at_entry=atr,
                conviction_score=84,
                regime_at_entry=row.get("regime", "-"),
                entry_tag=EntryTag.SPRINGBOARD.value,
                psz_at_entry=row.get("price_slope_z", 0.0),
                psz_peak=row.get("price_slope_z", 0.0),
            )
            peak_close = close
            delivery_bad_count = 0
            in_trade = True
            pending_signal_idx = -1
            
        else:
            if entry_fn(row, prev, records, i):
                pending_signal_idx = i
                
    if in_trade and trade and pending_exit_reason is None:
        last = records[-1]
        trade.exit_date = str(last.get("date", ""))[:10]
        trade.exit_price = last.get("close", trade.entry_price)
        trade.exit_reason = ExitReason.END_OF_DATA
        trade.pnl_pct = round((trade.exit_price / trade.entry_price - 1) * 100, 2)
        trade.duration = n - 1 - trade.entry_idx
        trades.append(trade)
        
    return trades

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
    
    exit_cfg = SavgolCTSExitConfig()
    
    signal = SignalFactory.get_signal("savgol_cts")
    
    print("Loading and caching stock data...")
    cache_records = {}
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            cache_records[sym] = res.ledger.to_dict('records')
        except Exception as e:
            print(f"Error loading {sym}: {e}")
            
    print(f"Cached records for {len(cache_records)} symbols.")
    
    # Run chronological simulation using original springboard to collect training trades
    print("\nRunning sequential simulation under original springboard baseline to gather training trades...")
    all_baseline_trades = []
    for sym, recs in cache_records.items():
        trades = simulate_chronological_trades(sym, recs, exit_cfg, signal, check_original_springboard)
        all_baseline_trades.extend(trades)
        
    print(f"Total baseline trades executed: {len(all_baseline_trades)}")
    
    train_baseline = [t for t in all_baseline_trades if str(t.entry_date) < "2024-01-01"]
    test_baseline = [t for t in all_baseline_trades if str(t.entry_date) >= "2024-01-01"]
    
    print(f"Train baseline trades: {len(train_baseline)}")
    print(f"Test baseline trades: {len(test_baseline)}")
    
    # Build training features
    train_rows = []
    for t in train_baseline:
        sym = t.symbol
        records = cache_records[sym]
        sig_idx = t.entry_idx - 1
        
        row = records[sig_idx]
        prev = records[sig_idx - 1]
        
        rp_63 = row.get("range_pos_63", np.nan)
        min_cts = get_min_cts_10(records, sig_idx)
        cts_surge = row.get("cts", 0.0) - prev.get("cts", 0.0)
        cwc_slope = row.get("cwc_slope", np.nan)
        psz_v = row.get("psz_v", np.nan)
        spearman_5 = get_spearman_5(records, sig_idx)
        
        train_rows.append({
            "pnl": t.pnl_pct,
            "range_pos_63": rp_63,
            "min_cts_10": min_cts,
            "cts_surge": cts_surge,
            "cwc_slope": cwc_slope,
            "psz_v": psz_v,
            "spearman_5": spearman_5
        })
        
    df_train = pd.DataFrame(train_rows)
    
    # Use 3 broader bins per feature to generalize better and prevent overfitting
    feature_bins = {
        "range_pos_63": [-np.inf, 0.20, 0.32, np.inf],
        "min_cts_10": [-np.inf, -0.85, -0.78, np.inf],
        "cts_surge": [-np.inf, 0.08, 0.18, np.inf],
        "cwc_slope": [-np.inf, -0.01, 0.02, np.inf],
        "psz_v": [-np.inf, -0.08, 0.02, np.inf],
        "spearman_5": [-np.inf, -0.85, -0.60, np.inf],
    }
    
    alpha = 1.0  # Laplace smoothing
    
    success_targets = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    
    best_results = []
    
    for target in success_targets:
        print(f"\n========================================================")
        print(f"Training with Success Definition: PnL >= {target}%")
        print(f"========================================================")
        
        df_train['success'] = (df_train['pnl'] >= target).astype(int)
        n_success = df_train['success'].sum()
        n_failure = len(df_train) - n_success
        print(f"Train Successes: {n_success} | Failures: {n_failure} | Base Win Rate: {n_success / len(df_train) * 100:.1f}%")
        
        feature_weights = {}
        for feat, edges in feature_bins.items():
            train_bins = pd.cut(df_train[feat], bins=edges)
            df_train[feat + "_bin"] = train_bins
            
            bin_weights = {}
            grouped = df_train.groupby(feat + "_bin", observed=False)
            for name, group in grouped:
                s_count = group['success'].sum()
                f_count = len(group) - s_count
                
                p_s = (s_count + alpha) / (n_success + alpha * len(edges))
                p_f = (f_count + alpha) / (n_failure + alpha * len(edges))
                
                weight = np.log(p_s / p_f)
                bin_weights[name] = weight
            feature_weights[feat] = bin_weights
            
        def get_bayesian_score(row, prev_row, records, idx):
            if not check_original_springboard(row, prev_row, records, idx):
                return -999.0
                
            rp_63 = row.get("range_pos_63", np.nan)
            min_cts = get_min_cts_10(records, idx)
            cts_surge = row.get("cts", 0.0) - prev_row.get("cts", 0.0)
            cwc_slope = row.get("cwc_slope", np.nan)
            psz_v = row.get("psz_v", np.nan)
            spearman_5 = get_spearman_5(records, idx)
            
            feat_vals = {
                "range_pos_63": rp_63,
                "min_cts_10": min_cts,
                "cts_surge": cts_surge,
                "cwc_slope": cwc_slope,
                "psz_v": psz_v,
                "spearman_5": spearman_5,
            }
            
            score = 0.0
            for feat, val in feat_vals.items():
                for bin_interval, weight in feature_weights[feat].items():
                    if val in bin_interval:
                        score += weight
                        break
            return score

        # Precalculate Bayesian scores on all cached records
        all_scores = []
        for sym, recs in cache_records.items():
            n = len(recs)
            for i in range(1, n):
                score = get_bayesian_score(recs[i], recs[i-1], recs, i)
                recs[i]['bayesian_score'] = score
                if score > -900:
                    all_scores.append(score)
            recs[0]['bayesian_score'] = -999.0
            
        if not all_scores:
            continue
            
        min_score = min(all_scores)
        max_score = max(all_scores)
        
        sweep_results = []
        for thresh in np.linspace(min_score - 0.05, max_score + 0.05, 40):
            def entry_fn(row, prev_row, records, idx):
                return row.get('bayesian_score', -999.0) >= thresh
                
            all_trades_sweep = []
            for sym, recs in cache_records.items():
                trades = simulate_chronological_trades(sym, recs, exit_cfg, signal, entry_fn)
                all_trades_sweep.extend(trades)
                
            train_trades = [t for t in all_trades_sweep if str(t.entry_date) < "2024-01-01"]
            test_trades = [t for t in all_trades_sweep if str(t.entry_date) >= "2024-01-01"]
            
            train_pnl = np.mean([t.pnl_pct for t in train_trades]) if train_trades else np.nan
            test_pnl = np.mean([t.pnl_pct for t in test_trades]) if test_trades else np.nan
            
            sweep_results.append({
                "Target": target,
                "Threshold": thresh,
                "Train Trades": len(train_trades),
                "Train Avg PnL%": train_pnl,
                "Test Trades": len(test_trades),
                "Test Avg PnL%": test_pnl,
            })
            
        df_sweep = pd.DataFrame(sweep_results)
        
        # Filter for Train/Test avg pnl > 5.0% and minimum trade counts
        valid = df_sweep[
            (df_sweep['Train Avg PnL%'] >= 5.0) & 
            (df_sweep['Test Avg PnL%'] >= 5.0) &
            (df_sweep['Train Trades'] >= 25) &
            (df_sweep['Test Trades'] >= 10)
        ].sort_values(by="Train Avg PnL%", ascending=False)
        
        if not valid.empty:
            print(f"Found {len(valid)} valid thresholds for Target {target}%!")
            print(tabulate(valid.head(5), headers="keys", tablefmt="grid", floatfmt=".3f"))
            best_results.append((target, valid.iloc[0], feature_weights, feature_bins))
        else:
            print(f"No valid thresholds meeting target of >5% for Target {target}% with minimum trade count.")
            top_t = df_sweep.sort_values(by="Train Avg PnL%", ascending=False).head(5)
            print("Top results by Train PnL:")
            print(tabulate(top_t, headers="keys", tablefmt="grid", floatfmt=".3f"))
            
    if best_results:
        best_results.sort(key=lambda x: x[1]['Train Avg PnL%'], reverse=True)
        best_target, best_row, best_weights, best_bins = best_results[0]
        
        print("\n" + "="*80)
        print(f"BEST CONFIGURATION FOUND")
        print("="*80)
        print(f"Success Target: PnL >= {best_target}%")
        print(f"Threshold: {best_row['Threshold']:.4f}")
        print(f"Train Trades: {best_row['Train Trades']} | Train PnL: {best_row['Train Avg PnL%']:.3f}%")
        print(f"Test Trades: {best_row['Test Trades']} | Test PnL: {best_row['Test Avg PnL%']:.3f}%")
        
        print("\n--- Copy-paste config to config.py ---")
        print("bayesian_mode: bool = True")
        print(f"score_threshold: float = {best_row['Threshold']:.4f}")
        
        print("\nfeature_bins = {")
        for feat, edges in best_bins.items():
            print(f"    '{feat}': {edges},")
        print("}")
        
        print("\nfeature_weights = {")
        for feat, bin_w in best_weights.items():
            print(f"    '{feat}': [")
            for interval, weight in bin_w.items():
                left = interval.left
                right = interval.right
                print(f"        ({left}, {right}, {weight:.6f}),")
            print("    ],")
        print("}")
    else:
        print("\nCould not find any configuration meeting > 5.0% Train/Test P&L and minimum trade counts.")

if __name__ == "__main__":
    main()
