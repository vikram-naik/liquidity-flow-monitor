import sqlite3
import sys
import numpy as np
import pandas as pd
from pathlib import Path

# Add root folder to path
sys.path.append(str(Path(__file__).parent.parent.resolve()))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag

DUDS_SET = {
    ("LT", "2022-02-08"),
    ("EICHERMOT", "2021-03-15"),
    ("KOTAKBANK", "2025-07-04"),
    ("HDFCBANK", "2023-07-07"),
    ("JSWSTEEL", "2023-01-25"),
    ("BAJFINANCE", "2022-10-24"),
    ("BEL", "2019-11-14"),
    ("GRASIM", "2022-12-23"),
    ("BHARTIARTL", "2026-02-13"),
    ("WIPRO", "2019-06-27"),
    ("ASIANPAINT", "2022-12-26"),
    ("TATACONSUM", "2022-12-27"),
    ("SBIN", "2026-03-11"),
    ("ULTRACEMCO", "2019-07-01"),
    ("JIOFIN", "2025-11-21"),
    ("KOTAKBANK", "2026-02-19"),
    ("BAJAJFINSV", "2022-12-06"),
    ("TCS", "2024-12-26"),
    ("HDFCLIFE", "2024-11-08"),
    ("CIPLA", "2025-11-14"),
    ("HINDUNILVR", "2024-10-11"),
    ("HINDALCO", "2024-10-28"),
    ("COALINDIA", "2024-10-17"),
    ("SBIN", "2019-07-26"),
    ("CIPLA", "2022-11-25"),
    ("GRASIM", "2019-07-22"),
    ("TECHM", "2025-01-30"),
    ("BAJAJ-AUTO", "2020-02-17"),
    ("MARUTI", "2020-01-27"),
    ("TATASTEEL", "2020-02-12"),
    ("AXISBANK", "2020-02-25"),
}

def load_enriched_dataset():
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    
    symbols = get_watchlist_symbols("NIFTY 50")
    test_end = today_str()
    
    print("Simulating trades...", flush=True)
    trades = []
    trades.extend(run_period(symbols, "2019-01-01", "2023-12-31", entry_cfg, exit_cfg, "TRAIN", signal))
    trades.extend(run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, "TEST", signal))
    
    anchor_trades = [t for t in trades if t.entry_tag == EntryTag.ANCHOR_SHOCK_PULLBACK.value]
    
    enriched_trades = []
    by_sym = {}
    for t in anchor_trades:
        by_sym.setdefault(t.symbol, []).append(t)
        
    for sym, sym_trades in by_sym.items():
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            ledger = res.ledger
            if ledger is None or ledger.empty:
                continue
            ledger['date_str'] = ledger['date'].astype(str).str[:10]
            
            for t in sym_trades:
                matches = ledger[ledger['date_str'] == t.entry_date]
                if matches.empty:
                    continue
                entry_idx = matches.index[0]
                sig_idx = entry_idx - 1
                if sig_idx < 0:
                    continue
                    
                sig_row = ledger.iloc[sig_idx]
                t_info = {
                    "symbol": t.symbol,
                    "signal_date": str(sig_row["date"])[:10],
                    "pnl": t.pnl_pct,
                    "cwc": sig_row.get("cwc", 0.0),
                    "psz": sig_row.get("price_slope_z", 0.0),
                    "cdvl": sig_row.get("cdvl", 0.0),
                    "rp_10": sig_row.get("range_pos_10", 0.0),
                    "rp_22": sig_row.get("range_pos_22", 0.0),
                    "rp_63": sig_row.get("range_pos_63", 0.0),
                    "rp_252": sig_row.get("range_pos_252", 0.0),
                    "esr": sig_row.get("esr", 0.0),
                    "dv_shock": sig_row.get("dv_shock", 0.0),
                    "range_width_252": sig_row.get("range_width_252", 0.0),
                    "range_width_63": sig_row.get("range_width_63", 0.0),
                    "range_width_22": sig_row.get("range_width_22", 0.0),
                }
                t_info["is_dud"] = (t.symbol, t_info["signal_date"]) in DUDS_SET
                enriched_trades.append(t_info)
        except Exception as e:
            pass
            
    df = pd.DataFrame(enriched_trades)
    print(f"Successfully enriched {len(df)} Anchor trades.")
    return df

