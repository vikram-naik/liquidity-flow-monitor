import pandas as pd
from tabulate import tabulate

def main():
    df = pd.read_csv("scratch/missed_trades_20251201.csv")
    print(f"Total Missed Trade Setups: {len(df)}")
    
    # Sort missed trades by P&L descending
    df_sorted = df.sort_values(by="pnl_pct", ascending=False)
    
    print("\n--- ALL MISSED TRADE SETUPS (Sorted by P&L%) ---")
    print(tabulate(df_sorted, headers='keys', tablefmt='psql', showindex=False))
    
    print("\n--- INDIVIDUAL MISSED TRADES WITH PnL > 5% ---")
    high_pnl_trades = df[df['pnl_pct'] > 5.0].sort_values(by="pnl_pct", ascending=False)
    if high_pnl_trades.empty:
        print("None")
    else:
        print(tabulate(high_pnl_trades, headers='keys', tablefmt='psql', showindex=False))
        print(f"\nAverage PnL of these high-performing trades: {high_pnl_trades['pnl_pct'].mean():.2f}%")
        print(f"Total count: {len(high_pnl_trades)}")
        
    print("\n--- GROUPED BY ENTRY TAG (Setup Type) ---")
    tag_grouped = df.groupby("entry_tag").agg(
        count=("pnl_pct", "size"),
        avg_pnl=("pnl_pct", "mean"),
        win_rate=("pnl_pct", lambda x: (x > 0).mean() * 100),
        avg_duration=("duration", "mean")
    ).reset_index().sort_values(by="avg_pnl", ascending=False)
    print(tabulate(tag_grouped, headers='keys', tablefmt='psql', showindex=False))
    
    print("\n--- ENTRY TAGS WITH AVG PnL > 5% ---")
    high_pnl_tags = tag_grouped[tag_grouped['avg_pnl'] > 5.0]
    if high_pnl_tags.empty:
        print("None")
    else:
        print(tabulate(high_pnl_tags, headers='keys', tablefmt='psql', showindex=False))
        
    print("\n--- GROUPED BY SYMBOL ---")
    sym_grouped = df.groupby("symbol").agg(
        count=("pnl_pct", "size"),
        avg_pnl=("pnl_pct", "mean"),
        win_rate=("pnl_pct", lambda x: (x > 0).mean() * 100),
        avg_duration=("duration", "mean")
    ).reset_index().sort_values(by="avg_pnl", ascending=False)
    print(tabulate(sym_grouped, headers='keys', tablefmt='psql', showindex=False))
    
    print("\n--- SYMBOLS WITH AVG PnL > 5% ---")
    high_pnl_syms = sym_grouped[sym_grouped['avg_pnl'] > 5.0]
    if high_pnl_syms.empty:
        print("None")
    else:
        print(tabulate(high_pnl_syms, headers='keys', tablefmt='psql', showindex=False))

if __name__ == "__main__":
    main()
