
import pandas as pd
import json
from pathlib import Path

def build_scoring_model(ranking_path, dataset_path, top_n=15):
    # Load ranking
    ranking_df = pd.read_csv(ranking_path)

    # We want a RISK score.
    # Features with negative correlation to PnL should have POSITIVE weights in the RISK score.
    # Features with positive correlation to PnL should have NEGATIVE weights (reducing risk).

    # Assign weight = -1 * correlation
    ranking_df['risk_contribution'] = -1 * ranking_df['correlation']

    # Select top N features by absolute risk contribution
    ranking_df['abs_risk'] = ranking_df['risk_contribution'].abs()
    top_features = ranking_df.sort_values('abs_risk', ascending=False).head(top_n)

    # Weights are the raw risk contribution
    weights = top_features.set_index('feature')['risk_contribution']

    # Save weights
    scoring_data = weights.to_dict()
    out_json = Path(ranking_path).parent / "scoring_weights_avoidance.json"
    with open(out_json, "w") as f:
        json.dump(scoring_data, f, indent=4)
    print(f"Avoidance weights saved to {out_json}")

    # Validate on dataset
    df = pd.read_csv(dataset_path)
    scores = pd.Series(0, index=df.index)
    for feat, weight in weights.items():
        if feat in df.columns:
            # Skip boolean columns (triggers)
            if df[feat].dtype == bool or set(df[feat].unique()) <= {0, 1}:
                norm_feat = df[feat].astype(float)
            else:
                # Simple Min-Max normalization
                feat_series = df[feat]
                norm_feat = (feat_series - feat_series.min()) / (feat_series.max() - feat_series.min() + 1e-9)
            scores += weight * norm_feat

    df['risk_score'] = scores
    corr = df['risk_score'].corr(df['pnl_pct'])

    print(f"\nValidation complete.")
    print(f"Correlation of Risk Score with PnL: {corr:.4f}")

    # Show top 5 risk trades
    print("\nTop 5 HIGH RISK trades:")
    print(df.sort_values('risk_score', ascending=False).head(5)[['symbol', 'date', 'pnl_pct', 'risk_score']])


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ranking", default="./output/ml/feature_ranking.csv")
    parser.add_argument("--dataset", default="./output/ml/dataset_oracle_nifty_50_20260516.csv")
    args = parser.parse_args()
    
    build_scoring_model(args.ranking, args.dataset)
