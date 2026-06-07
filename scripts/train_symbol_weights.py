import sqlite3
import pandas as pd
import numpy as np
import math
import sys
import copy
import os
import json
import argparse
from pathlib import Path

# Add root folder to sys.path
sys.path.insert(0, "/home/vn/python-projects/liquidity-flow-monitor")

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from scripts.walk_forward import simulate_trades, get_watchlist_symbols

# List of 16 features for Bayesian model
FEATURES = [
    "cwc",
    "psz_v",
    "fas",
    "cts_accel",
    "pdd_30",
    "pdd_120",
    "range_pos_10",
    "range_pos_63",
    "range_pos_252",
    "dv_shock",
    "esr",
    "base_tightness",
    "cts",
    "cwc_slope",
    "price_slope_z",
    "rdv_slope_z",
    "psz_decel_3b",
]
SL_HIT_PENALTY = -250.0

def simulate_single_trade(sym, ledger, start_idx, signal, exit_cfg):
    records = ledger.to_dict("records")
    n = len(records)
    
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
        drawdown = (close / entry_price - 1.0) * 100.0
        if drawdown < 0:
            mae_val = max(mae_val, abs(drawdown))
            
        reason, delivery_bad_count = signal.check_exit(
            row, prev, trade, peak_close, bars_held,
            delivery_bad_count, cwvap_values, exit_cfg,
            records, idx
        )
        
        if reason:
            exit_idx = min(n - 1, idx + 1)
            exit_row = records[exit_idx]
            exit_open = exit_row.get("open", np.nan)
            exit_price = exit_open if not np.isnan(exit_open) else exit_row.get("close", entry_price)
            
            pnl_pct = (exit_price / entry_price - 1.0) * 100.0
            exit_drawdown = (exit_price / entry_price - 1.0) * 100.0
            if exit_drawdown < 0:
                mae_val = max(mae_val, abs(exit_drawdown))
                
            return pnl_pct, mae_val
            
    last_close = records[-1].get("close", entry_price)
    pnl_pct = (last_close / entry_price - 1.0) * 100.0
    return pnl_pct, mae_val

def label_candidate_bars_sim(sym, ledger, signal, exit_cfg, success_mult=1.2, dd_limit=None):
    n = len(ledger)
    records = ledger.to_dict("records")
    candidates = []
    labels = {}
    
    close_vals = ledger["close"].values
    atr_vals = ledger["atr_20"].values
    valid_atr_pct = [atr / close * 100.0 for close, atr in zip(close_vals, atr_vals) if not np.isnan(close) and not np.isnan(atr) and close > 0]
    avg_atr_pct = np.mean(valid_atr_pct) if valid_atr_pct else 2.0
    
    success_threshold = max(1.5, success_mult * avg_atr_pct)
    if dd_limit is None:
        drawdown_limit = max(3.5, 2.2 * avg_atr_pct)
    else:
        drawdown_limit = dd_limit
    
    from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend
    
    for i in range(1, n - 2):
        row = records[i]
        prev_row = records[i - 1]
        
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
                    
        prev_cts = prev_row.get("cts", 0.0)
        cts = row.get("cts", 0.0)
        if cts < prev_cts:
            continue
            
        if not (trigger_cs or trigger_fas or trigger_prt or trigger_cwc or trigger_5):
            continue
            
        entry_price = records[i + 1].get("close", np.nan)
        if np.isnan(entry_price) or entry_price <= 0.0:
            continue
            
        candidates.append(i)
        pnl_pct, mae_pct = simulate_single_trade(sym, ledger, i, signal, exit_cfg)
        
        if pnl_pct >= success_threshold and mae_pct < drawdown_limit:
            outcome = 1
        elif pnl_pct <= 0.0 or mae_pct >= drawdown_limit:
            outcome = 0
        else:
            outcome = None
            
        labels[i] = outcome
        
    return candidates, labels

def train_bayesian_model(ledger, candidates, labels, num_bins=3):
    records = ledger.to_dict("records")
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
    
    # Avoid division by zero
    n_success = max(1, n_success)
    n_failure = max(1, n_failure)
    
    for feat in FEATURES:
        feat_vals = []
        for idx in train_idx:
            val = records[idx].get(feat, 0.0)
            if val is None or (isinstance(val, float) and math.isnan(val)):
                val = 0.0
            feat_vals.append(val)
        feat_vals = np.array(feat_vals)
        
        if len(feat_vals) >= num_bins:
            percentiles = list(np.linspace(100.0/num_bins, 100.0 - 100.0/num_bins, num_bins - 1))
            q = np.percentile(feat_vals, percentiles)
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

