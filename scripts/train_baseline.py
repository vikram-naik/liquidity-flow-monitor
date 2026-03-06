"""
train_baseline.py — Step 4 of the divergence engine pipeline.

Trains an XGBoost binary classifier on labeled_panel.parquet with a hard
chronological split, applies a quality gate before saving, and writes a
feature importance CSV.

Usage:
    venv/bin/python3 scripts/train_baseline.py --verbose
"""

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import classification_report, log_loss


EXCLUDE_COLS = {"symbol", "date", "close", "atr_20", "label", "barrier_direction"}


def parse_args():
    p = argparse.ArgumentParser(description="Train XGBoost divergence classifier")
    p.add_argument("--labeled-panel", default="data/labeled_panel.parquet")
    p.add_argument("--model-out", default="models/xgb_divergence_v1.ubj")
    p.add_argument("--logloss-threshold", type=float, default=0.55)
    p.add_argument("--val-start", default="2024-01-01")
    p.add_argument("--holdout-days", type=int, default=60)
    p.add_argument("--verbose", action="store_true")
    p.add_argument(
        "--exclude-features", default="",
        help="Comma-separated extra feature columns to exclude from training",
    )
    return p.parse_args()


def load_data(path: str, verbose: bool):
    df = pd.read_parquet(path)
    df = df.sort_values("date").reset_index(drop=True)
    if verbose:
        print(f"Loaded {len(df):,} rows, {df['symbol'].nunique()} symbols")
        print(f"Date range: {df['date'].min()} → {df['date'].max()}")
        print(f"Label distribution:\n{df['label'].value_counts()}")
    return df


def split_data(df: pd.DataFrame, val_start: str, holdout_days: int, verbose: bool):
    all_dates = df["date"].sort_values().unique()
    holdout_cutoff = all_dates[-holdout_days]
    val_start_dt = pd.Timestamp(val_start)

    mask_train = df["date"] < val_start_dt
    mask_val = (df["date"] >= val_start_dt) & (df["date"] < holdout_cutoff)
    mask_holdout = df["date"] >= holdout_cutoff

    train = df[mask_train]
    val = df[mask_val]
    holdout = df[mask_holdout]

    if verbose:
        print(f"\nSplit summary:")
        print(f"  TRAIN:   {len(train):,} rows  ({df['date'][mask_train].min()} – {df['date'][mask_train].max()})")
        print(f"  VAL:     {len(val):,} rows  ({df['date'][mask_val].min()} – {df['date'][mask_val].max()})")
        print(f"  HOLDOUT: {len(holdout):,} rows  ({df['date'][mask_holdout].min()} – {df['date'][mask_holdout].max()})")

    holdout_dates = sorted(holdout["date"].astype(str).unique().tolist())
    return train, val, holdout, holdout_dates


LABEL_SCHEMES = {
    "direction": {"STRONG_UP": 1, "STRONG_DOWN": 0},
    "followthrough": {"FOLLOW_THROUGH": 1, "TIMEOUT": 0},
}


def detect_label_scheme(series: pd.Series) -> tuple[str, dict]:
    """Auto-detect label scheme from unique values in the label column."""
    unique = set(series.dropna().unique())
    for name, mapping in LABEL_SCHEMES.items():
        if unique <= set(mapping.keys()):
            return name, mapping
    raise ValueError(f"Unknown label values: {unique}. Expected one of {list(LABEL_SCHEMES.keys())}")


def encode_labels(series: pd.Series, mapping: dict) -> pd.Series:
    encoded = series.map(mapping)
    unknown = series[encoded.isna()].unique()
    if len(unknown):
        raise ValueError(f"Unknown label values: {unknown}")
    return encoded.astype(int)


def get_feature_cols(df: pd.DataFrame, exclude: set) -> list[str]:
    return [c for c in df.columns if c not in exclude and pd.api.types.is_numeric_dtype(df[c])]


