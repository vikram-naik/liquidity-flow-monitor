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
from concurrent.futures import ProcessPoolExecutor, as_completed

# Add root folder to sys.path
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.savgol_cts.entries.custom_bayesian import check_bayesian_triggers
from src.trading.signals.savgol_cts.bwo_settings import (
    SL_HIT_PENALTY,
    MIN_TRADES,
    MAX_SL_RATIO,
    TARGET_PNL_DEFAULT,
    ZERO_TARGET_PNL_SYMBOLS,
    TRADE_CAP_SCORE,
    TRADE_REWARD_COEFF,
    SL_MAE_THRESHOLD,
    BWO_START_DATE,
    get_bwo_settings_for_symbol,
)
from scripts.walk_forward import simulate_trades, get_watchlist_symbols

# List of features for Bayesian model
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
    "fas_slope",
    "fas_slope_sum_5",
    "fas_min_10",
    "fas_max_10",
    "fas_slope_change_3",
]

# Custom expert bin boundaries for critical risk features
EXPERT_BINS = {
    "fas_slope_sum_5": [-float('inf'), -4.5, -1.5, float('inf')],
    "pdd_30": [-float('inf'), -5.5, -2.0, float('inf')],
    "base_tightness": [-float('inf'), 0.35, 0.40, 0.46, float('inf')],
}


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

def label_candidate_bars_sim(sym, ledger, signal, exit_cfg, success_mult=1.2, dd_limit=None, sl_mae_threshold=None):
    n = len(ledger)
    records = ledger.to_dict("records")
    candidates = []
    labels = {}
    
    if sl_mae_threshold is None:
        sl_mae_threshold = SL_MAE_THRESHOLD
        
    close_vals = ledger["close"].values
    atr_vals = ledger["atr_20"].values
    valid_atr_pct = [atr / close * 100.0 for close, atr in zip(close_vals, atr_vals) if not np.isnan(close) and not np.isnan(atr) and close > 0]
    avg_atr_pct = np.mean(valid_atr_pct) if valid_atr_pct else 2.0
    
    success_threshold = max(1.5, success_mult * avg_atr_pct)
    if dd_limit is None:
        drawdown_limit = max(3.5, 2.2 * avg_atr_pct)
    else:
        drawdown_limit = dd_limit
    
    for i in range(1, n - 2):
        row = records[i]
        prev_row = records[i - 1]
        
        if not check_bayesian_triggers(row, prev_row, records, i):
            continue
            
        entry_price = records[i + 1].get("close", np.nan)
        if np.isnan(entry_price) or entry_price <= 0.0:
            continue
            
        candidates.append(i)
        pnl_pct, mae_pct = simulate_single_trade(sym, ledger, i, signal, exit_cfg)
        
        # Check for catastrophic hard stop failures first to penalize them heavily
        if pnl_pct <= -sl_mae_threshold or mae_pct >= sl_mae_threshold:
            outcome = -1
        elif pnl_pct >= success_threshold and mae_pct < drawdown_limit:
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
    n_failure_normal = int((y_train == 0).sum())
    n_failure_hard = int((y_train == -1).sum())
    
    # Weight hard-stop failures by a factor of 5 to penalize those bins heavily
    HARD_STOP_MULT = 5.0
    n_failure = n_failure_normal + HARD_STOP_MULT * n_failure_hard
    
    # Avoid division by zero
    n_success = max(1, n_success)
    n_failure = max(1.0, n_failure)
    
    for feat in FEATURES:
        feat_vals = []
        for idx in train_idx:
            val = records[idx].get(feat, 0.0)
            if val is None or (isinstance(val, float) and math.isnan(val)):
                val = 0.0
            feat_vals.append(val)
        feat_vals = np.array(feat_vals)
        
        # Apply custom expert bin boundaries if defined for this feature
        if feat in EXPERT_BINS:
            bins = EXPERT_BINS[feat]
        else:
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
            f_b_normal = int((bin_labels == 0).sum())
            f_b_hard = int((bin_labels == -1).sum())
            f_b = f_b_normal + HARD_STOP_MULT * f_b_hard
            
            p_success = (s_b + 1.0) / (n_success + float(len(bins) - 1))
            p_failure = (f_b + 1.0) / (n_failure + float(len(bins) - 1))
            
            w = math.log(p_success / p_failure)
            
            # Catastrophic risk override for expert bins
            if feat in EXPERT_BINS and s_b == 0 and f_b_hard >= 1:
                w = -15.0
                
            # Universal risk bin penalties (soft gates)
            if feat == "fas_slope_sum_5" and left == -float('inf') and right == -4.5:
                w = min(w, -10.0)
            elif feat == "pdd_30" and left == -float('inf') and right == -5.5:
                w = min(w, -10.0)
            elif feat == "base_tightness" and left == 0.46 and right == float('inf'):
                w = min(w, -3.0)
                
            weights_list.append((float(left), float(right), float(w)))
            
        feature_weights[feat] = weights_list
        
    return feature_bins, feature_weights