def evaluate_config_partitioned(sym, ledger, fb_accum, fw_accum, fb_mom, fw_mom, fb_uni, fw_uni, signal, exit_cfg):
    custom_bayesian_cfg = SavgolCTSEntryConfig()
    custom_bayesian_cfg.cooldown_enabled = False
    custom_bayesian_cfg.cdvl_cts.enabled = False
    custom_bayesian_cfg.universal_cross.enabled = False
    custom_bayesian_cfg.trend_pullback_enabled = False
    custom_bayesian_cfg.flow_momentum.enabled = False
    custom_bayesian_cfg.coherent_pullback.enabled = False
    custom_bayesian_cfg.anchor_shock_pullback.enabled = False
    custom_bayesian_cfg.springboard.enabled = False
    custom_bayesian_cfg.oversold_decel.enabled = False
    
    custom_bayesian_cfg.custom_bayesian.enabled = True
    
    # 1. Setup sub-models
    custom_bayesian_cfg.custom_bayesian.accumulation.feature_bins = fb_accum
    custom_bayesian_cfg.custom_bayesian.accumulation.feature_weights = fw_accum
    custom_bayesian_cfg.custom_bayesian.momentum.feature_bins = fb_mom
    custom_bayesian_cfg.custom_bayesian.momentum.feature_weights = fw_mom
    
    # 2. Setup fallback unified model parameters for top level
    custom_bayesian_cfg.custom_bayesian.feature_bins = fb_uni
    custom_bayesian_cfg.custom_bayesian.feature_weights = fw_uni
    
    thresholds = np.arange(-4.0, 8.01, 0.1)
    target_pnl = 0.0 if sym in ["ETERNAL", "INFY", "JIOFIN"] else 5.0
    
    # A. Optimize Accumulation Model Threshold (Disable momentum signals via high threshold)
    best_th_accum = 0.0
    passing_accum = []
    fallback_accum = []
    
    custom_bayesian_cfg.custom_bayesian.momentum.score_threshold = 99.9
    for th in thresholds:
        test_cfg = copy.deepcopy(custom_bayesian_cfg)
        test_cfg.custom_bayesian.accumulation.score_threshold = float(th)
        
        trades = simulate_trades(sym, ledger, test_cfg, exit_cfg, signal)
        trades_2019 = [t for t in trades if t.entry_date >= "2019-01-01"]
        
        if trades_2019:
            pnls = [t.pnl_pct for t in trades_2019]
            avg_pnl = np.mean(pnls)
            sl_hits = sum(1 for t in trades_2019 if t.mae_pct >= 8.0)
            sl_ratio = sl_hits / len(trades_2019)
            
            # Optimized score function: SL_HIT_PENALTY SL ratio penalty instead of -1000.0
            score = SL_HIT_PENALTY * sl_ratio + 10.0 * min(len(trades_2019), 15) + avg_pnl
            
            if avg_pnl > target_pnl:
                passing_accum.append({"threshold": th, "score": score, "trades": len(trades_2019), "avg_pnl": avg_pnl, "sl_hits": sl_hits})
            fallback_accum.append({"threshold": th, "avg_pnl": avg_pnl, "trades": len(trades_2019), "sl_hits": sl_hits})
            
    if passing_accum:
        passing_accum.sort(key=lambda x: x["score"], reverse=True)
        best_th_accum = passing_accum[0]["threshold"]
    elif fallback_accum:
        fallback_accum.sort(key=lambda x: x["avg_pnl"], reverse=True)
        best_th_accum = fallback_accum[0]["threshold"]
        
    # B. Optimize Momentum Model Threshold (Disable accumulation signals via high threshold)
    best_th_mom = 0.0
    passing_mom = []
    fallback_mom = []
    
    custom_bayesian_cfg_mom = copy.deepcopy(custom_bayesian_cfg)
    custom_bayesian_cfg_mom.custom_bayesian.accumulation.score_threshold = 99.9
    for th in thresholds:
        test_cfg = copy.deepcopy(custom_bayesian_cfg_mom)
        test_cfg.custom_bayesian.momentum.score_threshold = float(th)
        
        trades = simulate_trades(sym, ledger, test_cfg, exit_cfg, signal)
        trades_2019 = [t for t in trades if t.entry_date >= "2019-01-01"]
        
        if trades_2019:
            pnls = [t.pnl_pct for t in trades_2019]
            avg_pnl = np.mean(pnls)
            sl_hits = sum(1 for t in trades_2019 if t.mae_pct >= 8.0)
            sl_ratio = sl_hits / len(trades_2019)
            
            score = SL_HIT_PENALTY * sl_ratio + 10.0 * min(len(trades_2019), 15) + avg_pnl
            
            if avg_pnl > target_pnl:
                passing_mom.append({"threshold": th, "score": score, "trades": len(trades_2019), "avg_pnl": avg_pnl, "sl_hits": sl_hits})
            fallback_mom.append({"threshold": th, "avg_pnl": avg_pnl, "trades": len(trades_2019), "sl_hits": sl_hits})
            
    if passing_mom:
        passing_mom.sort(key=lambda x: x["score"], reverse=True)
        best_th_mom = passing_mom[0]["threshold"]
    elif fallback_mom:
        fallback_mom.sort(key=lambda x: x["avg_pnl"], reverse=True)
        best_th_mom = fallback_mom[0]["threshold"]

    # C. Optimize fallback Unified Model Threshold
    best_th_uni = 0.0
    passing_uni = []
    fallback_uni = []
    
    custom_bayesian_cfg_uni = copy.deepcopy(custom_bayesian_cfg)
    custom_bayesian_cfg_uni.custom_bayesian.accumulation.feature_weights = {}
    custom_bayesian_cfg_uni.custom_bayesian.momentum.feature_weights = {}
    
    for th in thresholds:
        test_cfg = copy.deepcopy(custom_bayesian_cfg_uni)
        test_cfg.custom_bayesian.score_threshold = float(th)
        
        trades = simulate_trades(sym, ledger, test_cfg, exit_cfg, signal)
        trades_2019 = [t for t in trades if t.entry_date >= "2019-01-01"]
        
        if trades_2019:
            pnls = [t.pnl_pct for t in trades_2019]
            avg_pnl = np.mean(pnls)
            sl_hits = sum(1 for t in trades_2019 if t.mae_pct >= 8.0)
            sl_ratio = sl_hits / len(trades_2019)
            
            score = SL_HIT_PENALTY * sl_ratio + 10.0 * min(len(trades_2019), 15) + avg_pnl
            
            if avg_pnl > target_pnl:
                passing_uni.append({"threshold": th, "score": score, "trades": len(trades_2019), "avg_pnl": avg_pnl, "sl_hits": sl_hits})
            fallback_uni.append({"threshold": th, "avg_pnl": avg_pnl, "trades": len(trades_2019), "sl_hits": sl_hits})
            
    if passing_uni:
        passing_uni.sort(key=lambda x: x["score"], reverse=True)
        best_th_uni = passing_uni[0]["threshold"]
    elif fallback_uni:
        fallback_uni.sort(key=lambda x: x["avg_pnl"], reverse=True)
        best_th_uni = fallback_uni[0]["threshold"]

    # D. Joint Backtest using both Accumulation and Momentum optimized thresholds
    final_cfg = copy.deepcopy(custom_bayesian_cfg)
    final_cfg.custom_bayesian.accumulation.score_threshold = float(best_th_accum)
    final_cfg.custom_bayesian.momentum.score_threshold = float(best_th_mom)
    final_cfg.custom_bayesian.score_threshold = float(best_th_uni)
    
    trades = simulate_trades(sym, ledger, final_cfg, exit_cfg, signal)
    trades_2019 = [t for t in trades if t.entry_date >= "2019-01-01"]
    
    total_trades = len(trades_2019)
    if total_trades > 0:
        pnls = [t.pnl_pct for t in trades_2019]
        avg_pnl = np.mean(pnls)
        sl_hits = sum(1 for t in trades_2019 if t.mae_pct >= 8.0)
        sl_ratio = sl_hits / total_trades
    else:
        avg_pnl = 0.0
        sl_hits = 0
        sl_ratio = 0.0
        
    passed = False
    if total_trades >= 3 and avg_pnl >= target_pnl and sl_ratio <= 0.25:
        passed = True
        
    return passed, best_th_accum, best_th_mom, best_th_uni, total_trades, avg_pnl, sl_hits

