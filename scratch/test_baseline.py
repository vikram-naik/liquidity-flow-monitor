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
sys.path.insert(0, "/home/vn/.gemini/antigravity/brain/76d44142-6bbd-4b94-85d0-f7fb5caff696/scratch")
from optimal_symbol_overrides import SYMBOL_ENTRY_OVERRIDES as BASELINE_OVERRIDES

symbols = ["AXISBANK", "HDFCBANK", "HINDUNILVR", "NESTLEIND", "ONGC", "TCS", "WIPRO"]

def main():
    signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()
    
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            ledger = res.ledger
            
            # Load baseline overrides
            default_cfg = SavgolCTSEntryConfig()
            default_cfg.cooldown_enabled = False
            
            if sym in BASELINE_OVERRIDES:
                overrides = BASELINE_OVERRIDES[sym]
                if "trend_pullback_enabled" in overrides:
                    default_cfg.trend_pullback_enabled = overrides["trend_pullback_enabled"]
                for path in ["cdvl_cts", "universal_cross", "flow_momentum", "coherent_pullback", "anchor_shock_pullback", "springboard", "oversold_decel"]:
                    if path in overrides:
                        sub = getattr(default_cfg, path)
                        path_overrides = overrides[path]
                        if "enabled" in path_overrides:
                            sub.enabled = path_overrides["enabled"]
                        if "score_threshold" in path_overrides and hasattr(sub, "score_threshold"):
                            sub.score_threshold = path_overrides["score_threshold"]
                            
            trades = simulate_trades(sym, ledger, default_cfg, exit_cfg, signal)
            trades_2019 = [t for t in trades if t.entry_date >= "2019-01-01"]
            if trades_2019:
                pnls = [t.pnl_pct for t in trades_2019]
                avg_pnl = np.mean(pnls)
                print(f"Symbol: {sym:<15} | Trades: {len(trades_2019):<4} | Baseline Avg P&L: {avg_pnl:.2f}%")
            else:
                print(f"Symbol: {sym:<15} | Trades: 0 | Baseline Avg P&L: N/A")
        except Exception as e:
            print(f"Error for {sym}: {e}")

if __name__ == "__main__":
    main()
