import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path
from tabulate import tabulate

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend
from scratch.analyze_trends import parse_trends, clean_date_str

def main():
    trends = parse_trends()
    symbols = list(set([t[0] for t in trends]))
    symbols = [s if s != "ASIANPAINTS" else "ASIANPAINT" for s in symbols]
    
    print("Loading stock data...")
    cache = {}
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            cache[sym] = res.ledger.copy()
        except Exception as e:
            pass
            
    # Default SpringBoard parameters
    cap_thresh = -0.50
    cap_lookback = 10
    rp_max = 0.40
    cwc_slope_min = -0.05
    psz_v_min = -0.16
    spearman_min = -0.95
    
    all_trades = []
    
    for sym, df in cache.items():
        # Filter for 2025-01-01 to 2026-06-04
        df_bt = df[(df['date'] >= '2025-01-01') & (df['date'] <= '2026-06-04')].copy().reset_index(drop=True)
        if df_bt.empty:
            continue
            
        records = df_bt.to_dict('records')
        n = len(records)
        in_trade = False
        entry_price = 0.0
        entry_idx = 0
        peak_close = 0.0
        entry_indicators = {}
        
        from scratch.optimize_springboard import check_springboard_custom
        
        for i in range(1, n):
            row = records[i]
            prev = records[i-1]
            close = row.get("close", np.nan)
            if np.isnan(close):
                continue
                
            if in_trade:
                if close > peak_close:
                    peak_close = close
                pnl_pct = (close / entry_price - 1.0) * 100.0
                
                # Exits
                cts = row.get("cts", 0.0)
                prev_cts = prev.get("cts", 0.0)
                exit_triggered = False
                exit_reason = ""
                
                if pnl_pct <= -8.0:
                    exit_triggered = True
                    exit_reason = "Stop Loss"
                elif cts < 0.0 and cts < prev_cts:
                    exit_triggered = True
                    exit_reason = "CTS Cross"
                elif i - entry_idx >= 50:
                    exit_triggered = True
                    exit_reason = "Time Limit"
                    
                if exit_triggered:
                    all_trades.append({
                        "Symbol": sym,
                        "Entry Date": records[entry_idx]['date'].strftime('%Y-%m-%d'),
                        "Exit Date": row['date'].strftime('%Y-%m-%d'),
                        "PnL%": pnl_pct,
                        "Exit Reason": exit_reason,
                        "Entry Inds": entry_indicators
                    })
                    in_trade = False
            else:
                qualifies = check_springboard_custom(
                    row, prev, records, i,
                    cap_thresh, cap_lookback, rp_max, cwc_slope_min, psz_v_min, spearman_min
                )
                if qualifies:
                    if i + 1 < n:
                        next_row = records[i+1]
                        next_close = next_row.get("close", np.nan)
                        if not np.isnan(next_close):
                            in_trade = True
                            entry_price = next_close
                            entry_idx = i + 1
                            peak_close = next_close
                            entry_indicators = {
                                "cts": row.get("cts", 0.0),
                                "cwc": row.get("cwc", 0.0),
                                "cwc_slope": row.get("cwc_slope", 0.0),
                                "fas": row.get("fas", 0.0),
                                "psz_v": row.get("psz_v", 0.0),
                                "pdd_120": row.get("pdd_120", 0.0),
                                "rp_63": row.get("range_pos_63", 0.0),
                            }
                            
    trades_df = pd.DataFrame(all_trades)
    if trades_df.empty:
        print("No trades found.")
        return
        
    winners = trades_df[trades_df['PnL%'] > 0]
    losers = trades_df[trades_df['PnL%'] <= 0]
    
    print(f"Total trades: {len(trades_df)} | Winners: {len(winners)} | Losers: {len(losers)} | Win Rate: {len(winners)/len(trades_df)*100:.1f}%")
    print(f"Avg P&L%: {trades_df['PnL%'].mean():.2f}%")
    print(f"Avg Winner P&L%: {winners['PnL%'].mean():.2f}% | Avg Loser P&L%: {losers['PnL%'].mean():.2f}%")
    
    # Let's see the exit reasons breakdown
    print("\nExit reasons for losing trades:")
    print(losers['Exit Reason'].value_counts())
    
    # Compare entry indicators for winners vs losers
    comparison = []
    for ind in ["cts", "cwc", "cwc_slope", "fas", "psz_v", "pdd_120", "rp_63"]:
        win_vals = [t["Entry Inds"].get(ind, 0.0) for t in winners.to_dict('records')]
        lose_vals = [t["Entry Inds"].get(ind, 0.0) for t in losers.to_dict('records')]
        comparison.append({
            "Indicator": ind,
            "Winner Mean": np.mean(win_vals),
            "Loser Mean": np.mean(lose_vals),
            "Winner P50": np.median(win_vals),
            "Loser P50": np.median(lose_vals),
            "Winner Min": np.min(win_vals),
            "Loser Min": np.min(lose_vals),
            "Winner Max": np.max(win_vals),
            "Loser Max": np.max(lose_vals),
        })
        
    print("\nEntry Indicator Comparison:")
    print(tabulate(comparison, headers="keys", tablefmt="grid"))
    
    # Let's inspect some of the worst losers (PnL% < -5%)
    worst = losers[losers['PnL%'] < -5.0].sort_values('PnL%')
    print("\nWorst 10 Trades:")
    rows_worst = []
    for idx, row in worst.head(10).iterrows():
        rows_worst.append([
            row['Symbol'], row['Entry Date'], row['Exit Date'], f"{row['PnL%']:.2f}%", row['Exit Reason'],
            f"{row['Entry Inds']['cts']:.3f}", f"{row['Entry Inds']['cwc']:.3f}", f"{row['Entry Inds']['fas']:.3f}",
            f"{row['Entry Inds']['pdd_120']:.2f}", f"{row['Entry Inds']['rp_63']:.2f}"
        ])
    print(tabulate(rows_worst, headers=["Symbol", "Entry Date", "Exit Date", "PnL%", "Exit Reason", "CTS", "CWC", "FAS", "PDD_120", "RP_63"], tablefmt="simple"))

if __name__ == "__main__":
    main()
