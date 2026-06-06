import pandas as pd

def main():
    df = pd.read_csv("scratch/modified_trades_20251201.csv")
    total = len(df)
    winners = df[df['pnl_pct'] > 0]
    win_rate = len(winners) / total * 100
    avg_pnl = df['pnl_pct'].mean()
    median_pnl = df['pnl_pct'].median()
    avg_duration = df['duration'].mean()
    
    gross_profit = df[df['pnl_pct'] > 0]['pnl_pct'].sum()
    gross_loss = abs(df[df['pnl_pct'] <= 0]['pnl_pct'].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    print(f"Total Trades:      {total}")
    print(f"Win Rate:          {win_rate:.2f}%")
    print(f"Avg PnL:           {avg_pnl:+.2f}%")
    print(f"Median PnL:        {median_pnl:+.2f}%")
    print(f"Profit Factor:     {profit_factor:.2f}x")
    print(f"Avg Duration:      {avg_duration:.1f} bars")
    
    # Group by entry tag
    print("\n--- PERFORMANCE BY ENTRY TAG ---")
    tag_grouped = df.groupby("entry_tag").agg(
        count=("pnl_pct", "size"),
        avg_pnl=("pnl_pct", "mean"),
        win_rate=("pnl_pct", lambda x: (x > 0).mean() * 100),
        avg_duration=("duration", "mean")
    ).reset_index().sort_values(by="avg_pnl", ascending=False)
    print(tag_grouped.to_string(index=False))

if __name__ == "__main__":
    main()
