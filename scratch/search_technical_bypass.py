import pandas as pd
import numpy as np

def main():
    filepath = "/home/vn/python-projects/liquidity-flow-monitor/output/added_trades_details.csv"
    df = pd.read_csv(filepath)
    
    # We want to search combinations of thresholds for:
    # - cwc (range 0.1 to 0.95)
    # - pdd_30 (range -6.0 to 1.0)
    # - psz_v (range 0.0 to 0.25)
    # - fas (range -1.0 to 0.5)
    # - base_tightness (range 0.1 to 0.6)
    # - cts (range -1.0 to 0.5)
    
    # Let's define parameter spaces
    cwc_grid = [0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8]
    pdd_grid = [-5.0, -4.5, -4.0, -3.5, -3.0, -2.5, -2.0, -1.5, -1.0, 0.0]
    psz_grid = [0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10]
    fas_grid = [-1.0, -0.8, -0.6, -0.4, -0.2, 0.0, 0.1]
    bt_grid = [0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6]
    
    best_results = []
    
    # We want to find rules of the form:
    # cwc >= C and pdd_30 < P and psz_v >= V [and optional other conditions]
    
    for cwc_val in cwc_grid:
        for pdd_val in pdd_grid:
            for psz_val in psz_grid:
                # Rule: cwc >= cwc_val and pdd_30 < pdd_val and psz_v >= psz_val
                cond = (df["cwc"] >= cwc_val) & (df["pdd_30"] < pdd_val) & (df["psz_v"] >= psz_val)
                subset = df[cond]
                
                if len(subset) >= 4:  # At least 4 trades accepted to be statistically meaningful
                    win_rate = (subset["pnl"] > 0).mean()
                    total_pnl = subset["pnl"].sum()
                    mean_pnl = subset["pnl"].mean()
                    severe_losses = (subset["pnl"] <= -5.0).sum()
                    
                    best_results.append({
                        "rule": f"cwc >= {cwc_val:.2f} AND pdd_30 < {pdd_val:.1f} AND psz_v >= {psz_val:.3f}",
                        "count": len(subset),
                        "win_rate": win_rate,
                        "total_pnl": total_pnl,
                        "mean_pnl": mean_pnl,
                        "severe_losses": severe_losses
                    })
                    
    # Also let's try rules involving cwc, pdd_30, and base_tightness
    for cwc_val in cwc_grid:
        for pdd_val in pdd_grid:
            for bt_val in bt_grid:
                cond = (df["cwc"] >= cwc_val) & (df["pdd_30"] < pdd_val) & (df["base_tightness"] < bt_val)
                subset = df[cond]
                if len(subset) >= 4:
                    win_rate = (subset["pnl"] > 0).mean()
                    total_pnl = subset["pnl"].sum()
                    mean_pnl = subset["pnl"].mean()
                    severe_losses = (subset["pnl"] <= -5.0).sum()
                    
                    best_results.append({
                        "rule": f"cwc >= {cwc_val:.2f} AND pdd_30 < {pdd_val:.1f} AND base_tightness < {bt_val:.2f}",
                        "count": len(subset),
                        "win_rate": win_rate,
                        "total_pnl": total_pnl,
                        "mean_pnl": mean_pnl,
                        "severe_losses": severe_losses
                    })

    # Let's filter: severe_losses == 0, and sort by total_pnl descending
    results_df = pd.DataFrame(best_results)
    safe_results = results_df[results_df["severe_losses"] == 0]
    
    print("Top 20 Safe Rules (Zero Severe Losses):")
    print(safe_results.sort_values(by="total_pnl", ascending=False).head(20).to_string(index=False))

if __name__ == "__main__":
    main()
