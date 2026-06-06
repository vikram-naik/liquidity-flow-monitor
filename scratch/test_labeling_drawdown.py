import sys
import numpy as np
import pandas as pd
from pathlib import Path
import copy

sys.path.insert(0, "/home/vn/python-projects/liquidity-flow-monitor")

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from scripts.walk_forward import simulate_trades
from scratch.train_symbol_weights import label_candidate_bars_sim, train_bayesian_model

FALLBACKS = ["HDFCBANK", "HINDUNILVR", "NESTLEIND", "ONGC", "TCS", "WIPRO"]

def modified_label_candidate_bars_sim(sym, ledger, signal, exit_cfg):
    n = len(ledger)
    records = ledger.to_dict("records")
    candidates = []
    labels = {}
    
    close_vals = ledger["close"].values
    atr_vals = ledger["atr_20"].values
    valid_atr_pct = [atr / close * 100.0 for close, atr in zip(close_vals, atr_vals) if not np.isnan(close) and not np.isnan(atr) and close > 0]
    avg_atr_pct = np.mean(valid_atr_pct) if valid_atr_pct else 2.0
    
    # Loosen drawdown limit so we don't penalize normal small drawdowns, but still avoid hard stops (8%)
    success_threshold = max(1.5, 1.2 * avg_atr_pct)
    drawdown_limit = 7.0 # Avoid hard stops (8.0%)
    
    from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend
    from scratch.train_symbol_weights import simulate_single_trade
    
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

def main():
    signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()

    for sym in FALLBACKS:
        print(f"\n==================== SWEEPING {sym} ====================")
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            ledger = res.ledger
            
            candidates, labels = modified_label_candidate_bars_sim(sym, ledger, signal, exit_cfg)
            feature_bins, feature_weights = train_bayesian_model(ledger, candidates, labels)
            
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
            
            best_avg = -99.0
            best_th = 0.0
            best_trades = 0
            best_sl = 0
            
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
                        best_th = th
                        best_trades = len(trades_2019)
                        best_sl = sl_hits
                    if avg_pnl >= 5.0:
                        print(f"PASS -> Threshold: {th:5.1f} | Trades: {len(trades_2019):3d} | Avg P&L: {avg_pnl:6.2f}% | SL Hits: {sl_hits}")
            print(f"Best: Threshold {best_th:.1f} | Avg P&L: {best_avg:.2f}% | Trades: {best_trades} | SL Hits: {best_sl}")
        except Exception as e:
            print(f"Error sweeping {sym}: {e}")

if __name__ == "__main__":
    main()
