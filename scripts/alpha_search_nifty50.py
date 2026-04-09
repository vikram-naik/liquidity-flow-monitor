import sqlite3
import sys
import itertools
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.base import Trade

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"

def get_watchlist_symbols(name: str) -> list[str]:
    db = sqlite3.connect(str(DB_PATH))
    row = db.execute("SELECT id FROM watchlists WHERE name = ?", (name,)).fetchone()
    if not row: return []
    symbols = [r[0] for r in db.execute("SELECT symbol FROM watchlist_items WHERE watchlist_id = ?",(row[0],)).fetchall()]
    db.close()
    return symbols

def get_market_data() -> pd.DataFrame:
    try:
        df = DivergenceEngine("NIFTY 50").run().ledger
        df['date'] = df['date'].astype(str).str[:10]
        return df[['date', 'price_slope_z']]
    except: return pd.DataFrame()

def evaluate_params(symbols_data, mkt_data, params):
    all_trades = []
    
    for sym, df in symbols_data.items():
        records = df.to_dict("records")
        n = len(records)
        in_trade = False
        trade = None
        pending_signal = None

        for i in range(60, n):
            row = records[i]
            dt = str(row['date'])[:10]
            
            # Skip COVID
            if "2020-02-01" <= dt <= "2021-06-30":
                continue

            if in_trade:
                close = row['close']
                atr = row['atr_20']
                
                pnl = (close / trade.entry_price - 1) * 100
                trade.mfe_pct = max(trade.mfe_pct, pnl)
                
                if not hasattr(trade, 'stop'):
                    trade.stop = trade.entry_price - params['stop_atr'] * trade.atr_at_entry
                
                # Wide swing trailing stop
                if pnl > params['trail_start_pnl']: 
                    trade.stop = max(trade.stop, close - params['trail_atr'] * atr)
                
                exit_reason = None
                
                if close < trade.stop: 
                    exit_reason = "TRAILING_STOP" if pnl > 0 else "STOP_LOSS"
                elif (i - trade.entry_idx) >= params['time_fail_bars'] and pnl < params['time_fail_pnl']: 
                    exit_reason = "TIME_FAIL"
                
                if exit_reason:
                    trade.exit_date = dt
                    trade.exit_price = row['open'] # EOD lag exit next open
                    trade.pnl_pct = round((trade.exit_price / trade.entry_price - 1) * 100, 2)
                    trade.duration = i - trade.entry_idx
                    trade.exit_reason = exit_reason
                    all_trades.append(trade)
                    in_trade = False
                    trade = None
                continue

            if pending_signal:
                trade = Trade(symbol=sym, entry_date=dt, entry_price=row['open'], entry_idx=i, atr_at_entry=row['atr_20'], soft_filters_passed=0, entry_tag="SWING")
                trade.signal_date = pending_signal
                trade.mfe_pct = 0
                trade.mae_pct = 0
                in_trade = True
                pending_signal = None
                continue

            # Swing / Position Lookback features
            cts_accel = row.get('cts_accel', 0)
            range_pos_63 = row.get('range_pos_63', 1.0)
            range_pos_252 = row.get('range_pos_252', 0)
            pdd_120 = row.get('pdd_120', 0) # Price distance to 120-bar DVL
            cwc_slope = row.get('cwc_slope', 0)
            coherence = row.get('coherence', 0)
            
            mkt_psz = mkt_data.get(dt, {}).get('price_slope_z', 0)
            
            # Position Swing rules: Deep value in a long-term structural uptrend
            cond_1 = range_pos_63 < params['rp63_max'] # Deep 3-month pullback
            cond_2 = pdd_120 > params['pdd_min'] # Asset is structurally trending up long-term
            cond_3 = cts_accel > params['cts_accel_min'] # Sharp momentum return
            cond_4 = cwc_slope > params['cwc_slope_min'] # Institutional order flow shifting
            cond_5 = mkt_psz > params['mkt_psz_min'] # Market not in a crash
            cond_6 = coherence > params['coherence_min'] # Trend is coherent
            
            if cond_1 and cond_2 and cond_3 and cond_4 and cond_5 and cond_6:
                pending_signal = dt
                
    if not all_trades:
        return 0, 0, 0, 0, 0
        
    df_trades = pd.DataFrame([{"pnl%": t.pnl_pct, "dur": t.duration} for t in all_trades])
    wins = df_trades[df_trades["pnl%"] > 0]
    losses = df_trades[df_trades["pnl%"] <= 0]
    
    wr = len(wins)/len(df_trades)*100 if len(df_trades) > 0 else 0
    avg_win = wins["pnl%"].mean() if len(wins) > 0 else 0
    avg_loss = abs(losses["pnl%"].mean()) if len(losses) > 0 else 0
    payoff = avg_win / avg_loss if avg_loss > 0 else (avg_win if avg_win > 0 else 0)
    avg_dur = df_trades['dur'].mean()
    
    return wr, payoff, len(df_trades), df_trades['pnl%'].mean(), avg_dur

