import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from scripts.walk_forward import simulate_trades

def main():
    sym = "ITC"
    engine = DivergenceEngine(sym)
    df = engine.run().ledger
    
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    
    trades = simulate_trades(sym, df, entry_cfg, exit_cfg, signal)
    
    print(f"Total trades found: {len(trades)}")
    print("=" * 100)
    for i, t in enumerate(trades):
        pnl = (t.exit_price / t.entry_price - 1) * 100.0 if t.exit_price and t.entry_price else 0.0
        print(f"Trade #{i+1}:")
        print(f"  Entry Date : {t.entry_date}")
        print(f"  Entry Price: {t.entry_price}")
        print(f"  Exit Date  : {t.exit_date}")
        print(f"  Exit Price : {t.exit_price}")
        print(f"  Exit Reason: {t.exit_reason}")
        print(f"  PnL %      : {pnl:.2f}%")
        print(f"  Duration   : {t.duration if hasattr(t, 'duration') else 'N/A'} bars")
        print("-" * 100)

if __name__ == "__main__":
    main()
