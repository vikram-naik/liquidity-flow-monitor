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
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from scripts.walk_forward import simulate_trades, get_watchlist_symbols
from scratch.verify_proposed_logic import add_new_features

class SweeperODPSignal:
    """Highly customizable ODP signal for sweeping entry filters and exit paths."""
    def __init__(self, base_signal, params):
        self.base_signal = base_signal
        
        # Entry threshold parameters
        self.das_thresh = params["das_thresh"]
        self.decel_thresh = params["decel_thresh"]
        self.cwc_min = params["cwc_min"]
        self.fas_min = params["fas_min"]
        self.bt_max = params["bt_max"]
        self.dv_shock_min = params["dv_shock_min"]
        self.rdv_min = params["rdv_min"]
        self.filter_regime = params["filter_regime"]
        
        # Exit parameters
        self.exit_mode = params["exit_mode"]
        self.hard_stop_pct = params["hard_stop_pct"]
        self.tp_pct = params["tp_pct"]
        self.be_activation_pct = params["be_activation_pct"]
        
    def check_gap_down(self, records, idx, lookback=10):
        start = max(1, idx - lookback)
        for i in range(start, idx + 1):
            prev_low = records[i-1].get("low", 0)
            curr_high = records[i].get("high", 0)
            atr = records[i].get("atr_20", 0)
            if prev_low > curr_high:
                gap_size = prev_low - curr_high
                if atr > 0 and gap_size > (0.3 * atr):
                    return True
        return False

    def check_entry(self, row, prev_row, cfg, records, idx):
        das = row.get("das", 0.0)
        psz_v = row.get("psz_v", 0.0)
        psz_decel = row.get("psz_decel_3b", 0.0)
        rp_22 = row.get("range_pos_22", 1.0)
        cwc = row.get("cwc", 0.0)
        pdd_30 = row.get("pdd_30", 0.0)
        bt = row.get("base_tightness", 1.0)
        fas = row.get("fas", 0.0)
        regime = row.get("regime", "notrend")
        dv_shock = row.get("dv_shock", 0.0)
        rdv = row.get("rdv", 0.0)
        
        # 1. Base ODP shape criteria
        if (das < self.das_thresh or rp_22 < 0.15) and psz_v > 0.01 and psz_decel > self.decel_thresh:
            
            # 2. Safety and volume climax filters
            if (self.dv_shock_min > 0.0 or self.rdv_min > 0.0) and (dv_shock < self.dv_shock_min or rdv < self.rdv_min):
                return False, 0, {}
                
            # 3. Soft and hard CWC, FAS, BT, Regime Filters
            if self.filter_regime and regime == "downtrend":
                return False, 0, {}
            if fas < self.fas_min:
                return False, 0, {}
            if cwc < self.cwc_min:
                return False, 0, {}
            if bt > self.bt_max:
                return False, 0, {}
                
            # 4. Gap down & structural bottom checks
            has_gap_down = self.check_gap_down(records, idx, 10)
            if not has_gap_down and pdd_30 < -2.50:
                return True, 80, {
                    "reason": "Oversold Decel",
                    "entry_tag": "Oversold-Decel",
                    "score": 80,
                    "conv_score": 80
                }
        return False, 0, {}

    def check_exit(self, row, prev_row, trade, peak_close, bars_held, delivery_bad_count, cwvap_values, exit_cfg, records, idx):
        close = row.get("close", np.nan)
        cwvap = row.get("cwvap", np.nan)
        pnl_pct = (close / trade.entry_price - 1.0) * 100.0
        peak_pnl = (peak_close / trade.entry_price - 1.0) * 100.0
        
        # 1. Hard Stop
        if pnl_pct <= -self.hard_stop_pct:
            return f"Hard Stop (-{self.hard_stop_pct}%)", delivery_bad_count
            
        # 2. Fixed Take Profit
        if self.tp_pct > 0 and pnl_pct >= self.tp_pct:
            return f"Take Profit (+{self.tp_pct}%)", delivery_bad_count
            
        # 3. Breakeven Stop
        if self.be_activation_pct > 0 and peak_pnl >= self.be_activation_pct:
            if pnl_pct <= 0.5:
                return "Breakeven Shield", delivery_bad_count
                
        # 4. Exit Modes
        if self.exit_mode == "mean_reversion":
            if close >= cwvap:
                return "Mean Reversion Target (CWVAP)", delivery_bad_count
            if bars_held >= 60:
                return "Time Stop", delivery_bad_count
            return None, delivery_bad_count
            
        # Fallback to standard savgol exits (e.g. CTS slope check, time stop)
        return self.base_signal.check_exit(row, prev_row, trade, peak_close, bars_held, delivery_bad_count, cwvap_values, exit_cfg, records, idx)

