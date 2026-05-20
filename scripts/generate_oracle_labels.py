
import argparse
import pandas as pd
import numpy as np
from pathlib import Path

def generate_labels(input_path, output_path, rolling_window=30, percentile_threshold=0.75):
    print(f"Loading dataset from {input_path}...")
    df = pd.read_csv(input_path)
    
    if 'pnl_pct' not in df.columns or 'date' not in df.columns or 'symbol' not in df.columns:
        raise ValueError("Dataset must contain 'pnl_pct', 'date', and 'symbol' columns.")
        
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values(['symbol', 'date'])
    
    # Calculate rolling Z-score
    # We use a rolling mean/std of PnL per symbol to define the 'good trade' relative to recent performance
    # Setting as_index=False ensures symbol and date are kept as columns
    rolling_stats = df.groupby('symbol')['pnl_pct'].rolling(window=rolling_window).agg(['mean', 'std']).reset_index()
    
    # After rolling, we need to ensure the index alignment matches.
    # Because we reset_index, the 'level_1' column is actually the original df index.
    df = df.merge(rolling_stats, left_index=True, right_on='level_1', suffixes=('', '_stats'))
    df['z_score'] = (df['pnl_pct'] - df['mean']) / df['std'].replace(0, 1)
    
    # Threshold for labelling
    # Use top percentile of Z-score as the "Oracle" signal
    threshold = df['z_score'].quantile(percentile_threshold)
    df['label'] = (df['z_score'] >= threshold).astype(int)
    
    print(f"Oracle labelling complete. Threshold Z-score: {threshold:.4f}")
    print(f"Class Balance: Good(1)={sum(df['label']==1)}, Bad(0)={sum(df['label']==0)}")
    
    df.drop(columns=['mean', 'std', 'z_score'], inplace=True)
    df.to_csv(output_path, index=False)
    print(f"Saved labelled dataset to {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate regime-aware Oracle labels.")
    parser.add_argument("--input", required=True, help="Input dense dataset CSV")
    parser.add_argument("--output", required=True, help="Output path for labelled dataset")
    parser.add_argument("--window", type=int, default=30, help="Rolling window for Z-score")
    args = parser.parse_args()
    
    generate_labels(args.input, args.output, rolling_window=args.window)
