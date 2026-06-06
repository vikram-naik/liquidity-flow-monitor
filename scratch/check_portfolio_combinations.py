import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from tabulate import tabulate

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade, SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.base import BaseEntryConfig, BaseExitConfig
from src.trading.signals.enums import ExitReason, EntryTag
from src.trading.charges import ZerodhaDeliveryCharges
from scratch.analyze_trends import parse_trends, clean_date_str
from scripts.portfolio_backtest import PortfolioConfig, run_portfolio_simulation, compute_portfolio_stats, simulate_trades

def main():
    trends = parse_trends()
    
    # Load and cache engines for Nifty 50 stocks
    import sqlite3
    from src.database import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    symbols = [r[0] for r in conn.execute(
        "SELECT symbol FROM watchlist_items WHERE watchlist_id = (SELECT id FROM watchlists WHERE name = 'NIFTY 50') ORDER BY display_order"
    ).fetchall()]
    conn.close()
    
    print("Loading and caching Nifty 50 data...")
    cache = {}
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            cache[sym] = res.ledger.copy()
        except Exception as e:
            pass
            
    print(f"Cached {len(cache)} stocks.")
    
    grid = [
        # (cap_thresh, prt_exit, hs_enabled, hs_pct, max_hold)
        (-0.70, False, False, 8.0, 30),
        (-0.70, False, False, 8.0, 40),
        (-0.70, False, False, 8.0, 50),
        (-0.75, False, False, 8.0, 30),
        (-0.75, False, False, 8.0, 40),
        (-0.75, False, False, 8.0, 50),
        (-0.80, False, False, 8.0, 30),
        (-0.80, False, False, 8.0, 40),
        (-0.80, False, False, 8.0, 50),
    ]
    
    results = []
    
    for cap_thresh, prt_exit, hs_enabled, hs_pct, max_hold in grid:
        # 1. Update config
        entry_cfg = SavgolCTSEntryConfig()
        entry_cfg.springboard.capitulation_threshold = cap_thresh
        entry_cfg.springboard.enabled = True
        
        exit_cfg = SavgolCTSExitConfig()
        exit_cfg.springboard.enabled = True
        exit_cfg.springboard.prt_st_cross_enabled = prt_exit
        exit_cfg.springboard.hard_stop_enabled = hs_enabled
        exit_cfg.springboard.hard_stop_pct = hs_pct
        exit_cfg.springboard.max_hold_bars = max_hold
        
        # 2. Generate trades
        all_trades = []
        signal = SignalFactory.get_signal("savgol_cts")
        for sym, df in cache.items():
            trades = simulate_trades(sym, df, entry_cfg, exit_cfg, signal)
            period_trades = [t for t in trades if str(t.entry_date) >= "2025-01-01"]
            all_trades.extend(period_trades)
            
        # 3. Check Trend Match Rate
        matched = 0
        for symbol, raw_date in trends:
            sym_clean = "ASIANPAINT" if symbol == "ASIANPAINTS" else symbol
            if sym_clean not in cache:
                continue
            target_date = clean_date_str(raw_date)
            found = False
            for t in all_trades:
                if t.symbol == sym_clean:
                    e_date = pd.to_datetime(t.entry_date)
                    delta = (e_date - target_date).days
                    if -5 <= delta <= 22:
                        found = True
                        break
            if found:
                matched += 1
                
        match_rate = matched / len(trends) * 100.0
        
        # 4. Run portfolio simulation (Equal-Weight)
        p_cfg = PortfolioConfig(start_date="2025-01-01")
        ew_records, ew_curve, ew_skip = run_portfolio_simulation(all_trades, p_cfg, sizing_method="equal_weight")
        ew_stats = compute_portfolio_stats(ew_records, ew_curve, p_cfg)
        
        # 5. Run portfolio simulation (Kelly)
        k_records, k_curve, k_skip = run_portfolio_simulation(all_trades, p_cfg, sizing_method="kelly")
        k_stats = compute_portfolio_stats(k_records, k_curve, p_cfg)
        
        results.append({
            "Cap Thresh": cap_thresh,
            "PRT Exit": prt_exit,
            "HS Enable": hs_enabled,
            "HS Pct": hs_pct,
            "Max Hold": max_hold,
            "Match Rate": f"{matched}/{len(trends)} ({match_rate:.1f}%)",
            "Total Trades": len(all_trades),
            "EW Return": f"{ew_stats.get('total_return', 0.0):+.2f}%",
            "EW Drawdown": f"{ew_stats.get('max_drawdown', 0.0):.2f}%",
            "EW Sharpe": f"{ew_stats.get('sharpe', 0.0):.2f}",
            "Kelly Return": f"{k_stats.get('total_return', 0.0):+.2f}%",
            "Kelly Drawdown": f"{k_stats.get('max_drawdown', 0.0):.2f}%",
        })
        
    print("\n" + "="*100)
    print("PORTFOLIO CONFIGURATION ZOOM SWEEP")
    print("="*100)
    print(tabulate(results, headers="keys", tablefmt="grid"))

if __name__ == "__main__":
    main()
