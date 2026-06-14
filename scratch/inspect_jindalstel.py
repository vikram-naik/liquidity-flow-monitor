import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig, get_symbol_entry_config, get_symbol_exit_config
from scripts.walk_forward import simulate_trades

def main():
    symbol = "JINDALSTEL"
    print(f"Running DivergenceEngine for {symbol}...")
    engine = DivergenceEngine(symbol, start_date=None, end_date=None)
    res = engine.run()
    ledger = res.ledger
    
    # Filter for May 2026 onwards
    ledger['date'] = pd.to_datetime(ledger['date'])
    subset = ledger[ledger['date'] >= '2026-05-01'].copy()
    
    print("\nLedger Data:")
    cols = ['date', 'open', 'high', 'low', 'close', 'volume', 'delivery_qty', 'delivery_pct', 'atr_20', 'cwvap', 'cwc', 'price_slope_z', 'rdv', 'dv_shock', 'z_5', 'z_20', 'z_60']
    # Check if these columns exist, print what exists
    existing_cols = [c for c in cols if c in subset.columns]
    print(subset[existing_cols].to_string())
    
    # Simulate trades
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    
    sym_entry_cfg = get_symbol_entry_config(symbol, entry_cfg)
    sym_exit_cfg = get_symbol_exit_config(symbol, exit_cfg)
    
    trades = simulate_trades(symbol, ledger, sym_entry_cfg, sym_exit_cfg, signal)
    
    print("\nTrades for JINDALSTEL:")
    for t in trades:
        print(f"Entry: {t.entry_date} @ {t.entry_price:.2f} | Exit: {t.exit_date} @ {t.exit_price:.2f} | PnL: {t.pnl_pct}% | Reason: {t.exit_reason}")

if __name__ == "__main__":
    main()
