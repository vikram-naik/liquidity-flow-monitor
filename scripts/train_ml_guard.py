"""
Trains and evaluates Decision Tree, Random Forest, and XGBoost models on the Oracle dataset.
Allows comparing their efficacy in acting as a ML Guard.
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
import joblib
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.ensemble import RandomForestClassifier
import argparse
from datetime import datetime
from xgboost import XGBClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def main():
    parser = argparse.ArgumentParser(description="Train XGBoost ML Guard")
    parser.add_argument("--dataset", help="Path to trade dataset CSV")
    args = parser.parse_args()

    if args.dataset:
        data_path = Path(args.dataset)
    else:
        ml_dir = PROJECT_ROOT / "output" / "ml"
        # Prioritize dense datasets
        datasets = list(ml_dir.glob("dataset_dense_*.csv"))
        if not datasets:
            datasets = list(ml_dir.glob("dataset_trade_*.csv"))
            if not datasets:
                print("No datasets found in output/ml/. Run extract_dense_universal_features.py first.")
                sys.exit(1)
        data_path = sorted(datasets)[-1] # get latest


    if not data_path.exists():
        print(f"Dataset not found at {data_path}.")
        sys.exit(1)

    print(f"Loading dataset from {data_path}...")
    df = pd.read_csv(data_path)
    
    exclude_cols = [
        'symbol', 'date', 'entry_path', 'pnl_pct', 'mfe_pct',
        'open', 'high', 'low', 'close', 'volume', 'delivery_qty', 'delivery_pct',
        'cwvap', 'smoothed_cwvap', 'va_high', 'va_low', 'va_profile_width',
        'dvwap_10', 'dvwap_30', 'dvwap_60', 'dvwap_120',
        'dist_high_10', 'dist_low_10', 'range_width_10', 
        'dist_high_22', 'dist_low_22', 'range_width_22', 
        'dist_high_63', 'dist_low_63', 'range_width_63', 
        'dist_high_252', 'dist_low_252', 'range_width_252',
        'dvl_10', 'dvl_rate_10', 'velocity_10', 'price_distance_10', 'ars_10', 
        'dvl_30', 'dvl_rate_30', 'velocity_30', 'price_distance_30', 'ars_30', 
        'dvl_60', 'dvl_rate_60', 'velocity_60', 'price_distance_60', 'ars_60', 
        'dvl_120', 'dvl_rate_120', 'velocity_120', 'price_distance_120', 'ars_120',
        'cdvl', 'rdv', 'atr_20'
    ]
    feature_cols = [c for c in df.columns if c not in exclude_cols and c != 'label']
    
    df = df.dropna(axis=1, how='all')
    feature_cols = [c for c in feature_cols if c in df.columns]
    
    X = df[feature_cols].fillna(0)
    y = df['label']

    print(f"Dataset Shape: {X.shape}")
    print(f"Class Balance: Good(1)={sum(y==1)}, Bad(0)={sum(y==0)}")
    
    # Stratified split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # In this dataset, Bad Trades (0) are actually the majority.
    # We still want to heavily penalize missing a Bad setup (False Positives),
    # but we don't need a 30:1 ratio anymore since the baseline is ~1.3:1.
    # A 2:1 ratio penalizes false positives slightly more than 'balanced' would, but allows the model to learn.
    weights_dict = {0: 2, 1: 1}
    sample_weights_train = np.array([weights_dict[label] for label in y_train])

    models = {
        "DecisionTree": DecisionTreeClassifier(
            max_depth=5, class_weight=weights_dict, random_state=42, min_samples_leaf=5
        ),
        "RandomForest": RandomForestClassifier(
            n_estimators=100, max_depth=9, class_weight=weights_dict, random_state=42, min_samples_leaf=5
        ),
        "XGBoost": XGBClassifier(
            n_estimators=150, max_depth=7, random_state=42, eval_metric='logloss'
        )
    }

    best_model = None
    best_name = ""

    for name, model in models.items():
        print(f"\n{'='*40}")
        print(f"Training {name}...")
        
        if name == "XGBoost":
            model.fit(X_train, y_train, sample_weight=sample_weights_train)
        else:
            model.fit(X_train, y_train)

        y_test_pred = model.predict(X_test)
        
        print("\n--- Test Set Evaluation ---")
        print("Confusion Matrix:")
        print(confusion_matrix(y_test, y_test_pred))
        print("\nClassification Report:")
        print(classification_report(y_test, y_test_pred, zero_division=0))
        
        if name == "XGBoost":
            # Let's save XGBoost as the default model to disk for now
            best_model = model
            best_name = name

    # --- Save Model ---
    models_dir = PROJECT_ROOT / "src" / "trading" / "signals" / "savgol_cts" / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    model_path = models_dir / f"model_xgb_{date_str}.joblib"
    
    model_data = {
        'model': best_model,
        'feature_cols': feature_cols
    }
    joblib.dump(model_data, model_path)
    print(f"\n{'='*40}")
    print(f"Saved {best_name} model to {model_path}")

if __name__ == "__main__":
    main()