def main():
    df = load_enriched_dataset()
    if df.empty:
        print("No trades to analyze.")
        return
        
    duds = df[df["is_dud"]]
    non_duds = df[~df["is_dud"]]
    total_duds = len(duds)
    
    print(f"Simulated Duds: {total_duds}")
    print(f"Simulated Non-Duds: {len(non_duds)}")
    
    # We will sweep parameters
    results = []
    
    # Define sweep arrays
    cwc_min_opts = [0.0, 0.15, 0.25]
    psz_min_opts = [-0.35, -0.25, -0.15]
    cdvl_min_opts = [-0.50, -0.15, -0.05]
    rw_252_min_opts = [0.0, 20.0, 25.0, 30.0, 35.0]
    rp_252_max_opts = [0.80, 0.75, 0.70, 0.65]
    
    print("\nStarting Advanced Sweep...", flush=True)
    
    for cwc_min in cwc_min_opts:
        for psz_min in psz_min_opts:
            for cdvl_min in cdvl_min_opts:
                for rw_252_min in rw_252_min_opts:
                    for rp_252_max in rp_252_max_opts:
                        # Apply filters
                        passed = df[
                            (df["cwc"] >= cwc_min) &
                            (df["psz"] >= psz_min) &
                            (df["cdvl"] >= cdvl_min) &
                            (df["range_width_252"] >= rw_252_min) &
                            (df["rp_252"] <= rp_252_max)
                        ]
                        
                        if len(passed) == 0:
                            continue
                            
                        # Calculate dud avoidance
                        passed_duds = passed[passed["is_dud"]]
                        num_bypassed_duds = total_duds - len(passed_duds)
                        pct_bypassed_duds = num_bypassed_duds / total_duds * 100 if total_duds > 0 else 100
                        
                        # Calculate win retention
                        retained_wins = passed[~passed["is_dud"] & (passed["pnl"] > 0)]
                        total_wins = len(non_duds[non_duds["pnl"] > 0])
                        pct_retained_wins = len(retained_wins) / total_wins * 100 if total_wins > 0 else 100
                        
                        # Portfolio metrics
                        avg_pnl = passed["pnl"].mean()
                        win_rate = (passed["pnl"] > 0).mean() * 100
                        
                        gross_profits = passed[passed["pnl"] > 0]["pnl"].sum()
                        gross_losses = abs(passed[passed["pnl"] <= 0]["pnl"].sum())
                        profit_factor = gross_profits / gross_losses if gross_losses > 0 else float('inf')
                        
                        results.append({
                            "cwc_min": cwc_min,
                            "psz_min": psz_min,
                            "cdvl_min": cdvl_min,
                            "rw_252_min": rw_252_min,
                            "rp_252_max": rp_252_max,
                            "total_trades": len(passed),
                            "bypassed_duds": num_bypassed_duds,
                            "pct_bypassed_duds": pct_bypassed_duds,
                            "pct_retained_wins": pct_retained_wins,
                            "avg_pnl": avg_pnl,
                            "win_rate": win_rate,
                            "profit_factor": profit_factor,
                            "score": pct_bypassed_duds + pct_retained_wins
                        })
                        
    res_df = pd.DataFrame(results)
    print(f"Swept {len(res_df)} combinations.")
    
    # Sort by joint score or portfolio performance
    print("\n--- Top 25 Parameter Combinations by Combined Score (Dud Bypass + Win Retention) ---")
    top_combos = res_df.sort_values(by=["score", "avg_pnl"], ascending=[False, False])
    print(top_combos.head(25).to_string(index=False))
    
    print("\n--- Top 25 Parameter Combinations by Portfolio Avg PnL ---")
    top_pnl = res_df.sort_values(by=["avg_pnl", "score"], ascending=[False, False])
    print(top_pnl.head(25).to_string(index=False))
    
    # Let's find the absolute best trade-off combo where we bypass a lot of duds and keep the portfolio metrics premium
    print("\n--- High Performing Balanced Selections (Dud Bypass >= 50% & Win Retention >= 75%) ---")
    balanced = res_df[
        (res_df["pct_bypassed_duds"] >= 50.0) & 
        (res_df["pct_retained_wins"] >= 75.0)
    ].sort_values(by="avg_pnl", ascending=False)
    print(balanced.head(20).to_string(index=False))

if __name__ == "__main__":
    main()
