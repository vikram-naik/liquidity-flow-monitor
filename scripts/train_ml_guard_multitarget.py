"""
Trains and evaluates an XGBRegressor ML Guard.
Goal: Predict the continuous PnL percentage to robustly rank trade setups,
avoiding the arbitrary noise boundaries of hard binary classification.

Usage:
    python train_ml_guard_regressor.py --dataset path/to/trades.csv
    python train_ml_guard_regressor.py --dataset path/to/trades.csv --use-gpu
"""

import logging
import argparse
import json
import warnings
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import joblib
from xgboost import XGBRegressor
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    precision_recall_curve,
    mean_absolute_error,
)
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from scipy.stats import spearmanr

# ---------------------------------------------------------------------------
# Warnings & Logging
# ---------------------------------------------------------------------------
warnings.filterwarnings(
    "ignore",
    message=".*Falling back to prediction using DMatrix due to mismatched devices.*",
    category=UserWarning,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LEAKAGE_COLS = {
    # ── User Defined Exclusion List ──
    "date", "pnl_pct", "mfe_pct", "mae_pct",
    "open", "high", "low", "close",
    "dvl_10", "dvl_rate_10", "velocity_10", "velocity_10_norm", "price_distance_10", "ars_10",
    "dvl_30", "dvl_rate_30", "velocity_30", "velocity_30_norm", "price_distance_30", "ars_30", "pdd_30",
    "dvl_60", "dvl_rate_60", "velocity_60", "velocity_60_norm", "price_distance_60", "ars_60",
    "dvl_120", "dvl_rate_120", "velocity_120", "velocity_120_norm", "price_distance_120", "ars_120", "pdd_120",
    "cdvl", "vel_dp5",
    "dvwap_10", "dvwap_30", "dvwap_60", "dvwap_120", "cwvap", "smoothed_cwvap", "va_high", "va_low", "dvwap_bear_stack",
    "cwc", "mcs_composite", "psz_smooth", "rsz_smooth",
    "accum_div", "distrib_div", "coherence_raw", "coherence", "pdd_120_threshold",
    "mfm", "prt_slope", "prt_accel",
    
    # ── Standard Leakage/Primitve Exclusions (Preserved) ──
    "level_1", "volume", "delivery_qty", "delivery_pct", "va_profile_width",
    "dist_high_10", "dist_low_10", "range_width_10",
    "dist_high_22", "dist_low_22", "range_width_22",
    "dist_high_63", "dist_low_63", "range_width_63",
    "dist_high_252", "dist_low_252", "range_width_252",
    "rdv", "atr_20", "is_ath",
    # ── Added by Vikram to experiment with feature importance and model behavior.
    "prt" ,"trigger_prt", "fas_sell_threshold", 
    "base_tightness", "bars_at_base", "range_pos_22", "range_pos_63", "range_pos_252", "range_pos_10", 
    "cts_sell_threshold", "psz_buy_threshold","psz_sell_threshold","rsz_sell_threshold", "rsz_buy_threshold",
    "close_to_prev_low", "trigger_accel", "cts_negative" , "mcs_composite_slope", "ibs", "rsz_v", "cwc_slope",
    "rdv_slope_z", "wick_ratio", "in_trade_pnl",
    "fas_negative","fas_buy_threshold","fas","psz_v_rising_2","psz_v",
    "trigger_fas","price_slope_z","psz_v_rising_3","typical_price_spearman_5","fas_is_flat",
    "cts_accel_rising_2", "cts_buy_threshold", "is_cts_accel_flat","cts_accel_above_threshold",
    "cts_accel_rising_3"
}

PARAM_GRID = {
    "max_depth":        [3, 4, 5],          # cap depth; 6 was allowing too-complex trees
    "learning_rate":    [0.01, 0.03, 0.05],
    "subsample":        [0.6, 0.7, 0.8],    # lower bound reduced to force more diversity
    "colsample_bytree": [0.6, 0.7, 0.8],
    "min_child_weight": [3, 5, 10],         # raised floor — prevents splits on tiny leaf samples
    "gamma":            [0.1, 0.3, 0.5],    # min split-loss gain; 0 removed (too permissive)
    "reg_alpha":        [0, 0.1, 0.5, 1.0], # L1: drives weak features toward zero
    "reg_lambda":       [1.0, 2.0, 5.0],    # L2: shrinks all weights; XGB default is 1.0
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def select_features(df: pd.DataFrame) -> list[str]:
    """Return numeric columns that are not in the leakage set."""
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    return [c for c in numeric_cols if c not in LEAKAGE_COLS and c != "label"]


def train_with_search(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    n_iter: int,
    cv_splits: int,
    random_state: int,
    use_gpu: bool = False,
) -> tuple[object, RandomizedSearchCV]:
    """
    Three-phase training for Regressor:
      1. RandomizedSearchCV (TimeSeriesSplit) finds best hyperparameters (MAE).
      2. Refit best params with early stopping to find the correct tree count.
      3. Final refit at the discovered tree count (no early stopping).
    """
    # We use Absolute Error (MAE) because financial returns have fat tails.
    # MSE would heavily over-penalize predicting +1% on a trade that went +30%,
    # dragging the entire model off course. MAE is far more robust to PnL outliers.
    xgb_kwargs = {
        "tree_method": "hist",
        "objective": "reg:absoluteerror",
    }
    if use_gpu:
        xgb_kwargs["device"] = "cuda"
        log.info("GPU acceleration enabled (device='cuda')")

    log.info("Evaluation metric: Mean Absolute Error (MAE)")

    # ── Phase 1: hyperparameter search ───────────────────────────────────────
    base_reg = XGBRegressor(
        n_estimators=300,
        eval_metric="mae",
        random_state=random_state,
        **xgb_kwargs,
    )
    tscv = TimeSeriesSplit(n_splits=cv_splits)
    search = RandomizedSearchCV(
        base_reg,
        PARAM_GRID,
        n_iter=n_iter,
        cv=tscv,
        scoring="neg_mean_absolute_error",
        refit=False,
        verbose=1,
        random_state=random_state,
        n_jobs=-1,
    )
    search.fit(X_train, y_train)
    log.info("Best params : %s", search.best_params_)
    # search.best_score_ is negative MAE
    log.info("Best CV MAE: %.4f", -search.best_score_)

    # ── Phase 2: find optimal tree count via early stopping ──────────────────
    probe_reg = XGBRegressor(
        n_estimators=1000,
        eval_metric="mae",
        early_stopping_rounds=50,
        random_state=random_state,
        **xgb_kwargs,
        **search.best_params_,
    )
    probe_reg.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )
    best_n_trees = probe_reg.best_iteration + 1

    MIN_TREES = 50
    if best_n_trees < MIN_TREES:
        log.warning(
            "Early stopping found only %d trees — below minimum of %d. "
            "Using %d trees.",
            best_n_trees, MIN_TREES, MIN_TREES,
        )
        best_n_trees = MIN_TREES
    else:
        log.info("Early stopping: best tree count = %d", best_n_trees)

    # ── Phase 3: final refit at exact tree count — no early stopping ─────────
    final_reg = XGBRegressor(
        n_estimators=best_n_trees,
        eval_metric="mae",
        random_state=random_state,
        **xgb_kwargs,
        **search.best_params_,
    )
    final_reg.fit(X_train, y_train)
    log.info("Final model retrained at %d trees (no early stopping)", best_n_trees)

    return final_reg, search


def tune_threshold(
    model,
    X_val: pd.DataFrame,
    y_val_actual_pnl: pd.Series,
    desired_precision: float,
    min_recall: float = 0.10,
) -> float:
    """
    Find the best predicted PnL threshold.
    For tuning, we treat 'actual PnL > 0' as the Ground Truth 'Take'.
    We evaluate the predicted continuous PnL score as the 'probability' equivalent.
    """
    preds = model.predict(X_val)
    y_binary = (y_val_actual_pnl > 0).astype(int)

    log.info(
        "Val Predicted PnL distribution  "
        "min=%.4f  p25=%.4f  p50=%.4f  p75=%.4f  p90=%.4f  max=%.4f",
        np.percentile(preds, 0),  np.percentile(preds, 25),
        np.percentile(preds, 50), np.percentile(preds, 75),
        np.percentile(preds, 90), np.percentile(preds, 100),
    )

    precision_curve, recall_curve, thresholds = precision_recall_curve(y_binary, preds)
    prec = precision_curve[:-1]
    rec  = recall_curve[:-1]

    threshold_a, n_fires_a = None, 0
    viable_a = np.where(prec >= desired_precision)[0]
    if len(viable_a) > 0:
        idx = viable_a[0]
        threshold_a = float(thresholds[idx])
        n_fires_a   = int((preds >= threshold_a).sum())
        log.info(
            "Strategy A (precision≥%.2f): threshold=%.4f%% predicted PnL  "
            "precision=%.4f  recall=%.4f  fires on %d trades",
            desired_precision, threshold_a, prec[idx], rec[idx], n_fires_a,
        )

    threshold_b, n_fires_b = None, 0
    viable_b = np.where(rec >= min_recall)[0]
    if len(viable_b) > 0:
        idx = int(np.where(rec >= min_recall)[0][np.argmax(prec[rec >= min_recall])])
        threshold_b = float(thresholds[idx])
        n_fires_b   = int((preds >= threshold_b).sum())
        log.info(
            "Strategy B (recall≥%.2f): threshold=%.4f%% predicted PnL  "
            "precision=%.4f  recall=%.4f  fires on %d trades",
            min_recall, threshold_b, prec[idx], rec[idx], n_fires_b,
        )

    if threshold_a is not None and threshold_b is not None:
        chosen = threshold_a if n_fires_a >= n_fires_b else threshold_b
        strategy = "A" if n_fires_a >= n_fires_b else "B"
        log.info("Selected Strategy %s threshold=%.4f%% predicted PnL (%d fires)", strategy, chosen, max(n_fires_a, n_fires_b))
        return chosen
    if threshold_a is not None:
        return threshold_a
    if threshold_b is not None:
        return threshold_b

    positive_rate = float(y_binary.mean())
    fallback = float(np.percentile(preds, (1 - positive_rate) * 100))
    log.warning(
        "Neither strategy achievable. Falling back to %.0f-th percentile "
        "threshold=%.4f (targets top %.0f%% of predicted scores)",
        (1 - positive_rate) * 100, fallback, positive_rate * 100,
    )
    return fallback


def evaluate(
    model,
    X: pd.DataFrame,
    y_actual_target: pd.Series,
    y_actual_pnl: pd.Series,
    threshold: float,
    split_name: str,
) -> dict:
    """Predict with a custom threshold and print a full evaluation summary."""
    preds_continuous = model.predict(X)
    preds_binary = (preds_continuous >= threshold).astype(int)
    y_binary = (y_actual_pnl > 0).astype(int)

    mae = mean_absolute_error(y_actual_target, preds_continuous)

    log.info("\n── %s Evaluation (Threshold: Predicted Score >= %.4f) ──", split_name, threshold)
    log.info("MAE (Actual vs Predicted Target): %.4f", mae)
    log.info(
        "Predictions → Take: %d  Skip: %d  (out of %d)",
        int(preds_binary.sum()), int((preds_binary == 0).sum()), len(preds_binary),
    )
    log.info(
        "\n%s",
        classification_report(
            y_binary, preds_binary,
            target_names=["Skip (PnL<=0)", "Take (PnL>0)"],
            zero_division=0,
        ),
    )

    report = classification_report(
        y_binary, preds_binary,
        target_names=["Skip", "Take"],
        zero_division=0,
        output_dict=True,
    )
    return {
        "split": split_name,
        "threshold": threshold,
        "mae": round(mae, 4),
        "n_predicted_take": int(preds_binary.sum()),
        "precision_take": round(report["Take"]["precision"], 4),
        "recall_take":    round(report["Take"]["recall"], 4),
        "f1_take":        round(report["Take"]["f1-score"], 4),
        "support_take":   int(report["Take"]["support"]),
    }


def trade_efficiency_report(
    model,
    X: pd.DataFrame,
    target: pd.Series,
    pnl: pd.Series,
    split_name: str,
    n_buckets: int = 5,
) -> pd.DataFrame:
    """
    Bucket all trades by their predicted score and report quality.
    """
    preds = model.predict(X)
    
    # y_binary is just for the hit_rate column calculation (Win Rate)
    y_binary = (pnl > 0).astype(int)

    frame = pd.DataFrame({
        "pred_score": preds,
        "actual_target": target.values,
        "label":    y_binary.values,
        "pnl":      pnl.values,
    })

    frame["bucket"] = pd.qcut(
        frame["pred_score"].rank(method="first"),
        n_buckets,
        labels=range(1, n_buckets + 1),
    )

    overall_hit_rate = float(y_binary.mean())

    rows = []
    for b, grp in frame.groupby("bucket", observed=True):
        winners = grp[grp["pnl"] > 0]
        row = {
            "bucket":           int(b),
            "score_range":      f"[{grp['pred_score'].min():.4f}, {grp['pred_score'].max():.4f}]",
            "n_trades":         len(grp),
            "hit_rate":         round(grp["label"].mean(), 4),
            "avg_pnl":          round(grp["pnl"].mean(), 4),
            "avg_target":       round(grp["actual_target"].mean(), 4),
            "lift":             round(grp["label"].mean() / overall_hit_rate, 3)
                                if overall_hit_rate > 0 else float("nan"),
        }
        rows.append(row)

    report_df = pd.DataFrame(rows)

    bucket_ranks = list(range(1, n_buckets + 1))
    rho, p_value = spearmanr(bucket_ranks, report_df["hit_rate"].tolist())
    if rho >= 0.8:
        rank_label = f"✓ STRONG  (ρ={rho:.2f})"
    elif rho >= 0.5:
        rank_label = f"~ MODERATE  (ρ={rho:.2f})"
    else:
        rank_label = f"✗ WEAK  (ρ={rho:.2f}) — model ranking unreliable"

    log.info(
        "\n══ %s Trade Efficiency Report (Target Ranking) ══\n"
        "  Overall hit rate: %.4f  |  Rank signal: %s\n\n%s",
        split_name,
        overall_hit_rate,
        rank_label,
        report_df.to_string(index=False),
    )
    return report_df


def feature_importance_df(model, feature_cols: list[str]) -> pd.DataFrame:
    """Return a sorted DataFrame of XGBoost gain-based feature importances."""
    scores = model.get_booster().get_score(importance_type="gain")
    df = pd.DataFrame(
        [(f, scores.get(f, 0.0)) for f in feature_cols],
        columns=["feature", "importance_gain"],
    ).sort_values("importance_gain", ascending=False).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Train XGBRegressor ML Guard")
    parser.add_argument("--dataset",       required=True,       help="Path to trade dataset CSV")
    parser.add_argument("--threshold",     type=float, default=None,
                        help="Override predicted PnL threshold (skips auto-tuning)")
    parser.add_argument("--min-precision", type=float, default=0.65,
                        help="Minimum precision target (Win Rate) for threshold tuning (default 0.65)")
    parser.add_argument("--min-recall",    type=float, default=0.10,
                        help="Coverage floor: threshold must fire on at least this "
                             "fraction of actual winners (default 0.10)")
    parser.add_argument("--n-iter",        type=int,   default=20,
                        help="RandomizedSearchCV iterations (default 20)")
    parser.add_argument("--cv-splits",     type=int,   default=5,
                        help="TimeSeriesSplit folds (default 5)")
    parser.add_argument("--target",        type=str,   default="pnl",
                        choices=["pnl", "mfe", "efficiency"],
                        help="Training target: 'pnl' (pnl_pct), 'mfe' (mfe_pct), or 'efficiency' (mfe/mae)")
    parser.add_argument("--output-dir",    default=None,
                        help="Directory to save model artefacts (default: auto)")
    parser.add_argument("--use-gpu",       action="store_true",
                        help="Enable GPU acceleration (device='cuda')")
    parser.add_argument("--random-state",  type=int,   default=42)
    args = parser.parse_args()

    # ── Load & sort ──────────────────────────────────────────────────────────
    data_path = Path(args.dataset)
    if not data_path.exists():
        log.error("Dataset not found: %s", data_path)
        raise SystemExit(1)

    df = pd.read_csv(data_path)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    log.info("Loaded %d rows from %s", len(df), data_path)

    # ── Target Variable Calculation ──────────────────────────────────────────
    if args.target == "pnl":
        target_col = "pnl_pct"
        log.info("Target: PnL Percentage")
    elif args.target == "mfe":
        target_col = "mfe_pct"
        log.info("Target: Maximum Favorable Excursion (MFE) %")
    elif args.target == "efficiency":
        # Efficiency = MFE / (MAE + 1.0) to avoid div zero and handle small pullbacks
        df["efficiency_ratio"] = df["mfe_pct"] / (df["mae_pct"] + 1.0)
        target_col = "efficiency_ratio"
        log.info("Target: Efficiency Ratio (MFE / (MAE + 1.0))")
    
    # ── Features ─────────────────────────────────────────────────────────────
    feature_cols = select_features(df)
    # Ensure our calculated target or raw targets aren't in features
    feature_cols = [c for c in feature_cols if c not in ["efficiency_ratio", "pnl_pct", "mfe_pct", "mae_pct"]]
    log.info("Using %d features", len(feature_cols))

    # Let XGBoost handle NaN natively.
    X = df[feature_cols]
    y = df[target_col]
    y_pnl = df["pnl_pct"] # Always keep pnl_pct for final 'Win' evaluation

    # ── Train / val / test split (chronological, no shuffle) ─────────────────
    n = len(X)
    train_end = int(n * 0.70)
    val_end   = int(n * 0.85)

    X_train, y_train = X.iloc[:train_end],       y.iloc[:train_end]
    X_val,   y_val   = X.iloc[train_end:val_end], y.iloc[train_end:val_end]
    X_test,  y_test  = X.iloc[val_end:],          y.iloc[val_end:]
    
    y_val_pnl  = y_pnl.iloc[train_end:val_end]
    y_test_pnl = y_pnl.iloc[val_end:]

    log.info(
        "Split sizes → train: %d | val: %d | test: %d",
        len(X_train), len(X_val), len(X_test),
    )

    # ── Train ─────────────────────────────────────────────────────────────────
    best_model, search = train_with_search(
        X_train, y_train,
        X_val, y_val,
        n_iter=args.n_iter,
        cv_splits=args.cv_splits,
        random_state=args.random_state,
        use_gpu=args.use_gpu,
    )

    # ── Threshold selection ───────────────────────────────────────────────────
    if args.threshold is not None:
        threshold = args.threshold
        log.info("Using user-supplied threshold: %.4f", threshold)
    else:
        # We still tune threshold to maximize Precision/Recall of PnL > 0
        threshold = tune_threshold(best_model, X_val, y_val_pnl, args.min_precision, args.min_recall)

    # ── Evaluate on held-out test set ─────────────────────────────────────────
    val_metrics  = evaluate(best_model, X_val, y_val, y_val_pnl, threshold, "Validation")
    test_metrics = evaluate(best_model, X_test, y_test, y_test_pnl, threshold, "Test")

    # ── Trade Efficiency Report ───────────────────────────────────────────────
    trade_efficiency_report(best_model, X_val, y_val, y_val_pnl, "Validation")
    trade_efficiency_report(best_model, X_test, y_test, y_test_pnl, "Test")

    # ── Feature importance ────────────────────────────────────────────────────
    fi_df = feature_importance_df(best_model, feature_cols)
    log.info("\nTop 15 features by gain:\n%s", fi_df.head(15).to_string(index=False))

    # ── Save artefacts ────────────────────────────────────────────────────────
    if args.output_dir:
        models_dir = Path(args.output_dir)
    else:
        models_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "trading" / "signals" / "savgol_cts" / "models"
        )
    models_dir.mkdir(parents=True, exist_ok=True)

    date_str   = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = models_dir / f"model_reg_{args.target}_{date_str}.joblib"
    meta_path  = models_dir / f"model_reg_{args.target}_{date_str}_meta.json"
    fi_path    = models_dir / f"model_reg_{args.target}_{date_str}_feature_importance.csv"

    # Model bundle
    model_bundle = {
        "model":        best_model,
        "feature_cols": feature_cols,
        "threshold":    threshold,
        "target":       args.target,
        "type":         "regressor"
    }
    joblib.dump(model_bundle, model_path)
    log.info("Model saved → %s", model_path)

    # Metadata / metrics JSON
    meta = {
        "trained_at":    date_str,
        "dataset":       str(data_path),
        "target":        args.target,
        "type":          "regressor",
        "n_features":    len(feature_cols),
        "threshold":     threshold,
        "best_params":   search.best_params_,
        "cv_mae":        round(-search.best_score_, 4),
        "val_metrics":   val_metrics,
        "test_metrics":  test_metrics,
    }

    meta_path.write_text(json.dumps(meta, indent=2))
    log.info("Metadata saved → %s", meta_path)

    # Feature importance CSV
    fi_df.to_csv(fi_path, index=False)
    log.info("Feature importance saved → %s", fi_path)

    log.info("\n✓ Done. Test precision (Take/Win Rate): %.4f", test_metrics["precision_take"])


if __name__ == "__main__":
    main()
