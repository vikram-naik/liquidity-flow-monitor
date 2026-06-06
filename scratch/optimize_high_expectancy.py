import sys
import os
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag, ExitReason

# Bins and weights from the optimization output
feature_bins = {
    'range_pos_22': [-np.inf, 0.1621347175611818, 0.30754132734516737, np.inf],
    'range_pos_63': [-np.inf, 0.1609814811284787, 0.32191849484000795, np.inf],
    'pdd_30': [-np.inf, -3.7199239219778977, -1.676826520243526, np.inf],
    'range_pos_22_low_count': [-np.inf, 3.0, 5.0, np.inf],
    'cts': [-np.inf, -0.7729008116730189, -0.26902766095853614, np.inf],
    'psz_v': [-np.inf, 0.016087133999999996, 0.06934025800000002, np.inf],
    'cts_accel': [-np.inf, -0.0017448884504695657, 0.02484232957857699, np.inf],
    'price_momentum_change': [-np.inf, -0.002274781941957703, 0.04307968503508724, np.inf],
    'price_decel_3b': [-np.inf, -0.0009566000000000117, 0.1267522, np.inf],
}

feature_weights = {
    'range_pos_22': [
        (-np.inf, 0.162, 0.934461),
        (0.162, 0.308, 0.976389),
        (0.308, np.inf, -0.804577),
    ],
    'range_pos_63': [
        (-np.inf, 0.161, 1.200473),
        (0.161, 0.322, 1.008560),
        (0.322, np.inf, -0.849904),
    ],
    'pdd_30': [
        (-np.inf, -3.72, 1.230515),
        (-3.72, -1.677, 0.978940),
        (-1.677, np.inf, -0.848164),
    ],
    'range_pos_22_low_count': [
        (-np.inf, 3.0, -0.700466),
        (3.0, 5.0, 1.181135),
        (5.0, np.inf, 4.746606),
    ],
    'cts': [
        (-np.inf, -0.773, 1.079407),
        (-0.773, -0.269, 0.674435),
        (-0.269, np.inf, -0.767056),
    ],
    'price_slope_z': [
        (-np.inf, -0.276, 0.708460),
        (-0.276, -0.123, 0.614644),
        (-0.123, np.inf, -0.679039),
    ],
    'psz_v': [
        (-np.inf, 0.0161, -0.582190),
        (0.0161, 0.0693, 0.678209),
        (0.0693, np.inf, 0.348266),
    ],
    'cts_accel': [
        (-np.inf, -0.00174, -0.350465),
        (-0.00174, 0.0248, -0.027507),
        (0.0248, np.inf, 0.588710),
    ],
    'price_momentum_change': [
        (-np.inf, -0.00227, -0.384382),
        (-0.00227, 0.0431, 0.058305),
        (0.0431, np.inf, 0.525397),
    ],
    'price_decel_3b': [
        (-np.inf, -0.000957, -0.399786),
        (-0.000957, 0.127, 0.159492),
        (0.127, np.inf, 0.414627),
    ],
}

def get_watchlist_symbols(name="NIFTY 50"):
    from src.database import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    symbols = [r[0] for r in conn.execute(
        "SELECT symbol FROM watchlist_items WHERE watchlist_id = (SELECT id FROM watchlists WHERE name = ?) ORDER BY display_order",
        (name,)
    ).fetchall()]
    conn.close()
    return symbols

