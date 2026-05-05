import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag

def main():
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    symbols = get_watchlist_symbols("NIFTY 50")
    
    print("Running Train Period...")
    train_trades = run_period(symbols, "2019-01-01", "2023-12-31", entry_cfg, exit_cfg, "TRAIN", signal)
    print("Running Test Period...")
    test_trades = run_period(symbols, "2024-01-01", today_str(), entry_cfg, exit_cfg, "TEST", signal)
    
    all_trades = train_trades + test_trades
    cts_trades = [t for t in all_trades if t.entry_tag == EntryTag.CTS_FLOOR_REVERSION.value]
    
    print(f"Found {len(cts_trades)} CTS Floor Reversion trades.")
    
    data = []
    
    for t in cts_trades:
        engine = DivergenceEngine(t.symbol)
        res = engine.run()
        ledger = res.ledger
        
        # Match entry date
        ledger['date_str'] = ledger['date'].astype(str).str[:10]
        matches = ledger[ledger['date_str'] == t.entry_date]
        if matches.empty:
            continue
            
        entry_idx = matches.index[0]
        sig_idx = entry_idx - 1 # The signal is generated on the day prior to entry
        
        if sig_idx < 0:
            continue
            
        row = ledger.iloc[sig_idx]
        prev = ledger.iloc[sig_idx - 1]
        
        data.append({
            'symbol': t.symbol,
            'date': str(row['date'])[:10],
            'pnl': t.pnl_pct,
            'score': t.conviction_score,
            'win': 1 if t.pnl_pct > 0 else 0,
            'psz_v': row.get('psz_v', np.nan),
            'prev_psz_v': prev.get('psz_v', np.nan),
            'prt': row.get('prt', np.nan),
            'prt_slope': row.get('prt_slope', np.nan),
            'prt_accel': row.get('prt_accel', np.nan),
            'cts_accel': row.get('cts_accel', np.nan),
            'cts_accel_thr': row.get('cts_accel_threshold', np.nan),
            'dist_high_10': row.get('dist_high_10', np.nan),
            'cwc_slope': row.get('cwc_slope', np.nan),
            'psz': row.get('price_slope_z', np.nan),
            'rp10': row.get('range_pos_10', np.nan),
            'rp22': row.get('range_pos_22', np.nan),
            'rp63': row.get('range_pos_63', np.nan),
            'rp252': row.get('range_pos_252', np.nan),
            'fas': row.get('fas', np.nan)
        })
        
    df = pd.DataFrame(data)
    
    bad_symbols = ['COALINDIA', 'WIPRO', 'TATACONSUM', 'TECHM']
    bad = df[df['symbol'].isin(bad_symbols) & (df['win'] == 0)]
    
    def check_guard(name, condition):
        caught_winners = df[condition & (df['win'] == 1)]
        caught_losers = df[condition & (df['win'] == 0)]
        caught_bad = bad[condition[bad.index]]
        print(f"Guard [{name}]: Caught {len(caught_winners)} winners, {len(caught_losers)} losers, {len(caught_bad)} specific bad trades")
    
    check_guard("Fake Floor (Shallow + No Exhaustion)", (df['dist_high_10'] > -5.0) & (df['psz'] > -0.15))
    check_guard("Deep PRT (prt < -0.70)", df['prt'] < -0.70)
    check_guard("Deep PRT (prt < -0.75)", df['prt'] < -0.75)
    
    # Let's test the combo of Fake Floor and strict negative momentum
    fake_floor = (df['dist_high_10'] > -5.0) & (df['psz'] > -0.15)
    strict_mom = df['psz_v'] <= 0
    combo = fake_floor | strict_mom
    check_guard("Fake Floor OR Strict Momentum", combo)

if __name__ == "__main__":
    main()