def evaluate_config_partitioned(sym, ledger, fb_accum, fw_accum, fb_mom, fw_mom, fb_uni, fw_uni, signal, exit_cfg, bwo_set=None):
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
    
    thresholds = np.arange(1.2, 8.01, 0.1)
    
    if bwo_set is None:
        bwo_set = get_bwo_settings_for_symbol(sym)
        
    sl_hit_penalty = bwo_set["SL_HIT_PENALTY"]
    min_trades = bwo_set["MIN_TRADES"]
    max_sl_ratio = bwo_set["MAX_SL_RATIO"]
    target_pnl = bwo_set["TARGET_PNL"]
    trade_cap_score = bwo_set["TRADE_CAP_SCORE"]
    trade_reward_coeff = bwo_set["TRADE_REWARD_COEFF"]
    sl_mae_threshold = bwo_set["SL_MAE_THRESHOLD"]
    bwo_start_date = bwo_set["BWO_START_DATE"]
    
    # Use min(target_pnl, 2.0) as the threshold selection hurdle for Accumulation.
    # Elevate the selection hurdle for Momentum to min(target_pnl, 4.0) to filter out low-expectancy setups.
    hurdle_pnl_accum = min(target_pnl, 2.0)
    hurdle_pnl_mom = min(target_pnl, 4.0)
    
    # A. Optimize Accumulation Model Threshold (Disable momentum signals via high threshold)
    best_th_accum = 99.9
    passing_accum = []
    fallback_accum = []
    
    custom_bayesian_cfg.custom_bayesian.momentum.score_threshold = 99.9
    for th in thresholds:
        custom_bayesian_cfg.custom_bayesian.accumulation.score_threshold = float(th)
        trades = simulate_trades(sym, ledger, custom_bayesian_cfg, exit_cfg, signal)
        trades_bwo = [t for t in trades if t.entry_date >= bwo_start_date]
        
        if trades_bwo:
            pnls = [t.pnl_pct for t in trades_bwo]
            avg_pnl = np.mean(pnls)
            sl_hits = sum(1 for t in trades_bwo if t.mae_pct >= sl_mae_threshold)
            sl_ratio = sl_hits / len(trades_bwo)
            
            score = sl_hit_penalty * sl_ratio + trade_reward_coeff * min(len(trades_bwo), trade_cap_score) + avg_pnl
            
            if avg_pnl > hurdle_pnl_accum:
                passing_accum.append({"threshold": th, "score": score, "trades": len(trades_bwo), "avg_pnl": avg_pnl, "sl_hits": sl_hits})
            fallback_accum.append({"threshold": th, "avg_pnl": avg_pnl, "trades": len(trades_bwo), "sl_hits": sl_hits})
            
    if passing_accum:
        passing_accum.sort(key=lambda x: x["score"], reverse=True)
        for cand in passing_accum:
            cand_sl_ratio = cand["sl_hits"] / cand["trades"] if cand["trades"] > 0 else 0.0
            if cand["trades"] >= min_trades and cand["avg_pnl"] >= hurdle_pnl_accum and cand_sl_ratio <= max_sl_ratio:
                best_th_accum = cand["threshold"]
                break
    
    if best_th_accum == 99.9 and fallback_accum:
        fallback_accum.sort(key=lambda x: x["avg_pnl"], reverse=True)
        for cand in fallback_accum:
            cand_sl_ratio = cand["sl_hits"] / cand["trades"] if cand["trades"] > 0 else 0.0
            if cand["trades"] >= min_trades and cand["avg_pnl"] >= hurdle_pnl_accum and cand_sl_ratio <= max_sl_ratio:
                best_th_accum = cand["threshold"]
                break
        
    # B. Optimize Momentum Model Threshold (Disable accumulation signals via high threshold)
    best_th_mom = 99.9
    passing_mom = []
    fallback_mom = []
    
    custom_bayesian_cfg_mom = copy.deepcopy(custom_bayesian_cfg)
    custom_bayesian_cfg_mom.custom_bayesian.accumulation.score_threshold = 99.9
    for th in thresholds:
        custom_bayesian_cfg_mom.custom_bayesian.momentum.score_threshold = float(th)
        trades = simulate_trades(sym, ledger, custom_bayesian_cfg_mom, exit_cfg, signal)
        trades_bwo = [t for t in trades if t.entry_date >= bwo_start_date]
        
        if trades_bwo:
            pnls = [t.pnl_pct for t in trades_bwo]
            avg_pnl = np.mean(pnls)
            sl_hits = sum(1 for t in trades_bwo if t.mae_pct >= sl_mae_threshold)
            sl_ratio = sl_hits / len(trades_bwo)
            
            score = sl_hit_penalty * sl_ratio + trade_reward_coeff * min(len(trades_bwo), trade_cap_score) + avg_pnl
            
            if avg_pnl > hurdle_pnl_mom:
                passing_mom.append({"threshold": th, "score": score, "trades": len(trades_bwo), "avg_pnl": avg_pnl, "sl_hits": sl_hits})
            fallback_mom.append({"threshold": th, "avg_pnl": avg_pnl, "trades": len(trades_bwo), "sl_hits": sl_hits})
            
    if passing_mom:
        passing_mom.sort(key=lambda x: x["score"], reverse=True)
        for cand in passing_mom:
            cand_sl_ratio = cand["sl_hits"] / cand["trades"] if cand["trades"] > 0 else 0.0
            if cand["trades"] >= min_trades and cand["avg_pnl"] >= hurdle_pnl_mom and cand_sl_ratio <= max_sl_ratio:
                best_th_mom = cand["threshold"]
                break
                
    if best_th_mom == 99.9 and fallback_mom:
        fallback_mom.sort(key=lambda x: x["avg_pnl"], reverse=True)
        for cand in fallback_mom:
            cand_sl_ratio = cand["sl_hits"] / cand["trades"] if cand["trades"] > 0 else 0.0
            if cand["trades"] >= min_trades and cand["avg_pnl"] >= hurdle_pnl_mom and cand_sl_ratio <= max_sl_ratio:
                best_th_mom = cand["threshold"]
                break
 
    # C. Optimize fallback Unified Model Threshold
    best_th_uni = 0.0
    passing_uni = []
    fallback_uni = []
    
    custom_bayesian_cfg_uni = copy.deepcopy(custom_bayesian_cfg)
    custom_bayesian_cfg_uni.custom_bayesian.accumulation.feature_weights = {}
    custom_bayesian_cfg_uni.custom_bayesian.momentum.feature_weights = {}
    
    for th in thresholds:
        custom_bayesian_cfg_uni.custom_bayesian.score_threshold = float(th)
        trades = simulate_trades(sym, ledger, custom_bayesian_cfg_uni, exit_cfg, signal)
        trades_bwo = [t for t in trades if t.entry_date >= bwo_start_date]
        
        if trades_bwo:
            pnls = [t.pnl_pct for t in trades_bwo]
            avg_pnl = np.mean(pnls)
            sl_hits = sum(1 for t in trades_bwo if t.mae_pct >= sl_mae_threshold)
            sl_ratio = sl_hits / len(trades_bwo)
            
            score = sl_hit_penalty * sl_ratio + trade_reward_coeff * min(len(trades_bwo), trade_cap_score) + avg_pnl
            
            if avg_pnl > hurdle_pnl_accum:
                passing_uni.append({"threshold": th, "score": score, "trades": len(trades_bwo), "avg_pnl": avg_pnl, "sl_hits": sl_hits})
            fallback_uni.append({"threshold": th, "avg_pnl": avg_pnl, "trades": len(trades_bwo), "sl_hits": sl_hits})
            
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
    trades_bwo = [t for t in trades if t.entry_date >= bwo_start_date]
    
    total_trades = len(trades_bwo)
    if total_trades > 0:
        pnls = [t.pnl_pct for t in trades_bwo]
        avg_pnl = np.mean(pnls)
        sl_hits = sum(1 for t in trades_bwo if t.mae_pct >= sl_mae_threshold)
        sl_ratio = sl_hits / total_trades
    else:
        avg_pnl = 0.0
        sl_hits = 0
        sl_ratio = 0.0
        
    passed = False
    if total_trades >= min_trades and avg_pnl >= target_pnl and sl_ratio <= max_sl_ratio:
        passed = True
        
    return passed, best_th_accum, best_th_mom, best_th_uni, total_trades, avg_pnl, sl_hits