def main():
    symbols = get_watchlist_symbols("NIFTY 50")
    print(f"Loaded {len(symbols)} symbols from NIFTY 50.")
    
    # Load and cache records
    cache_records = {}
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            df = res.ledger.copy()
            
            def get_score(row):
                score = 0.0
                for feat in feature_bins.keys():
                    val = row.get(feat, np.nan)
                    if np.isnan(val):
                        continue
                    for left, right, weight in feature_weights[feat]:
                        if left < val <= right:
                            score += weight
                            break
                return score
                
            df["bayesian_score"] = df.apply(get_score, axis=1)
            cache_records[sym] = df
        except Exception as e:
            print(f"Error loading {sym}: {e}")
            
    print("Data loading completed.")
    
    # Let's sweep parameters:
    # - Threshold: 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5
    # - Downtrend filter: True / False (if True, we reject entry when regime == 'downtrend')
    # - Profit target: 8.0%, 10.0%, 12.0%, 15.0%, None
    # - Stop Loss: 5.0%, 8.0%
    
    thresholds = [4.5, 5.5, 6.5, 7.5]
    downtrend_filters = [True, False]
    profit_targets = [10.0, 15.0, None]
    stop_losses = [5.0, 8.0]
    
    sweep_results = []
    
    for thresh in thresholds:
        for dt_filter in downtrend_filters:
            for target in profit_targets:
                for stop in stop_losses:
                    # Simulate trades
                    all_trades = []
                    for sym, df_sym in cache_records.items():
                        records = df_sym.to_dict('records')
                        n = len(records)
                        in_trade = False
                        entry_idx = -1
                        entry_price = 0.0
                        peak_price = 0.0
                        pending_entry = False
                        
                        for i in range(1, n):
                            row = records[i]
                            prev = records[i - 1]
                            close = row.get("close", np.nan)
                            if np.isnan(close):
                                continue
                                
                            if in_trade:
                                if close > peak_price:
                                    peak_price = close
                                    
                                pnl = (close / entry_price - 1.0) * 100.0
                                
                                # Exit 1: Hard Stop Loss
                                if pnl <= -stop:
                                    all_trades.append({"pnl": -stop, "duration": i - entry_idx, "date": row["date"]})
                                    in_trade = False
                                    continue
                                    
                                # Exit 2: Profit Target
                                if target is not None and pnl >= target:
                                    all_trades.append({"pnl": target, "duration": i - entry_idx, "date": row["date"]})
                                    in_trade = False
                                    continue
                                    
                                # Exit 3: Standard Trailing Stop (using 2.5 * ATR_20)
                                atr = row.get("atr_20", 0.0)
                                if atr > 0:
                                    stop_level = peak_price - 2.5 * atr
                                    if close < stop_level:
                                        realized_pnl = (close / entry_price - 1.0) * 100.0
                                        all_trades.append({"pnl": realized_pnl, "duration": i - entry_idx, "date": row["date"]})
                                        in_trade = False
                                        continue
                                        
                                # Exit 4: CTS exhaustion
                                cts = row.get("cts", 0.0)
                                if cts > 0.80:
                                    realized_pnl = (close / entry_price - 1.0) * 100.0
                                    all_trades.append({"pnl": realized_pnl, "duration": i - entry_idx, "date": row["date"]})
                                    in_trade = False
                                    continue
                                    
                            elif pending_entry:
                                in_trade = True
                                entry_idx = i
                                entry_price = close
                                peak_price = close
                                pending_entry = False
                            else:
                                if row.get("bayesian_score", -999.0) >= thresh:
                                    # Causal filters
                                    if dt_filter and row.get("regime", "") == "downtrend":
                                        continue
                                    pending_entry = True
                                    
                        if in_trade:
                            last = records[-1]
                            realized_pnl = (last["close"] / entry_price - 1.0) * 100.0
                            all_trades.append({"pnl": realized_pnl, "duration": n - 1 - entry_idx, "date": last["date"]})
                            
                    # Evaluate
                    train_trades = [t for t in all_trades if str(t["date"]) < "2025-01-01" and str(t["date"]) >= "2019-03-01"]
                    test_trades = [t for t in all_trades if str(t["date"]) >= "2025-01-01" and str(t["date"]) <= "2026-04-30"]
                    
                    def get_stats(trades):
                        if not trades:
                            return 0, np.nan, np.nan, np.nan
                        pnls = [t["pnl"] for t in trades]
                        avg_pnl = np.mean(pnls)
                        win_rate = sum(1 for p in pnls if p > 0) / len(pnls) * 100.0
                        gross_win = sum(p for p in pnls if p > 0)
                        gross_loss = abs(sum(p for p in pnls if p <= 0))
                        pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
                        return len(trades), avg_pnl, win_rate, pf
                        
                    tr_cnt, tr_pnl, tr_win, tr_pf = get_stats(train_trades)
                    te_cnt, te_pnl, te_win, te_pf = get_stats(test_trades)
                    
                    sweep_results.append({
                        "Thresh": thresh,
                        "NoDowntrend": dt_filter,
                        "Target%": target if target is not None else "None",
                        "Stop%": stop,
                        "Train Trades": tr_cnt,
                        "Train PnL%": tr_pnl,
                        "Train Win%": tr_win,
                        "Train PF": tr_pf,
                        "Test Trades": te_cnt,
                        "Test PnL%": te_pnl,
                        "Test Win%": te_win,
                        "Test PF": te_pf,
                    })
                    
    df_results = pd.DataFrame(sweep_results)
    
    # Sort configurations that maximize Train PnL% and Test PnL%
    print("\nTop configurations sorted by Test PnL%:")
    print(tabulate(df_results.sort_values("Test PnL%", ascending=False).head(15), headers="keys", tablefmt="grid", floatfmt=".3f"))
    
    print("\nConfigurations sorted by Train PnL% (maximizing Train):")
    print(tabulate(df_results.sort_values("Train PnL%", ascending=False).head(15), headers="keys", tablefmt="grid", floatfmt=".3f"))
    
    # Filter for configs where Train PnL >= 4.0% and Test PnL >= 4.0%
    high_exp = df_results[(df_results["Train PnL%"] >= 3.0) & (df_results["Test PnL%"] >= 3.0)]
    print("\nConfigurations with PnL >= 3% in both Train and Test:")
    print(tabulate(high_exp, headers="keys", tablefmt="grid", floatfmt=".3f"))

if __name__ == "__main__":
    main()