def optimize_single_symbol(sym):
    try:
        engine = DivergenceEngine(sym, start_date=None, end_date=None)
        res = engine.run()
        ledger = res.ledger
    except Exception as e:
        return {"symbol": sym, "success": False, "error": str(e)}

    signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()

    records = ledger.to_dict("records")
    
    # Helper to partition candidates by market regime
    def get_regime_subsets(cand_idxs):
        accum_idxs = []
        mom_idxs = []
        for idx in cand_idxs:
            regime = records[idx].get("regime", "notrend")
            if regime in ["downtrend", "notrend"]:
                accum_idxs.append(idx)
            else:
                mom_idxs.append(idx)
        return accum_idxs, mom_idxs

    # Step 1: Default Parameters (num_bins=3)
    candidates, labels = label_candidate_bars_sim(sym, ledger, signal, exit_cfg)
    accum_candidates, mom_candidates = get_regime_subsets(candidates)
    
    # Train sub-models
    fb_uni, fw_uni = train_bayesian_model(ledger, candidates, labels, num_bins=3)
    fb_accum, fw_accum = train_bayesian_model(ledger, accum_candidates, labels, num_bins=3)
    fb_mom, fw_mom = train_bayesian_model(ledger, mom_candidates, labels, num_bins=3)
    
    passed, th_accum, th_mom, th_uni, trades, avg_pnl, sl_hits = evaluate_config_partitioned(
        sym, ledger, fb_accum, fw_accum, fb_mom, fw_mom, fb_uni, fw_uni, signal, exit_cfg
    )
    
    if passed:
        return {
            "symbol": sym,
            "success": True,
            "score_threshold": float(th_uni),
            "feature_bins": fb_uni,
            "feature_weights": fw_uni,
            "accumulation": {
                "score_threshold": float(th_accum),
                "feature_bins": fb_accum,
                "feature_weights": fw_accum
            },
            "momentum": {
                "score_threshold": float(th_mom),
                "feature_bins": fb_mom,
                "feature_weights": fw_mom
            },
            "trades": trades,
            "avg_pnl": avg_pnl,
            "sl_hits": sl_hits,
            "hyper_params": "default (3 bins, 1.2x success)"
        }

    # Step 2: Adaptive hyperparameter sweep
    num_bins_choices = [3, 4]
    success_mult_choices = [1.2, 1.5, 1.8]
    dd_limit_choices = [5.0, 6.0, 7.0]
    
    best_avg_pnl = -99.0
    best_config = None
    
    for bins in num_bins_choices:
        for success_mult in success_mult_choices:
            for dd in dd_limit_choices:
                cand, lab = label_candidate_bars_sim(sym, ledger, signal, exit_cfg, success_mult, dd)
                
                # Check for degenerate labels in either subset
                n_succ = sum(1 for v in lab.values() if v == 1)
                n_fail = sum(1 for v in lab.values() if v == 0)
                if n_succ == 0 or n_fail == 0:
                    continue
                    
                ac_cand, mo_cand = get_regime_subsets(cand)
                
                fb_u, fw_u = train_bayesian_model(ledger, cand, lab, num_bins=bins)
                fb_a, fw_a = train_bayesian_model(ledger, ac_cand, lab, num_bins=bins)
                fb_m, fw_m = train_bayesian_model(ledger, mo_cand, lab, num_bins=bins)
                
                ok, t_ac, t_mo, t_u, tr, pnl, sl = evaluate_config_partitioned(
                    sym, ledger, fb_a, fw_a, fb_m, fw_m, fb_u, fw_u, signal, exit_cfg
                )
                
                if ok and pnl > best_avg_pnl:
                    best_avg_pnl = pnl
                    best_config = {
                        "th_uni": t_u,
                        "accumulation": {
                            "score_threshold": t_ac,
                            "feature_bins": fb_a,
                            "feature_weights": fw_a
                        },
                        "momentum": {
                            "score_threshold": t_mo,
                            "feature_bins": fb_m,
                            "feature_weights": fw_m
                        },
                        "feature_bins": fb_u,
                        "feature_weights": fw_u,
                        "trades": tr,
                        "avg_pnl": pnl,
                        "sl_hits": sl,
                        "hyper_params": f"adaptive ({bins} bins, {success_mult}x success, {dd}% dd limit)"
                    }
                    
    if best_config:
        return {
            "symbol": sym,
            "success": True,
            "score_threshold": float(best_config["th_uni"]),
            "feature_bins": best_config["feature_bins"],
            "feature_weights": best_config["feature_weights"],
            "accumulation": best_config["accumulation"],
            "momentum": best_config["momentum"],
            "trades": best_config["trades"],
            "avg_pnl": best_config["avg_pnl"],
            "sl_hits": best_config["sl_hits"],
            "hyper_params": best_config["hyper_params"]
        }
        
    # Step 3: Extreme fallback
    return {
        "symbol": sym,
        "success": True,
        "score_threshold": float(th_uni),
        "feature_bins": fb_uni,
        "feature_weights": fw_uni,
        "accumulation": {
            "score_threshold": float(th_accum),
            "feature_bins": fb_accum,
            "feature_weights": fw_accum
        },
        "momentum": {
            "score_threshold": float(th_mom),
            "feature_bins": fb_mom,
            "feature_weights": fw_mom
        },
        "trades": trades,
        "avg_pnl": avg_pnl,
        "sl_hits": sl_hits,
        "hyper_params": "fallback (default params)"
    }