def main():
    watchlist = "NIFTY 50"
    start_date = "2019-01-01"
    
    symbols = get_watchlist_symbols(watchlist)
    print("Loading data and pre-calculating ledgers...")
    
    cached_ledgers = {}
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            df = res.ledger.copy()
            df = add_new_features(df)
            cached_ledgers[sym] = df
        except Exception:
            pass
            
    print(f"Loaded {len(cached_ledgers)} ledgers.")
    
    base_signal = SignalFactory.get_signal("savgol_cts")
    exit_cfg = SavgolCTSExitConfig()
    
    # Defining Sweep Parameter Grid
    # Focused on finding high average PnL (>5.0%) with trades >= 15
    das_thresh_vals = [-1.8, -2.0, -2.2]
    decel_thresh_vals = [0.04, 0.05]
    cwc_min_vals = [0.20, 0.40, 0.45]
    fas_min_vals = [-0.5, -0.3, -0.1]
    bt_max_vals = [0.45, 0.38, 0.35]
    vol_climax_vals = [
        (0.0, 0.0),  # No volume filter
        (0.5, 1.0),  # Mild climax
        (1.0, 1.2),  # Strong climax
    ]
    filter_regime_vals = [False, True]
    
    exit_modes = ["standard", "mean_reversion"]
    hard_stops = [8.0, 10.0]
    tps = [0.0, 12.0, 15.0]  # 0.0 means no fixed TP
    be_triggers = [0.0, 4.0] # 0.0 means no BE shield
    
    # We will sample combinations intelligently to keep the run time reasonable.
    # To do this, let's select a robust set of candidate configurations:
    # We want to search for configurations that restrict entries to high-quality ones.
    
    import itertools
    
    # Let's generate a list of promising configs to try
    promising_grids = []
    
    # We will generate a smart grid of about 200 combinations
    grid = list(itertools.product(
        das_thresh_vals,      # 3
        decel_thresh_vals,    # 2
        cwc_min_vals,         # 3
        fas_min_vals,         # 3
        bt_max_vals,          # 3
        vol_climax_vals,      # 3
        filter_regime_vals,   # 2
        exit_modes,           # 2
        hard_stops,           # 2
        tps,                  # 3
        be_triggers           # 2
    ))
    
    # That is too many (3*2*3*3*3*3*2*2*2*3*2 = 38,880 combinations). 
    # Let's prune the grid by fixing some parameters based on previous sweeps or pairing them logically.
    # 1. We know decel floor should be 0.04 (previous sweeps proved 0.02 is too loose).
    # 2. We know DAS should be -2.0 or -2.2.
    # 3. We know exit mode is either mean_reversion or standard.
    # Let's construct a cleaner, targeted list of test cases:
    
    configs = []
    
    # Let's define specific combinations that represent different logical strategies:
    # Strategy 1: Volume climax + Tight Base + Mean Reversion Exit
    # Strategy 2: High CWC + High FAS + Standard Exit
    # Strategy 3: Tight Base + No Downtrend + Trailing/Fixed TP
    # Strategy 4: High Stretch (DAS < -2.2) + Breakeven Shield
    
    # Let's generate a smart subset of 300 configs:
    for das in [-2.0, -2.2]:
        for decel in [0.04, 0.05]:
            for cwc in [0.20, 0.40, 0.45]:
                for fas in [-0.5, -0.3, -0.1]:
                    for bt in [0.45, 0.38, 0.35]:
                        for vol in [(0.0, 0.0), (0.5, 1.0), (1.0, 1.2)]:
                            for regime in [False, True]:
                                # Pick matching exit profiles
                                if cwc >= 0.4 or fas >= -0.3:
                                    # Try standard exits with 10% hard stop
                                    configs.append({
                                        "das_thresh": das, "decel_thresh": decel, "cwc_min": cwc, "fas_min": fas, "bt_max": bt,
                                        "dv_shock_min": vol[0], "rdv_min": vol[1], "filter_regime": regime,
                                        "exit_mode": "standard", "hard_stop_pct": 10.0, "tp_pct": 0.0, "be_activation_pct": 0.0
                                    })
                                    # Try standard exits with fixed TP
                                    configs.append({
                                        "das_thresh": das, "decel_thresh": decel, "cwc_min": cwc, "fas_min": fas, "bt_max": bt,
                                        "dv_shock_min": vol[0], "rdv_min": vol[1], "filter_regime": regime,
                                        "exit_mode": "standard", "hard_stop_pct": 8.0, "tp_pct": 12.0, "be_activation_pct": 4.0
                                    })
                                if vol[0] >= 0.5:
                                    # Try mean reversion exits
                                    configs.append({
                                        "das_thresh": das, "decel_thresh": decel, "cwc_min": cwc, "fas_min": fas, "bt_max": bt,
                                        "dv_shock_min": vol[0], "rdv_min": vol[1], "filter_regime": regime,
                                        "exit_mode": "mean_reversion", "hard_stop_pct": 10.0, "tp_pct": 0.0, "be_activation_pct": 0.0
                                    })
                                    configs.append({
                                        "das_thresh": das, "decel_thresh": decel, "cwc_min": cwc, "fas_min": fas, "bt_max": bt,
                                        "dv_shock_min": vol[0], "rdv_min": vol[1], "filter_regime": regime,
                                        "exit_mode": "mean_reversion", "hard_stop_pct": 8.0, "tp_pct": 15.0, "be_activation_pct": 4.0
                                    })

    # Deduplicate configs
    unique_configs = []
    seen = set()
    for c in configs:
        k = tuple(sorted(c.items()))
        if k not in seen:
            seen.add(k)
            unique_configs.append(c)
            
    print(f"Generated {len(unique_configs)} unique test configurations.")
    
    # Shuffle or sample if too large, let's limit to 400 for speed
    if len(unique_configs) > 400:
        import random
        random.seed(42)
        unique_configs = random.sample(unique_configs, 400)
        
    results = []
    
    print("\nStarting intelligent search sweep...")
    for idx, params in enumerate(unique_configs):
        if idx > 0 and idx % 20 == 0:
            print(f"Processed {idx} / {len(unique_configs)} configs...")
            
        sweep_signal = SweeperODPSignal(base_signal, params)
        
        trades = []
        for sym, df in cached_ledgers.items():
            try:
                sym_trades = simulate_trades(sym, df, None, exit_cfg, sweep_signal)
                period_trades = [t for t in sym_trades if start_date <= str(t.entry_date)]
                trades.extend(period_trades)
            except Exception:
                pass
                
        total_trades = len(trades)
        if total_trades >= 15:  # Require statistical significance
            pnls = [t.pnl_pct for t in trades]
            avg_pnl = np.mean(pnls)
            win_rate = sum(1 for p in pnls if p > 0) / total_trades * 100
            avg_dur = np.mean([t.duration for t in trades])
            
            gross_win = sum(p for p in pnls if p > 0)
            gross_loss = abs(sum(p for p in pnls if p <= 0))
            pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
            
            results.append({
                "DAS": params["das_thresh"],
                "Decel": params["decel_thresh"],
                "CWC": params["cwc_min"],
                "FAS": params["fas_min"],
                "BT": params["bt_max"],
                "Vol": f"{params['dv_shock_min']}/{params['rdv_min']}",
                "NoDT": "Yes" if params["filter_regime"] else "No",
                "Exit": params["exit_mode"],
                "SL": f"-{params['hard_stop_pct']}%",
                "TP": f"+{params['tp_pct']}%" if params["tp_pct"] > 0 else "None",
                "BE": f"+{params['be_activation_pct']}%" if params["be_activation_pct"] > 0 else "None",
                "Trades": total_trades,
                "Win Rate": f"{win_rate:.1f}%",
                "Avg P&L": f"{avg_pnl:+.2f}%",
                "Profit Factor": f"{pf:.2f}x",
                "Avg Duration": f"{avg_dur:.1f}",
                "_avg_pnl_raw": avg_pnl
            })
            
    # Convert and sort
    df_results = pd.DataFrame(results)
    if not df_results.empty:
        df_results = df_results.sort_values(by="_avg_pnl_raw", ascending=False)
        print("\n--- COMBINED SWEEP TOP RESULTS (Sorted by Avg P&L) ---")
        print(tabulate(df_results.head(40).drop(columns=["_avg_pnl_raw"]), headers="keys", tablefmt="grid", showindex=False))
        
        # Save results to a CSV for detailed inspection
        df_results.to_csv("scratch/combined_sweep_results.csv", index=False)
        print("\nSaved all sweep results to scratch/combined_sweep_results.csv")
    else:
        print("No configurations generated >= 15 trades.")

if __name__ == "__main__":
    main()