def evaluate_candidate_configuration(sym, ledger, bins, success_mult, dd, signal, exit_cfg, bwo_set):
    candidates, labels = label_candidate_bars_sim(sym, ledger, signal, exit_cfg, success_mult, dd, bwo_set["SL_MAE_THRESHOLD"])
    
    # Check for degenerate labels in either subset
    n_succ = sum(1 for v in labels.values() if v == 1)
    n_fail = sum(1 for v in labels.values() if v in [0, -1])
    if n_succ == 0 or n_fail == 0:
        return None
        
    records = ledger.to_dict("records")
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
        
    accum_candidates, mom_candidates = get_regime_subsets(candidates)
    
    fb_uni, fw_uni = train_bayesian_model(ledger, candidates, labels, num_bins=bins)
    fb_accum, fw_accum = train_bayesian_model(ledger, accum_candidates, labels, num_bins=bins)
    fb_mom, fw_mom = train_bayesian_model(ledger, mom_candidates, labels, num_bins=bins)
    
    # Precalculate triggers and scores for all three sub-models on a copied ledger DataFrame
    ledger_copy = ledger.copy()
    records_copy = ledger_copy.to_dict("records")
    
    precalc_triggers = [False] * len(records_copy)
    for idx in range(1, len(records_copy)):
        row = records_copy[idx]
        prev_row = records_copy[idx - 1]
        precalc_triggers[idx] = check_bayesian_triggers(row, prev_row, records_copy, idx)
        
    def get_model_score(row, feature_weights):
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
            "cwc_slope": row.get("cwc_slope", 0.0),
            "price_slope_z": row.get("price_slope_z", 0.0),
            "rdv_slope_z": row.get("rdv_slope_z", 0.0),
            "psz_decel_3b": row.get("psz_decel_3b", 0.0),
            "fas_slope": row.get("fas_slope", 0.0),
            "fas_slope_sum_5": row.get("fas_slope_sum_5", 0.0),
            "fas_min_10": row.get("fas_min_10", 0.0),
            "fas_max_10": row.get("fas_max_10", 0.0),
            "fas_slope_change_3": row.get("fas_slope_change_3", 0.0),
        }
        score = 0.0
        for feat, val in feat_vals.items():
            if val is None or (isinstance(val, float) and math.isnan(val)):
                val = 0.0
            weights = feature_weights.get(feat, [])
            for left, right, w in weights:
                if left < val <= right:
                    score += w
                    break
        return score

    precalc_accum = []
    precalc_mom = []
    precalc_uni = []
    
    for row in records_copy:
        precalc_accum.append(get_model_score(row, fw_accum))
        precalc_mom.append(get_model_score(row, fw_mom))
        precalc_uni.append(get_model_score(row, fw_uni))
        
    ledger_copy["precalc_bayesian_trigger"] = precalc_triggers
    ledger_copy["precalc_bayesian_score_accum"] = precalc_accum
    ledger_copy["precalc_bayesian_score_mom"] = precalc_mom
    ledger_copy["precalc_bayesian_score_uni"] = precalc_uni
    
    records_precalc = ledger_copy.to_dict("records")
    
    passed, th_accum, th_mom, th_uni, trades, avg_pnl, sl_hits = evaluate_config_partitioned(
        sym, records_precalc, fb_accum, fw_accum, fb_mom, fw_mom, fb_uni, fw_uni, signal, exit_cfg, bwo_set
    )
    
    return {
        "passed": passed,
        "th_accum": th_accum,
        "th_mom": th_mom,
        "th_uni": th_uni,
        "trades": trades,
        "avg_pnl": avg_pnl,
        "sl_hits": sl_hits,
        "fb_uni": fb_uni,
        "fw_uni": fw_uni,
        "fb_accum": fb_accum,
        "fw_accum": fw_accum,
        "fb_mom": fb_mom,
        "fw_mom": fw_mom
    }


