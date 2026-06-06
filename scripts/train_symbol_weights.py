import sqlite3
import pandas as pd
import numpy as np
import math
import sys
import copy
import argparse
import json
from pathlib import Path

# Add root folder to sys.path
sys.path.insert(0, "/home/vn/python-projects/liquidity-flow-monitor")

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from scripts.walk_forward import simulate_trades, get_watchlist_symbols

# List of 12 features for Bayesian model
FEATURES = [
    "cwc",
    "psz_v",
    "fas",
    "cts_accel",
    "pdd_30",
    "pdd_120",
    "range_pos_10",
    "range_pos_63",
    "dv_shock",
    "esr",
    "base_tightness",
    "cts"
]

def simulate_single_trade(sym, ledger, start_idx, signal, exit_cfg):
    """
    Simulates a single trade starting at entry_idx = start_idx + 1 (using EOD-lag).
    Returns:
      pnl_pct: final P&L percent
      mae_pct: maximum adverse excursion (max drawdown percent during trade)
    """
    records = ledger.to_dict("records")
    n = len(records)
    
    # Entry executes at close of start_idx + 1
    entry_idx = start_idx + 1
    if entry_idx >= n:
        return 0.0, 0.0
        
    entry_price = records[entry_idx].get("close", np.nan)
    atr = records[entry_idx].get("atr_20", 0.0)
    if np.isnan(entry_price) or entry_price <= 0.0 or atr <= 0.0:
        return 0.0, 0.0
        
    trade = Trade(
        symbol=sym,
        entry_date=str(records[entry_idx].get("date", ""))[:10],
        entry_price=entry_price,
        entry_idx=entry_idx,
        atr_at_entry=atr,
        conviction_score=100,
        regime_at_entry="-",
        entry_tag="custom_bayesian",
        psz_at_entry=0.0,
        psz_peak=0.0
    )
    
    peak_close = entry_price
    delivery_bad_count = 0
    cwvap_values = []
    
    # Fill pre-entry cwvap values
    for k in range(max(0, entry_idx - 50), entry_idx):
        cwvap_values.append(records[k].get("cwvap", np.nan))
        
    mae_val = 0.0
    
    for idx in range(entry_idx + 1, n):
        row = records[idx]
        prev = records[idx - 1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue
            
        cwvap_values.append(row.get("cwvap", np.nan))
        
        if close > peak_close:
            peak_close = close
            
        bars_held = idx - entry_idx
        
        # Track drawdown (MAE)
        drawdown = (close / entry_price - 1.0) * 100.0
        if drawdown < 0:
            mae_val = max(mae_val, abs(drawdown))
            
        # Check exit
        reason, delivery_bad_count = signal.check_exit(
            row, prev, trade, peak_close, bars_held,
            delivery_bad_count, cwvap_values, exit_cfg,
            records, idx
        )
        
        if reason:
            # Exit executes at open of next bar (idx + 1)
            exit_idx = min(n - 1, idx + 1)
            exit_row = records[exit_idx]
            exit_open = exit_row.get("open", np.nan)
            exit_price = exit_open if not np.isnan(exit_open) else exit_row.get("close", entry_price)
            
            pnl_pct = (exit_price / entry_price - 1.0) * 100.0
            
            # Final drawdown check on exit bar
            exit_drawdown = (exit_price / entry_price - 1.0) * 100.0
            if exit_drawdown < 0:
                mae_val = max(mae_val, abs(exit_drawdown))
                
            return pnl_pct, mae_val
            
    # If we reached the end of the data without exiting
    last_close = records[-1].get("close", entry_price)
    pnl_pct = (last_close / entry_price - 1.0) * 100.0
    return pnl_pct, mae_val


def label_candidate_bars_sim(sym, ledger, signal, exit_cfg):
    """
    Labels each candidate bar based on a single-trade simulation with actual exits.
    Returns:
      candidates: list of indices that pass structural triggers
      labels: dict mapping index to label (1=Success, 0=Failure, None=Neutral)
    """
    n = len(ledger)
    records = ledger.to_dict("records")
    candidates = []
    labels = {}
    
    # Calculate average ATR%
    close_vals = ledger["close"].values
    atr_vals = ledger["atr_20"].values
    valid_atr_pct = [atr / close * 100.0 for close, atr in zip(close_vals, atr_vals) if not np.isnan(close) and not np.isnan(atr) and close > 0]
    avg_atr_pct = np.mean(valid_atr_pct) if valid_atr_pct else 2.0
    
    # Calculate volatility-adjusted thresholds
    success_threshold = max(1.5, 1.2 * avg_atr_pct)
    drawdown_limit = max(3.5, 2.2 * avg_atr_pct)
    
    print(f"  Volatility-adjusted targets: Success P&L >= {success_threshold:.2f}%, Drawdown limit >= {drawdown_limit:.2f}%")
    
    # Import evaluate_spearman_trend
    from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend
    
    for i in range(1, n - 2):
        row = records[i]
        prev_row = records[i - 1]
        
        # 1. Structural triggers
        prev_cs = prev_row.get("cts_slope", 0.0)
        cs = row.get("cts_slope", 0.0)
        trigger_cs = (prev_cs <= 0 and cs > 0)

        fas_bt = row.get("fas_buy_threshold", -0.8)
        prev_fas = prev_row.get("fas", 0.0)
        fas = row.get("fas", 0.0)
        trigger_fas = (prev_fas <= fas_bt and fas > fas_bt)

        prt = row.get("prt_slope", 0.0)
        prev_prt = prev_row.get("prt_slope", 0.0)
        trigger_prt = (prev_prt <= 0 and prt > 0)

        trigger_cwc = False
        if i >= 3:
            cwc_vals = [records[k].get("cwc", 0.0) for k in range(i - 3, i)]
            cwc_curr = row.get("cwc", 0.0)
            cwc_avg = sum(cwc_vals) / len(cwc_vals) if cwc_vals else 0.0
            cwc_disp = max(cwc_vals) - min(cwc_vals) if cwc_vals else 0.0
            regime = row.get("regime", "notrend")
            if 0.80 <= cwc_avg <= 1.00 and cwc_disp <= 0.10:
                if cwc_curr > cwc_avg and (cwc_curr - cwc_avg) >= 0.01:
                    if regime != "downtrend":
                        trigger_cwc = True

        # Trigger 5: Accumulation Exhaustion
        trigger_5 = False
        cwc_val = row.get("cwc", 0.0)
        cts_accel = row.get("cts_accel", 0.0)
        pdd_120 = row.get("pdd_120", 0.0)
        bt = row.get("base_tightness", 1.0)
        price_slope_z = row.get("price_slope_z", 0.0)
        
        if cwc_val >= 0.40 and cts_accel >= 0.01 and pdd_120 <= -4.0 and bt <= 0.45 and price_slope_z <= -0.15:
            tps_10 = []
            for k in range(max(0, i - 9), i + 1):
                r = records[k]
                tp = (r.get("high", 0.0) + r.get("low", 0.0) + r.get("close", 0.0)) / 3.0
                tps_10.append(tp)
            if len(tps_10) >= 5:
                spearman_10 = evaluate_spearman_trend(tps_10)
                if spearman_10 > -0.90:
                    trigger_5 = True
                    
        # Check basic safety: CTS must be rising
        prev_cts = prev_row.get("cts", 0.0)
        cts = row.get("cts", 0.0)
        if cts < prev_cts:
            continue
            
        if not (trigger_cs or trigger_fas or trigger_prt or trigger_cwc or trigger_5):
            continue
            
        # Entry executes at close of i+1
        entry_price = records[i + 1].get("close", np.nan)
        if np.isnan(entry_price) or entry_price <= 0.0:
            continue
            
        candidates.append(i)
        
        # Simulate single trade outcome using standard exits
        pnl_pct, mae_pct = simulate_single_trade(sym, ledger, i, signal, exit_cfg)
        
        # Label:
        if pnl_pct >= success_threshold and mae_pct < drawdown_limit:
            outcome = 1
        elif pnl_pct <= 0.0 or mae_pct >= drawdown_limit:
            outcome = 0
        else:
            outcome = None
            
        labels[i] = outcome
        
    return candidates, labels


def train_bayesian_model(ledger, candidates, labels):
    """
    Fits 3-bin edges and computes log-odds weights for each feature.
    """
    records = ledger.to_dict("records")
    
    # Filter candidates to only those with non-neutral labels
    train_idx = [idx for idx in candidates if labels.get(idx) is not None]
    
    feature_bins = {}
    feature_weights = {}
    
    if not train_idx:
        for feat in FEATURES:
            feature_bins[feat] = [-float('inf'), 0.0, float('inf')]
            feature_weights[feat] = [(-float('inf'), 0.0, 0.0), (0.0, float('inf'), 0.0)]
        return feature_bins, feature_weights

    y_train = np.array([labels[idx] for idx in train_idx])
    n_success = int((y_train == 1).sum())
    n_failure = int((y_train == 0).sum())
    
    for feat in FEATURES:
        feat_vals = []
        for idx in train_idx:
            val = records[idx].get(feat, 0.0)
            if val is None or (isinstance(val, float) and math.isnan(val)):
                val = 0.0
            feat_vals.append(val)
        feat_vals = np.array(feat_vals)
        
        if len(feat_vals) >= 3:
            q = np.percentile(feat_vals, [33.3, 66.7])
            q = np.unique(q)
            bins = [-float('inf')] + list(q) + [float('inf')]
        else:
            bins = [-float('inf'), 0.0, float('inf')]
            
        feature_bins[feat] = bins
        
        weights_list = []
        for b_idx in range(len(bins) - 1):
            left = bins[b_idx]
            right = bins[b_idx + 1]
            
            bin_mask = (feat_vals > left) & (feat_vals <= right)
            bin_labels = y_train[bin_mask]
            
            s_b = int((bin_labels == 1).sum())
            f_b = int((bin_labels == 0).sum())
            
            p_success = (s_b + 1.0) / (n_success + float(len(bins) - 1))
            p_failure = (f_b + 1.0) / (n_failure + float(len(bins) - 1))
            
            w = math.log(p_success / p_failure)
            weights_list.append((float(left), float(right), float(w)))
            
        feature_weights[feat] = weights_list
        
    return feature_bins, feature_weights


def main():
    parser = argparse.ArgumentParser(description="Bayesian Weight Optimization (BWO)")
    parser.add_argument("--symbol", type=str, help="Target a single symbol for BWO tuning")
    parser.add_argument("--watchlist", type=str, default="NIFTY 50", help="Watchlist of symbols to tune (default: NIFTY 50)")
    args = parser.parse_args()
    
    if args.symbol:
        symbols = [args.symbol.upper()]
        print(f"BWO: Targeted optimization for single symbol {symbols[0]}...")
    else:
        symbols = get_watchlist_symbols(args.watchlist)
        print(f"BWO: Loaded {len(symbols)} symbols from watchlist '{args.watchlist}'.")
    
    # Cache ledger for all symbols
    print("Caching DivergenceEngine ledgers...")
    cached_ledgers = {}
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            cached_ledgers[sym] = res.ledger
        except Exception as e:
            print(f"Error loading {sym}: {e}")
            
    print(f"Successfully loaded {len(cached_ledgers)} symbol ledgers.")
    
    optimal_overrides = {}
    signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()
    
    for sym in symbols:
        if sym not in cached_ledgers:
            continue
            
        ledger = cached_ledgers[sym]
        print(f"\n--- Training Bayesian Model for {sym} ---")
        
        # Label candidates using simulated trades with actual exit logic
        candidates, labels = label_candidate_bars_sim(sym, ledger, signal, exit_cfg)
        num_success = sum(1 for v in labels.values() if v == 1)
        num_failure = sum(1 for v in labels.values() if v == 0)
        print(f"Candidate inflections: {len(candidates)} (Success: {num_success}, Failure: {num_failure})")
        
        # Fit model weights
        feature_bins, feature_weights = train_bayesian_model(ledger, candidates, labels)
        
        # Build custom bayesian config
        custom_bayesian_cfg = SavgolCTSEntryConfig()
        custom_bayesian_cfg.cooldown_enabled = False
        
        # Disable legacy paths
        custom_bayesian_cfg.cdvl_cts.enabled = False
        custom_bayesian_cfg.universal_cross.enabled = False
        custom_bayesian_cfg.trend_pullback_enabled = False
        custom_bayesian_cfg.flow_momentum.enabled = False
        custom_bayesian_cfg.coherent_pullback.enabled = False
        custom_bayesian_cfg.anchor_shock_pullback.enabled = False
        custom_bayesian_cfg.springboard.enabled = False
        custom_bayesian_cfg.oversold_decel.enabled = False
        
        # Enable Custom Bayesian path
        custom_bayesian_cfg.custom_bayesian.enabled = True
        custom_bayesian_cfg.custom_bayesian.feature_bins = feature_bins
        custom_bayesian_cfg.custom_bayesian.feature_weights = feature_weights
        
        # Sweep score threshold: -4.0 to +4.0 in steps of 0.1
        thresholds = np.arange(-4.0, 4.01, 0.1)
        best_threshold = 0.0
        best_trades_count = 0
        best_avg_pnl = -99.0
        best_score = -99999.0
        
        passing_thresholds = []
        
        for th in thresholds:
            test_cfg = copy.deepcopy(custom_bayesian_cfg)
            test_cfg.custom_bayesian.score_threshold = float(th)
            
            trades = simulate_trades(sym, ledger, test_cfg, exit_cfg, signal)
            trades_2019 = [t for t in trades if t.entry_date >= "2019-01-01"]
            
            if trades_2019:
                pnls = [t.pnl_pct for t in trades_2019]
                avg_pnl = np.mean(pnls)
                sl_hits = sum(1 for t in trades_2019 if t.mae_pct >= 8.0)
                sl_ratio = sl_hits / len(trades_2019)
                
                if avg_pnl > 5.0:
                    # Score function: penalize SL hits heavily, reward trade counts up to 15, reward avg_pnl
                    score = -1000.0 * sl_ratio + 10.0 * min(len(trades_2019), 15) + avg_pnl
                    passing_thresholds.append({
                        "threshold": th,
                        "trades": len(trades_2019),
                        "avg_pnl": avg_pnl,
                        "sl_hits": sl_hits,
                        "sl_ratio": sl_ratio,
                        "score": score
                    })
                    
        if passing_thresholds:
            # Sort by score descending
            passing_thresholds.sort(key=lambda x: x["score"], reverse=True)
            best = passing_thresholds[0]
            best_threshold = best["threshold"]
            best_trades_count = best["trades"]
            best_avg_pnl = best["avg_pnl"]
            print(f"  Optimal threshold found: {best_threshold:.2f} | Trades: {best_trades_count} | Avg P&L: {best_avg_pnl:.2f}% | SL Hits: {best['sl_hits']}")
        else:
            # Fallback: find threshold that maximizes avg_pnl
            fallback_runs = []
            for th in thresholds:
                test_cfg = copy.deepcopy(custom_bayesian_cfg)
                test_cfg.custom_bayesian.score_threshold = float(th)
                trades = simulate_trades(sym, ledger, test_cfg, exit_cfg, signal)
                trades_2019 = [t for t in trades if t.entry_date >= "2019-01-01"]
                if trades_2019:
                    pnls = [t.pnl_pct for t in trades_2019]
                    fallback_runs.append({
                        "threshold": th,
                        "trades": len(trades_2019),
                        "avg_pnl": np.mean(pnls)
                    })
            if fallback_runs:
                fallback_runs.sort(key=lambda x: x["avg_pnl"], reverse=True)
                best = fallback_runs[0]
                best_threshold = best["threshold"]
                best_trades_count = best["trades"]
                best_avg_pnl = best["avg_pnl"]
                print(f"  WARNING: Could not achieve >5% P&L. Fallback threshold: {best_threshold:.2f} | Trades: {best_trades_count} | Avg P&L: {best_avg_pnl:.2f}%")
            else:
                best_threshold = 0.0
                best_trades_count = 0
                best_avg_pnl = 0.0
                print(f"  WARNING: No trades found at any threshold for {sym}")
                
        optimal_overrides[sym] = {
            "enabled": True,
            "score_threshold": float(best_threshold),
            "feature_bins": feature_bins,
            "feature_weights": feature_weights,
            "trades": best_trades_count,
            "avg_pnl": best_avg_pnl
        }

    # Print overall results summary
    print("\n" + "="*50)
    print("BAYESIAN WEIGHT TRAINING SUMMARY (VOLATILITY-ADJUSTED)")
    print("="*50)
    success_count = 0
    total_symbols = 0
    
    for sym, details in optimal_overrides.items():
        avg_p = details["avg_pnl"]
        tr = details["trades"]
        pnl_str = f"{avg_p:.2f}%" if tr > 0 else "N/A"
        print(f"Symbol: {sym:<15} | Trades: {tr:<4} | Avg P&L: {pnl_str:<8}")
        
        if tr > 0:
            total_symbols += 1
            if avg_p >= 5.0:
                success_count += 1
                
    print(f"\nOptimized symbols with Trades: {total_symbols}")
    print(f"Symbols with Avg P&L >= 5.0%: {success_count} / {total_symbols} ({success_count/total_symbols*100:.1f}%)")
    
    from src.database import BW_CONFIGS_DIR
    
    # Generate and write individual JSON files
    print(f"\nSerializing Bayesian Weights (BW) configs directly to {BW_CONFIGS_DIR}...")
    for sym, details in optimal_overrides.items():
        config_dict = {
            "trend_pullback_enabled": False,
            "cdvl_cts": {"enabled": False},
            "universal_cross": {"enabled": False},
            "flow_momentum": {"enabled": False},
            "coherent_pullback": {"enabled": False},
            "anchor_shock_pullback": {"enabled": False},
            "springboard": {"enabled": False},
            "oversold_decel": {"enabled": False},
            "custom_bayesian": {
                "enabled": True,
                "score_threshold": float(details["score_threshold"]),
                "feature_bins": {},
                "feature_weights": {}
            }
        }
        
        # Serialize feature bins (convert inf to strings for valid JSON)
        for feat, bins in details["feature_bins"].items():
            bins_list = []
            for b in bins:
                if math.isinf(b):
                    bins_list.append("-inf" if b < 0 else "inf")
                else:
                    bins_list.append(float(b))
            config_dict["custom_bayesian"]["feature_bins"][feat] = bins_list
            
        # Serialize feature weights (convert inf to strings for valid JSON)
        for feat, weights in details["feature_weights"].items():
            weights_list = []
            for left, right, w in weights:
                l_val = "-inf" if math.isinf(left) and left < 0 else float(left)
                r_val = "inf" if math.isinf(right) and right > 0 else float(right)
                weights_list.append([l_val, r_val, float(w)])
            config_dict["custom_bayesian"]["feature_weights"][feat] = weights_list
            
        # Write to JSON file
        out_path = Path(BW_CONFIGS_DIR) / f"{sym}.json"
        with open(out_path, "w") as f:
            json.dump(config_dict, f, indent=4)
        print(f"Saved BW override directly to {out_path}")

if __name__ == "__main__":
    main()