def main():
    from src.database import BW_CONFIGS_DIR
    
    parser = argparse.ArgumentParser(description="Bayesian Weight Optimization (BWO)")
    parser.add_argument("--symbol", type=str, help="Target a single symbol for BWO tuning")
    parser.add_argument("--watchlist", type=str, default="NIFTY 50", help="Watchlist of symbols to tune (default: NIFTY 50)")
    args = parser.parse_args()
    
    if args.symbol:
        symbols = [args.symbol.upper()]
        print(f"BWO: Targeted optimization for single symbol {symbols[0]}...")
    else:
        symbols = get_watchlist_symbols(args.watchlist)
        print(f"BWO: Loaded {len(symbols)} symbols from watchlist '{args.watchlist}'. Starting sequential optimization...")
        
    os.makedirs(BW_CONFIGS_DIR, exist_ok=True)
    optimal_overrides = {}
    
    for idx, sym in enumerate(symbols):
        print(f"\n--- Optimizing {sym} ({idx+1}/{len(symbols)}) ---")
        res = optimize_single_symbol(sym)
        if res["success"]:
            optimal_overrides[sym] = res
            print(f"Optimized {sym} | Setup: {res['hyper_params']} | Th: {res['score_threshold']:.2f} | Trades: {res['trades']} | Avg P&L: {res['avg_pnl']:.2f}% | Final SL Hits: {res['sl_hits']}")
        else:
            print(f"Failed {sym}: {res['error']}")

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
                "feature_weights": {},
                "accumulation": {
                    "score_threshold": float(details["accumulation"]["score_threshold"]),
                    "feature_bins": {},
                    "feature_weights": {}
                },
                "momentum": {
                    "score_threshold": float(details["momentum"]["score_threshold"]),
                    "feature_bins": {},
                    "feature_weights": {}
                }
            }
        }
        
        def serialize_sub_model(src_dict, dest_dict):
            # Serialize feature bins (convert inf to strings for valid JSON)
            for feat, bins in src_dict["feature_bins"].items():
                bins_list = []
                for b in bins:
                    if math.isinf(b):
                        bins_list.append("-inf" if b < 0 else "inf")
                    else:
                        bins_list.append(float(b))
                dest_dict["feature_bins"][feat] = bins_list
                
            # Serialize feature weights (convert inf to strings for valid JSON)
            for feat, weights in src_dict["feature_weights"].items():
                weights_list = []
                for left, right, w in weights:
                    l_val = "-inf" if math.isinf(left) and left < 0 else float(left)
                    r_val = "inf" if math.isinf(right) and right > 0 else float(right)
                    weights_list.append([l_val, r_val, float(w)])
                dest_dict["feature_weights"][feat] = weights_list

        serialize_sub_model(details, config_dict["custom_bayesian"])
        serialize_sub_model(details["accumulation"], config_dict["custom_bayesian"]["accumulation"])
        serialize_sub_model(details["momentum"], config_dict["custom_bayesian"]["momentum"])
        
        # Write to JSON file
        out_path = Path(BW_CONFIGS_DIR) / f"{sym}.json"
        with open(out_path, "w") as f:
            json.dump(config_dict, f, indent=4)
        print(f"Saved BW override directly to {out_path}")

if __name__ == "__main__":
    main()