def main():
    print("Loading NIFTY 50...")
    symbols = get_watchlist_symbols("NIFTY 50")
    if not symbols: return
        
    mkt_df = get_market_data()
    mkt_dict = mkt_df.set_index('date').to_dict('index') if not mkt_df.empty else {}
    
    print(f"Loaded {len(symbols)} symbols. Getting ledger data...")
    symbols_data = {}
    for sym in symbols:
        try:
            df = DivergenceEngine(sym).run().ledger
            df['date'] = df['date'].astype(str).str[:10]
            symbols_data[sym] = df
        except: pass

    # Grid for Swing Trading (Duration 15-45 days, Avg PnL > 4-5%)
    param_grid = {
        'rp63_max': [0.2, 0.4, 0.6], # Buy the 3-month dip
        'pdd_min': [0.0, 0.05], # Ensure structural long-term uptrend
        'cts_accel_min': [0.02, 0.05], # Momentum spark
        'cwc_slope_min': [0.0, 0.02], # Institutional backing
        'mkt_psz_min': [-0.5, 0.0],
        'coherence_min': [0.0, 0.5],
        
        # Swing Exit params
        'stop_atr': [2.5, 3.5], # Wide stop to survive volatility
        'trail_start_pnl': [5.0, 10.0], # Give it room to run before trailing
        'trail_atr': [2.0, 3.0], # Loose trail to catch the massive swings
        'time_fail_bars': [20, 30], # Give it a month to work out
        'time_fail_pnl': [0.0]
    }
    
    keys, values = zip(*param_grid.items())
    permutations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    
    results = []
    np.random.seed(42)
    idxs = np.random.choice(len(permutations), min(1000, len(permutations)), replace=False)
    permutations = [permutations[i] for i in idxs]
        
    print(f"Evaluating {len(permutations)} combinations for SWING/POSITION alpha...")
    for i, params in enumerate(permutations):
        if i % 100 == 0:
            print(f"Done {i}...")
        wr, payoff, count, avg_pnl, avg_dur = evaluate_params(symbols_data, mkt_dict, params)
        # We want high average PnL and decent duration
        if count >= 15 and avg_pnl >= 3.0 and avg_dur > 10: 
            results.append({
                'wr': wr, 'payoff': payoff, 'count': count, 'avg_pnl': avg_pnl, 'avg_dur': avg_dur, 'params': params
            })
            
    # Maximize Expectancy (Avg PnL) while keeping WR respectable
    results.sort(key=lambda x: x['avg_pnl'] * (x['wr'] > 50) + (x['payoff'] > 2.0)*10, reverse=True)
    
    print("\n--- TOP POSITION TRADING RESULTS ---")
    for r in results[:10]:
        print(f"WR: {r['wr']:.1f}% | Payoff: {r['payoff']:.2f}x | Trades: {r['count']} | Avg PnL: {r['avg_pnl']:.2f}% | Avg Dur: {r['avg_dur']:.1f} bars")
        print(r['params'])
        print("-" * 50)

if __name__ == "__main__":
    main()
