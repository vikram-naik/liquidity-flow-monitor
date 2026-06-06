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
from scripts.train_symbol_weights import train_bayesian_model, simulate_single_trade

FALLBACKS = ["HDFCBANK", "HINDUNILVR", "NESTLEIND", "ONGC", "TCS", "WIPRO"]

def union_label_candidate_bars(sym, ledger, signal, exit_cfg):
    n = len(ledger)
    records = ledger.to_dict("records")
    candidates = []
    labels = {}
    
    close_vals = ledger["close"].values
    atr_vals = ledger["atr_20"].values
    valid_atr_pct = [atr / close * 100.0 for close, atr in zip(close_vals, atr_vals) if not np.isnan(close) and not np.isnan(atr) and close > 0]
    avg_atr_pct = np.mean(valid_atr_pct) if valid_atr_pct else 2.0
    
    success_threshold = max(1.5, 1.2 * avg_atr_pct)
    drawdown_limit = 7.0 # Avoid hard stops (8.0%)
    
    # Configure config to enable ALL legacy paths (without custom_bayesian)
    legacy_cfg = SavgolCTSEntryConfig()
    legacy_cfg.cooldown_enabled = False
    legacy_cfg.cdvl_cts.enabled = True
    legacy_cfg.universal_cross.enabled = True
    legacy_cfg.trend_pullback_enabled = True
    legacy_cfg.flow_momentum.enabled = True
    legacy_cfg.coherent_pullback.enabled = True
    legacy_cfg.anchor_shock_pullback.enabled = True
    legacy_cfg.springboard.enabled = True
    legacy_cfg.oversold_decel.enabled = True
    legacy_cfg.custom_bayesian.enabled = False
    
    for i in range(1, n - 2):
        row = records[i]
        prev_row = records[i - 1]
        
        # Check if any legacy path accepts the entry
        passed, intensity, meta = signal.check_entry(row, prev_row, legacy_cfg, records, i)
        if not passed:
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
        print(f"\n==================== UNION SWEEPING {sym} ====================")
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            ledger = res.ledger
            
            candidates, labels = union_label_candidate_bars(sym, ledger, signal, exit_cfg)
            num_success = sum(1 for v in labels.values() if v == 1)
            num_failure = sum(1 for v in labels.values() if v == 0)
            print(f"Candidates: {len(candidates)} (Success: {num_success}, Failure: {num_failure})")
            
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