def optimize_single_symbol(sym):
    try:
        engine = DivergenceEngine(sym, start_date=None, end_date=None)
        res = engine.run()
        ledger = res.ledger
    except Exception as e:
        return {"symbol": sym, "success": False, "error": str(e)}

    bwo_set = get_bwo_settings_for_symbol(sym)
    signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()

    configs_to_run = []
    
    # 1. Default config (dd=None uses dynamic ATR-based drawdown limit)
    configs_to_run.append({
        "bins": 3,
        "success_mult": 1.2,
        "dd": None,
        "label": "default (3 bins, 1.2x success)"
    })
    
    # 2. Sweep configs
    for bins in bwo_set["NUM_BINS_CHOICES"]:
        for success_mult in bwo_set["SUCCESS_MULT_CHOICES"]:
            for dd in bwo_set["DD_LIMIT_CHOICES"]:
                configs_to_run.append({
                    "bins": bins,
                    "success_mult": success_mult,
                    "dd": dd,
                    "label": f"adaptive ({bins} bins, {success_mult}x success, {dd}% dd limit)"
                })
                
    evaluated_results = []
    
    import multiprocessing
    current_proc = multiprocessing.current_process()
    
    if current_proc.name != "MainProcess":
        # Sequential execution fallback for child processes to avoid nested daemonic process errors
        for cfg in configs_to_run:
            try:
                res = evaluate_candidate_configuration(
                    sym, ledger, cfg["bins"], cfg["success_mult"], cfg["dd"], signal, exit_cfg, bwo_set
                )
                if res is not None:
                    res["label"] = cfg["label"]
                    evaluated_results.append(res)
            except Exception:
                pass
    else:
        # Parallel execution for main process run
        total = len(configs_to_run)
        
        # Check if tqdm is available
        try:
            from tqdm import tqdm
            use_tqdm = True
        except ImportError:
            use_tqdm = False
            
        with ProcessPoolExecutor() as executor:
            futures = {}
            for cfg in configs_to_run:
                fut = executor.submit(
                    evaluate_candidate_configuration,
                    sym, ledger, cfg["bins"], cfg["success_mult"], cfg["dd"], signal, exit_cfg, bwo_set
                )
                futures[fut] = cfg
                
            if use_tqdm:
                from concurrent.futures import as_completed
                # Display progress bar for MainProcess run
                for fut in tqdm(as_completed(futures), total=total, desc=f"Optimizing {sym}", unit="config"):
                    try:
                        res = fut.result()
                        if res is not None:
                            res["label"] = futures[fut]["label"]
                            evaluated_results.append(res)
                    except Exception:
                        pass
            else:
                for fut in futures:
                    try:
                        res = fut.result()
                        if res is not None:
                            res["label"] = futures[fut]["label"]
                            evaluated_results.append(res)
                    except Exception as e:
                        # Silently ignore errors on specific configurations during sweep
                        pass
            
    # Filter by basic trade count & safety constraints
    min_trades = bwo_set["MIN_TRADES"]
    max_sl_ratio = bwo_set["MAX_SL_RATIO"]
    
    valid_results = []
    for r in evaluated_results:
        sl_ratio = r["sl_hits"] / r["trades"] if r["trades"] > 0 else 0.0
        if r["trades"] >= min_trades and sl_ratio <= max_sl_ratio:
            valid_results.append(r)
            
    if not valid_results:
        if evaluated_results:
            best_res = evaluated_results[0]
        else:
            return {"symbol": sym, "success": False, "error": "No configurations could be evaluated (degenerate labels)"}
        dynamic_target_pnl = bwo_set["TARGET_PNL"]
    else:
        # Find best achieved P&L
        best_achieved_pnl = max(r["avg_pnl"] for r in valid_results)
        
        # Check if there is an explicit TARGET_PNL override in bwo_symbol_params.json
        import json
        from src.trading.signals.savgol_cts.bwo_settings import BWO_SYMBOL_PARAMS_PATH
        has_target_override = False
        if os.path.exists(BWO_SYMBOL_PARAMS_PATH):
            try:
                with open(BWO_SYMBOL_PARAMS_PATH, "r") as f:
                    overrides = json.load(f)
                if sym in overrides and "TARGET_PNL" in overrides[sym]:
                    has_target_override = True
            except Exception:
                pass
                
        if has_target_override:
            dynamic_target_pnl = bwo_set["TARGET_PNL"]
        else:
            if best_achieved_pnl >= 5.0:
                dynamic_target_pnl = 5.0
            elif best_achieved_pnl > 0.0:
                dynamic_target_pnl = max(2.0, best_achieved_pnl - 0.5)
            else:
                dynamic_target_pnl = 0.0
                
        # Filter to only keep configurations meeting the dynamic target P&L
        passing_results = [r for r in valid_results if r["avg_pnl"] >= dynamic_target_pnl]
        if not passing_results:
            passing_results = valid_results
            
        # Select highest BWO score winner
        def get_score(r):
            sl_ratio = r["sl_hits"] / r["trades"] if r["trades"] > 0 else 0.0
            return bwo_set["SL_HIT_PENALTY"] * sl_ratio + bwo_set["TRADE_REWARD_COEFF"] * min(r["trades"], bwo_set["TRADE_CAP_SCORE"]) + r["avg_pnl"]
            
        passing_results.sort(key=get_score, reverse=True)
        best_res = passing_results[0]
        
    return {
        "symbol": sym,
        "success": True,
        "score_threshold": float(best_res["th_uni"]),
        "feature_bins": best_res["fb_uni"],
        "feature_weights": best_res["fw_uni"],
        "accumulation": {
            "score_threshold": float(best_res["th_accum"]),
            "feature_bins": best_res["fb_accum"],
            "feature_weights": best_res["fw_accum"]
        },
        "momentum": {
            "score_threshold": float(best_res["th_mom"]),
            "feature_bins": best_res["fb_mom"],
            "feature_weights": best_res["fw_mom"]
        },
        "trades": best_res["trades"],
        "avg_pnl": best_res["avg_pnl"],
        "sl_hits": best_res["sl_hits"],
        "hyper_params": best_res["label"],
        "target_pnl": dynamic_target_pnl
    }


