import sys
import numpy as np
import pandas as pd
from pathlib import Path
import copy
import math

sys.path.insert(0, "/home/vn/python-projects/liquidity-flow-monitor")

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from scripts.walk_forward import simulate_trades
from scripts.train_symbol_weights import simulate_single_trade

FALLBACKS = ["HDFCBANK", "HINDUNILVR", "NESTLEIND", "ONGC", "TCS", "WIPRO"]

# 13 features
FEATURES = [
    "cwc", "psz_v", "fas", "cts_accel", "pdd_30", "pdd_120",
    "range_pos_10", "range_pos_63", "range_pos_252", "dv_shock", "esr", "base_tightness", "cts"
]

def label_candidates_custom(sym, ledger, signal, exit_cfg, success_mult, dd_limit):
    n = len(ledger)
    records = ledger.to_dict("records")
    candidates = []
    labels = {}
    
    close_vals = ledger["close"].values
    atr_vals = ledger["atr_20"].values
    valid_atr_pct = [atr / close * 100.0 for close, atr in zip(close_vals, atr_vals) if not np.isnan(close) and not np.isnan(atr) and close > 0]
    avg_atr_pct = np.mean(valid_atr_pct) if valid_atr_pct else 2.0
    
    success_threshold = max(2.0, success_mult * avg_atr_pct)
    
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
        
        if pnl_pct >= success_threshold and mae_pct < dd_limit:
            outcome = 1
        elif pnl_pct <= 0.0 or mae_pct >= dd_limit:
            outcome = 0
        else:
            outcome = None
            
        labels[i] = outcome
        
    return candidates, labels, success_threshold

def train_bayesian_model_custom(ledger, candidates, labels, num_bins):
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

def main():
    signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()

    # Sweep settings
    num_bins_choices = [3, 4]
    success_mult_choices = [1.2, 1.5, 1.8]
    dd_limit_choices = [5.0, 6.0, 7.0]

    for sym in FALLBACKS:
        print(f"\n==================== OPTIMIZING {sym} ====================")
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            ledger = res.ledger
            
            best_run = None
            best_avg = -99.0
            
            for num_bins in num_bins_choices:
                for success_mult in success_mult_choices:
                    for dd_limit in dd_limit_choices:
                        candidates, labels, s_t = label_candidates_custom(
                            sym, ledger, signal, exit_cfg, success_mult, dd_limit
                        )
                        num_success = sum(1 for v in labels.values() if v == 1)
                        num_failure = sum(1 for v in labels.values() if v == 0)
                        
                        if num_success == 0 or num_failure == 0:
                            continue
                            
                        feature_bins, feature_weights = train_bayesian_model_custom(
                            ledger, candidates, labels, num_bins
                        )
                        
                        cfg = SavgolCTSEntryConfig()
                        cfg.cooldown_enabled = False
                        cfg.cdvl_cts.enabled = False
                        cfg.universal_cross.enabled = False
                        cfg.trend_pullback_enabled = False
                        cfg.flow_momentum.enabled = False
                        cfg.coherent_pullback.enabled = False
                        cfg.anchor_shock_pullback.enabled = False
                        cfg.springboard.enabled = False
                        cfg.oversold_decel.enabled = False
                        
                        cfg.custom_bayesian.enabled = True
                        cfg.custom_bayesian.feature_bins = feature_bins
                        cfg.custom_bayesian.feature_weights = feature_weights
                        
                        for th in np.arange(-4.0, 8.01, 0.2):
                            cfg.custom_bayesian.score_threshold = float(th)
                            trades = simulate_trades(sym, ledger, cfg, exit_cfg, signal)
                            trades_2019 = [t for t in trades if t.entry_date >= "2019-01-01"]
                            if trades_2019:
                                pnls = [t.pnl_pct for t in trades_2019]
                                avg_pnl = np.mean(pnls)
                                sl_hits = sum(1 for t in trades_2019 if t.mae_pct >= 8.0)
                                if avg_pnl > best_avg:
                                    best_avg = avg_pnl
                                    best_run = {
                                        "num_bins": num_bins,
                                        "success_mult": success_mult,
                                        "dd_limit": dd_limit,
                                        "threshold": th,
                                        "trades": len(trades_2019),
                                        "sl_hits": sl_hits,
                                        "avg_pnl": avg_pnl
                                    }
                                    
            if best_run:
                print(f"BEST RUN FOR {sym}:")
                print(f"  Bins: {best_run['num_bins']} | Success Mult: {best_run['success_mult']} | DD Limit: {best_run['dd_limit']}")
                print(f"  Threshold: {best_run['threshold']:.2f} | Trades: {best_run['trades']} | Avg P&L: {best_run['avg_pnl']:.2f}% | SL Hits: {best_run['sl_hits']}")
            else:
                print(f"No valid run found for {sym}")
        except Exception as e:
            print(f"Error optimizing {sym}: {e}")

if __name__ == "__main__":
    main()