def main():
    args = parse_args()
    verbose = args.verbose

    # ── Load ──────────────────────────────────────────────────────────────────
    df = load_data(args.labeled_panel, verbose)

    # Auto-detect label scheme
    scheme_name, label_mapping = detect_label_scheme(df["label"])
    class_names = sorted(label_mapping, key=label_mapping.get)  # [neg_label, pos_label]
    if verbose:
        print(f"Label scheme: {scheme_name}  ({class_names[0]}=0, {class_names[1]}=1)")

    # Build exclude set
    exclude = set(EXCLUDE_COLS)
    if args.exclude_features:
        extra = {f.strip() for f in args.exclude_features.split(",") if f.strip()}
        exclude.update(extra)
        if verbose:
            print(f"Extra excluded features: {sorted(extra)}")

    feature_cols = get_feature_cols(df, exclude)
    if verbose:
        print(f"\nFeature count: {len(feature_cols)}")

    # ── Split ─────────────────────────────────────────────────────────────────
    train, val, holdout, holdout_dates = split_data(df, args.val_start, args.holdout_days, verbose)

    if len(train) == 0 or len(val) == 0:
        print("ERROR: train or val set is empty — rebuild panel with more data.")
        sys.exit(1)

    X_train = train[feature_cols]
    y_train = encode_labels(train["label"], label_mapping)
    X_val = val[feature_cols]
    y_val = encode_labels(val["label"], label_mapping)

    # ── Class imbalance ───────────────────────────────────────────────────────
    n_down = (y_train == 0).sum()
    n_up = (y_train == 1).sum()
    scale_pos_weight = n_down / n_up if n_up > 0 else 1.0
    if verbose:
        print(f"\nTrain class counts — {class_names[0]}: {n_down}, {class_names[1]}: {n_up}")
        print(f"scale_pos_weight: {scale_pos_weight:.4f}")

    # ── Train ─────────────────────────────────────────────────────────────────
    model = xgb.XGBClassifier(
        objective="binary:logistic",
        n_estimators=500,
        learning_rate=0.05,
        max_depth=6,
        scale_pos_weight=scale_pos_weight,
        early_stopping_rounds=30,
        eval_metric="logloss",
        random_state=42,
        verbosity=0,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=50 if verbose else False,
    )

    # ── Validate ──────────────────────────────────────────────────────────────
    y_val_proba = model.predict_proba(X_val)
    y_val_pred = model.predict(X_val)
    val_logloss = log_loss(y_val, y_val_proba)

    print(f"\n── Validation metrics ──────────────────────────────────────────")
    print(f"  Log-loss:  {val_logloss:.4f}  (threshold < {args.logloss_threshold})")
    print(f"\n{classification_report(y_val, y_val_pred, target_names=class_names)}")

    # ── Quality gate + save ───────────────────────────────────────────────────
    if val_logloss < args.logloss_threshold:
        os.makedirs(os.path.dirname(args.model_out), exist_ok=True)
        model.save_model(args.model_out)
        print(f"Model saved → {args.model_out}")

        importance_df = (
            pd.DataFrame({"feature": feature_cols, "importance": model.feature_importances_})
            .sort_values("importance", ascending=False)
            .reset_index(drop=True)
        )
        importance_df.to_csv("data/feature_importance.csv", index=False)
        print(f"Feature importance → data/feature_importance.csv")

        if verbose:
            print(f"\nTop 15 features:\n{importance_df.head(15).to_string(index=False)}")

        # Save holdout dates
        with open("data/holdout_dates.json", "w") as f:
            json.dump({"holdout_dates": holdout_dates, "n_rows": len(holdout)}, f, indent=2)
        print(f"Holdout dates → data/holdout_dates.json  ({len(holdout_dates)} trading days)")
    else:
        print("QUALITY GATE FAILED — model NOT saved. Retune features or barriers.")
        sys.exit(1)


if __name__ == "__main__":
    main()