def main():
    from src.database import BW_CONFIGS_DIR
    
    parser = argparse.ArgumentParser(description="Bayesian Weight Optimization (BWO)")
    parser.add_argument("--symbol", type=str, help="Target a single symbol for BWO tuning")
    parser.add_argument("--watchlist", type=str, default="NIFTY 50", help="Watchlist of symbols to tune (default: NIFTY 50)")
    parser.add_argument("--sequential", action="store_true", help="Run BWO sequentially instead of parallel")
    args = parser.parse_args()
    
    if args.symbol:
        symbols = [args.symbol.upper()]
        print(f"BWO: Targeted optimization for single symbol {symbols[0]}...")
    else:
        symbols = get_watchlist_symbols(args.watchlist)
        
    os.makedirs(BW_CONFIGS_DIR, exist_ok=True)
    optimal_overrides = {}
    
    if args.symbol or args.sequential:
        # Run sequentially
        mode_str = "sequential" if args.sequential else "targeted sequential"
        if not args.symbol:
            print(f"BWO: Loaded {len(symbols)} symbols from watchlist '{args.watchlist}'. Starting {mode_str} optimization...")
        for idx, sym in enumerate(symbols):
            if not args.symbol:
                print(f"\n--- Optimizing {sym} ({idx+1}/{len(symbols)}) ---")
            res = optimize_single_symbol(sym)
            if res["success"]:
                optimal_overrides[sym] = res
                print(f"Optimized {sym} | Setup: {res['hyper_params']} | Th: {res['score_threshold']:.2f} | Trades: {res['trades']} | Avg P&L: {res['avg_pnl']:.2f}% | Final SL Hits: {res['sl_hits']}")
            else:
                print(f"Failed {sym}: {res['error']}")
    else:
        # Run in parallel
        print(f"BWO: Loaded {len(symbols)} symbols from watchlist '{args.watchlist}'. Starting parallel optimization...")
        with ProcessPoolExecutor() as executor:
            futures = {executor.submit(optimize_single_symbol, sym): sym for sym in symbols}
            for idx, fut in enumerate(as_completed(futures)):
                sym = futures[fut]
                try:
                    res = fut.result()
                    if res["success"]:
                        optimal_overrides[sym] = res
                        print(f"[{idx+1}/{len(symbols)}] Optimized {sym:<15} | Setup: {res['hyper_params']:<45} | Th: {res['score_threshold']:.2f} | Trades: {res['trades']:<3} | Avg P&L: {res['avg_pnl']:.2f}% | Final SL Hits: {res['sl_hits']}")
                    else:
                        print(f"[{idx+1}/{len(symbols)}] Failed {sym}: {res['error']}")
                except Exception as e:
                    print(f"[{idx+1}/{len(symbols)}] Future error for {sym}: {e}")

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
