
import pandas as pd
import numpy as np
import argparse
from pathlib import Path

def analyze_ranking_power(dataset_path):
    print(f"Loading dataset from {dataset_path}...")
    df = pd.read_csv(dataset_path)
    
    if 'label' not in df.columns:
        # Re-derive label if not present, using threshold 2% as fallback
        df['label'] = (df['pnl_pct'] >= 2.0).astype(int)
    
    # Exclude non-feature columns
    exclude = ['symbol', 'date', 'entry_path', 'pnl_pct', 'mfe_pct', 'mae_pct', 'label']
    features = [c for c in df.columns if c not in exclude]
    
    results = []
    
    for f in features:
        if pd.api.types.is_numeric_dtype(df[f]):
            # 1. Pearson Correlation
            corr = df[f].corr(df['pnl_pct'])
            
            # 2. Mean Difference (Power of the feature to separate labels)
            mean_good = df[df['label'] == 1][f].mean()
            mean_bad = df[df['label'] == 0][f].mean()
            diff = mean_good - mean_bad
            
            # 3. Standardized Separation
            std = df[f].std()
            separation = diff / std if std > 0 else 0
            
            results.append({
                'feature': f,
                'correlation': corr,
                'mean_diff': diff,
                'z_separation': separation
            })
            
    res_df = pd.DataFrame(results).sort_values('z_separation', ascending=False)
    print("\n--- Feature Ranking Report ---")
    print(res_df.to_string())
    
    # Save the ranking for later use
    out_path = Path(dataset_path).parent / "feature_ranking.csv"
    res_df.to_csv(out_path, index=False)
    print(f"\nRanking saved to {out_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    args = parser.parse_args()
    
    analyze_ranking_power(args.dataset)
