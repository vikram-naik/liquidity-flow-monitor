import sys
import numpy as np
import pandas as pd
from pathlib import Path

# Add root folder to sys.path
sys.path.insert(0, "/home/vn/python-projects/liquidity-flow-monitor")

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from scripts.walk_forward import simulate_trades
from scratch.train_symbol_weights import label_candidate_bars, train_bayesian_model

UNDERPERFORMING = [
    "BAJFINANCE", "CIPLA", "ETERNAL", "HDFCBANK", "HINDALCO", "HINDUNILVR", 
    "MARUTI", "NESTLEIND", "SBILIFE", "TCS", "TATASTEEL", "TECHM", "WIPRO"
]

def main():
    signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()
    
    for sym in UNDERPERFORMING:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            ledger = res.ledger
            
            candidates, labels = label_candidate_bars(ledger)
            feature_bins, feature_weights = train_bayesian_model(ledger, candidates, labels)
            
            custom_bayesian_cfg = SavgolCTSEntryConfig()
            custom_bayesian_cfg.cooldown_enabled = False
            
            # Disable all legacy entry paths
            custom_bayesian_cfg.cdvl_cts.enabled = False
            custom_bayesian_cfg.universal_cross.enabled = False
            custom_bayesian_cfg.trend_pullback_enabled = False
            custom_bayesian_cfg.flow_momentum.enabled = False
            custom_bayesian_cfg.coherent_pullback.enabled = False
            custom_bayesian_cfg.anchor_shock_pullback.enabled = False
            custom_bayesian_cfg.springboard.enabled = False
            custom_bayesian_cfg.oversold_decel.enabled = False
            
            # Enable Custom Bayesian entry path
            custom_bayesian_cfg.custom_bayesian.enabled = True
            custom_bayesian_cfg.custom_bayesian.feature_bins = feature_bins
            custom_bayesian_cfg.custom_bayesian.feature_weights = feature_weights
            
            print(f"\n================= {sym} =================")
            # Let's try wider threshold sweep: -4.0 to +4.0
            thresholds = np.arange(-4.0, 4.01, 0.1)
            results = []
            for th in thresholds:
                custom_bayesian_cfg.custom_bayesian.score_threshold = float(th)
                trades = simulate_trades(sym, ledger, custom_bayesian_cfg, exit_cfg, signal)
                trades_2019 = [t for t in trades if t.entry_date >= "2019-01-01"]
                if trades_2019:
                    pnls = [t.pnl_pct for t in trades_2019]
                    avg_pnl = np.mean(pnls)
                    results.append((th, len(trades_2019), avg_pnl))
            
            df = pd.DataFrame(results, columns=["threshold", "trades", "avg_pnl"])
            print(df.sort_values("avg_pnl", ascending=False).head(10).to_string(index=False))
            
        except Exception as e:
            print(f"Error analyzing {sym}: {e}")

if __name__ == "__main__":
    main()
