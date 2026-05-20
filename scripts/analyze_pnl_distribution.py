import pandas as pd
import numpy as np

# Load the dataset
df = pd.read_csv("output/ml/dataset_dense_nifty_50_20260517.csv")

# Ensure pnl_pct is numeric and drop any NaNs
pnl = pd.to_numeric(df["pnl_pct"], errors="coerce").dropna()

# Calculate dynamic bins using numpy
# We use 'auto' which leverages Sturges and FD estimators for optimal bin sizes
counts, bins = np.histogram(pnl, bins='auto')

print("--- PnL Distribution ---")
print(f"Total Trades: {len(pnl)}")
print(f"Min PnL: {pnl.min():.2f}%")
print(f"Max PnL: {pnl.max():.2f}%")
print(f"Median PnL: {pnl.median():.2f}%")
print(f"Mean PnL: {pnl.mean():.2f}%")
print(f"Win Rate: {(pnl > 0).mean() * 100:.2f}%\n")

print(f"{'PnL Range (%)':<25} | {'Count':<8} | {'% of Total':<10}")
print("-" * 50)

# Display the distribution
for i in range(len(counts)):
    if counts[i] > 0: # Only print non-empty bins to keep output clean
        range_str = f"[{bins[i]:>6.2f} to {bins[i+1]:>6.2f})"
        pct = (counts[i] / len(pnl)) * 100
        print(f"{range_str:<25} | {counts[i]:<8} | {pct:>5.1f}%")

