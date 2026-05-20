"""
Trains and evaluates an XGBClassifier ML Guard.
Goal: Identify high-conviction, positive-PnL trade setups (Top 20% PnL = label 1).

Usage:
    python train_ml_guard.py --dataset path/to/trades.csv
    python train_ml_guard.py --dataset path/to/trades.csv --threshold 0.6 --top-quantile 0.75
    python train_ml_guard.py --dataset path/to/trades.csv --label-mode positive_pnl
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
from xgboost import XGBClassifier
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    precision_recall_curve,
    average_precision_score,
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
# Columns that leak future information or are raw price/volume primitives
LEAKAGE_COLS = {
    "pnl_pct", "mfe_pct", "mae_pct", "level_1",
    "open", "high", "low", "close", "volume", "delivery_qty", "delivery_pct",
    "cwvap", "smoothed_cwvap", "va_high", "va_low", "va_profile_width",
    "dvwap_10", "dvwap_30", "dvwap_60", "dvwap_120",
    "dist_high_10", "dist_low_10", "range_width_10",
    "dist_high_22", "dist_low_22", "range_width_22",
    "dist_high_63", "dist_low_63", "range_width_63",
    "dist_high_252", "dist_low_252", "range_width_252",
    "dvl_10", "dvl_rate_10", "velocity_10", "price_distance_10", "ars_10",
    "dvl_30", "dvl_rate_30", "velocity_30", "price_distance_30", "ars_30",
    "dvl_60", "dvl_rate_60", "velocity_60", "price_distance_60", "ars_60",
    "dvl_120", "dvl_rate_120", "velocity_120", "price_distance_120", "ars_120",
    "cdvl", "rdv", "atr_20",
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


class PlattCalibratedModel:
    """
    Thin wrapper that applies Platt scaling (sigmoid) to XGBoost's raw probabilities.
    """
    def __init__(self, base_model: XGBClassifier, a: float, b: float):
        self.base_model = base_model
        self._a = a
        self._b = b
        self.classes_ = np.array([0, 1])

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        from scipy.special import expit
        raw = self.base_model.predict_proba(X)[:, 1]
        cal = expit(self._a * raw + self._b)
        return np.column_stack([1 - cal, cal])

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)

    # expose booster for feature importance
    def get_booster(self):
        return self.base_model.get_booster()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def build_label(series: pd.Series, mode: str, top_quantile: float) -> pd.Series:
    """
    Two label modes:

    quantile (default):
        Label = 1 → top (1 - top_quantile) fraction by PnL.
        E.g. top_quantile=0.80 → top 20% trades.
        Use when you want the model to distinguish elite setups from merely good ones.

    positive_pnl:
        Label = 1 → any trade with pnl_pct > 0.
        More learnable boundary; recommended when baseline AUPRC ≈ random.
    """
    if mode == "positive_pnl":
        labels = (series > 0).astype(int)
        log.info(
            "Label mode: positive_pnl  |  positives: %d / %d  (%.1f%%)",
            labels.sum(), len(labels), labels.mean() * 100,
        )
    else:
        threshold = series.quantile(top_quantile)
        labels = (series >= threshold).astype(int)
        log.info(
            "Label mode: quantile (q%.0f=%.4f)  |  positives: %d / %d  (%.1f%%)",
            top_quantile * 100, threshold,
            labels.sum(), len(labels), labels.mean() * 100,
        )
    return labels


def select_features(df: pd.DataFrame) -> list[str]:
    """Return numeric columns that are not in the leakage set and not 'label'."""
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
    Three-phase training:
      1. RandomizedSearchCV (TimeSeriesSplit) finds best hyperparameters.
      2. Refit best params with early stopping to find the correct tree count.
         - Near-balanced classes (positive rate > 30%): use 'aucpr' — logloss
           plateaus too early (~20 trees) on balanced data and stops prematurely.
         - Imbalanced classes: use 'logloss' — more stable with few positives.
      3. Final refit at the discovered tree count (no early stopping) so the model
         is consistent with what the CV search evaluated.
      4. Optional Platt calibration when the model has meaningful probability spread.

    Returns (model, search_object).
    """
    pos = int(y_train.sum())
    neg = int((y_train == 0).sum())
    spw = neg / pos if pos > 0 else 1.0
    positive_rate_train = pos / (pos + neg)
    log.info("scale_pos_weight = %.3f  (neg=%d, pos=%d)", spw, neg, pos)
    log.info(
        "Random baseline AUPRC ≈ %.4f  (positive rate in val set)",
        y_val.mean(),
    )

    # Step 1: choose early stopping metric based on class balance
    # logloss converges in ~20 trees on near-balanced data giving a falsely "optimal"
    # early stop. aucpr keeps improving longer and is the metric we actually care about.
    es_metric = "aucpr" if positive_rate_train > 0.30 else "logloss"
    log.info(
        "Early stopping metric: %s  (positive rate=%.2f)",
        es_metric, positive_rate_train,
    )
    
    xgb_kwargs = {"tree_method": "hist"}
    if use_gpu:
        xgb_kwargs["device"] = "cuda"
        log.info("GPU acceleration enabled (device='cuda')")

    # ── Phase 1: hyperparameter search ───────────────────────────────────────
    base_clf = XGBClassifier(
        n_estimators=300,
        scale_pos_weight=spw,
        eval_metric=es_metric,
        random_state=random_state,
        **xgb_kwargs,
    )
    tscv = TimeSeriesSplit(n_splits=cv_splits)
    search = RandomizedSearchCV(
        base_clf,
        PARAM_GRID,
        n_iter=n_iter,
        cv=tscv,
        scoring="average_precision",
        refit=False,
        verbose=1,
        random_state=random_state,
        n_jobs=-1,
    )
    search.fit(X_train, y_train)
    log.info("Best params : %s", search.best_params_)
    log.info("Best CV avg-precision: %.4f", search.best_score_)

    # ── Phase 2: find optimal tree count via early stopping ──────────────────
    probe_clf = XGBClassifier(
        n_estimators=1000,
        scale_pos_weight=spw,
        eval_metric=es_metric,
        early_stopping_rounds=50,
        random_state=random_state,
        **xgb_kwargs,
        **search.best_params_,
    )
    probe_clf.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )
    best_n_trees = probe_clf.best_iteration + 1

    # Enforce a minimum tree count: the CV search found hyperparams for 300-tree
    # models, so those params may converge quickly (e.g. 17 trees) on the probe.
    # With 49 features and depth 3-5, fewer than 50 trees cannot meaningfully
    # sample feature interactions. If early stopping fires below this floor,
    # we use the floor and log a warning so the mismatch is visible.
    MIN_TREES = 50
    if best_n_trees < MIN_TREES:
        log.warning(
            "Early stopping found only %d trees — below minimum of %d. "
            "Using %d trees. Consider lowering learning_rate in PARAM_GRID "
            "to let the model learn more gradually.",
            best_n_trees, MIN_TREES, MIN_TREES,
        )
        best_n_trees = MIN_TREES
    else:
        log.info("Early stopping: best tree count = %d", best_n_trees)

    # ── Phase 3: final refit at exact tree count — no early stopping ─────────
    # This ensures the final model matches what the CV search was optimising:
    # a fixed-depth ensemble at best_n_trees, not a model with early-stop state.
    final_clf = XGBClassifier(
        n_estimators=best_n_trees,
        scale_pos_weight=spw,
        eval_metric=es_metric,
        random_state=random_state,
        **xgb_kwargs,
        **search.best_params_,
    )
    final_clf.fit(X_train, y_train)
    log.info("Final model retrained at %d trees (no early stopping)", best_n_trees)

    # ── Phase 4: optional Platt calibration ──────────────────────────────────
    raw_val_probs = final_clf.predict_proba(X_val)[:, 1]
    prob_range = float(raw_val_probs.max() - raw_val_probs.min())
    log.info(
        "Raw probability range on val: [%.4f, %.4f]  spread=%.4f",
        raw_val_probs.min(), raw_val_probs.max(), prob_range,
    )

    # Step 5: lowered tree guard from 50 → 20; spread=0.05 threshold kept.
    # A 21-tree model with spread=0.17 is absolutely calibratable.
    if best_n_trees >= 10 and prob_range > 0.05:
        from scipy.special import expit
        from scipy.optimize import curve_fit

        def _platt(x, a, b):
            return expit(a * x + b)

        try:
            (a, b), _ = curve_fit(
                _platt, raw_val_probs, y_val,
                p0=[1.0, 0.0], bounds=([0, -np.inf], [np.inf, np.inf]), maxfev=2000
            )
            log.info("Platt scaling fitted: a=%.4f  b=%.4f", a, b)
            calibrated = PlattCalibratedModel(final_clf, float(a), float(b))
            log.info("Platt calibration applied")
        except Exception as exc:
            log.warning("Platt calibration failed (%s); using raw model", exc)
            calibrated = final_clf
    else:
        log.warning(
            "Skipping calibration: only %d trees and prob spread=%.4f "
            "(need ≥10 trees and spread >0.05)",
            best_n_trees, prob_range,
        )
        calibrated = final_clf

    return calibrated, search


def tune_threshold(
    model,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    desired_precision: float,
    min_recall: float = 0.10,
) -> float:
    """
    Find the best probability threshold using two strategies, pick the more
    permissive one (lower threshold) so the model actually fires on trades:

    Strategy A — precision target:
        Lowest threshold achieving >= desired_precision.
        Can produce near-zero recall if the model is uncertain.

    Strategy B — coverage floor:
        Highest-precision threshold that still fires on >= min_recall of
        positives. Ensures the guard isn't so tight it never approves a trade.

    Returns the threshold from whichever strategy fires on MORE trades.
    If neither is achievable, falls back to the (1 - positive_rate) percentile.
    """
    probs = model.predict_proba(X_val)[:, 1]

    log.info(
        "Val probability distribution  "
        "min=%.4f  p25=%.4f  p50=%.4f  p75=%.4f  p90=%.4f  max=%.4f",
        np.percentile(probs, 0),  np.percentile(probs, 25),
        np.percentile(probs, 50), np.percentile(probs, 75),
        np.percentile(probs, 90), np.percentile(probs, 100),
    )

    precision_curve, recall_curve, thresholds = precision_recall_curve(y_val, probs)
    # precision_curve[-1] = 1.0 with no predictions; align to thresholds length
    prec = precision_curve[:-1]
    rec  = recall_curve[:-1]

    # Strategy A: precision target
    threshold_a, n_fires_a = None, 0
    viable_a = np.where(prec >= desired_precision)[0]
    if len(viable_a) > 0:
        idx = viable_a[0]
        threshold_a = float(thresholds[idx])
        n_fires_a   = int((probs >= threshold_a).sum())
        log.info(
            "Strategy A (precision≥%.2f): threshold=%.4f  precision=%.4f  "
            "recall=%.4f  fires on %d trades",
            desired_precision, threshold_a, prec[idx], rec[idx], n_fires_a,
        )

    # Strategy B: coverage floor — highest precision among thresholds with recall >= min_recall
    threshold_b, n_fires_b = None, 0
    viable_b = np.where(rec >= min_recall)[0]
    if len(viable_b) > 0:
        # pick highest precision satisfying recall floor
        idx = int(np.where(rec >= min_recall)[0][np.argmax(prec[rec >= min_recall])])
        threshold_b = float(thresholds[idx])
        n_fires_b   = int((probs >= threshold_b).sum())
        log.info(
            "Strategy B (recall≥%.2f): threshold=%.4f  precision=%.4f  "
            "recall=%.4f  fires on %d trades",
            min_recall, threshold_b, prec[idx], rec[idx], n_fires_b,
        )

    # Pick the threshold that fires on more trades (more coverage)
    if threshold_a is not None and threshold_b is not None:
        chosen = threshold_a if n_fires_a >= n_fires_b else threshold_b
        strategy = "A" if n_fires_a >= n_fires_b else "B"
        log.info("Selected Strategy %s threshold=%.4f (%d fires)", strategy, chosen, max(n_fires_a, n_fires_b))
        return chosen
    if threshold_a is not None:
        return threshold_a
    if threshold_b is not None:
        return threshold_b

    # Fallback: percentile cut targeting the top positive_rate% of scores
    positive_rate = float(y_val.mean())
    fallback = float(np.percentile(probs, (1 - positive_rate) * 100))
    log.warning(
        "Neither strategy achievable. Falling back to %.0f-th percentile "
        "threshold=%.4f (targets top %.0f%% of scores)",
        (1 - positive_rate) * 100, fallback, positive_rate * 100,
    )
    return fallback


def evaluate(
    model,
    X: pd.DataFrame,
    y: pd.Series,
    threshold: float,
    split_name: str,
) -> dict:
    """Predict with a custom threshold and print a full evaluation summary."""
    probs = model.predict_proba(X)[:, 1]
    preds = (probs >= threshold).astype(int)
    ap = average_precision_score(y, probs)

    log.info("\n── %s Evaluation (threshold=%.4f) ──", split_name, threshold)
    log.info("Average Precision (AUPRC): %.4f  |  random baseline ≈ %.4f", ap, y.mean())
    log.info(
        "Predictions → Take: %d  Skip: %d  (out of %d)",
        int(preds.sum()), int((preds == 0).sum()), len(preds),
    )
    log.info(
        "\n%s",
        classification_report(
            y, preds,
            target_names=["Skip", "Take"],
            zero_division=0,        # silence UndefinedMetricWarning when model never fires
        ),
    )
    log.info("Confusion matrix:\n%s", confusion_matrix(y, preds))

    report = classification_report(
        y, preds,
        target_names=["Skip", "Take"],
        zero_division=0,
        output_dict=True,
    )
    return {
        "split": split_name,
        "threshold": threshold,
        "avg_precision_auprc": round(ap, 4),
        "random_baseline_auprc": round(float(y.mean()), 4),
        "n_predicted_take": int(preds.sum()),
        "precision_take": round(report["Take"]["precision"], 4),
        "recall_take":    round(report["Take"]["recall"], 4),
        "f1_take":        round(report["Take"]["f1-score"], 4),
        "support_take":   int(report["Take"]["support"]),
    }


def trade_efficiency_report(
    model,
    X: pd.DataFrame,
    y: pd.Series,
    pnl: pd.Series,
    split_name: str,
    label_mode: str = "quantile",
    n_buckets: int = 5,
) -> pd.DataFrame:
    """
    Bucket all trades by their probability score (quintiles by default) and
    report PnL quality per bucket.

    Signal quality indicators logged:
      - Spearman rank correlation between bucket rank and hit_rate.
        More robust than strict monotonicity: a single noisy bucket won't
        falsely flag a model that clearly trends upward.
        ρ > 0.8 = strong signal | 0.5–0.8 = moderate | <0.5 = weak/noise.
      - avg_pnl_winners: mean PnL of winning trades per bucket. Flat values
        here mean the model ranks frequency of winning, not magnitude.

    Note: in positive_pnl mode, hit_rate == pct_positive by definition
    (label=1 iff pnl>0). pct_positive is suppressed in that mode to avoid
    printing the same number twice.

    Columns
    -------
    bucket          : 1 = lowest score, N = highest score
    score_range     : [min, max] probability score in the bucket
    n_trades        : number of trades in bucket
    hit_rate        : fraction that are true positives (label=1)
    avg_pnl         : mean pnl_pct in bucket
    median_pnl      : median pnl_pct in bucket
    avg_pnl_winners : mean pnl_pct of only the positive-pnl trades in bucket
    pct_positive    : fraction with pnl_pct > 0  [omitted in positive_pnl mode]
    lift            : hit_rate / overall_hit_rate  (>1 = model adds value vs random)
    """
    probs = model.predict_proba(X)[:, 1]

    frame = pd.DataFrame({
        "prob":  probs,
        "label": y.values,
        "pnl":   pnl.values,
    })

    # Use raw rank-based cut so equal-probability ties don't collapse buckets
    frame["bucket"] = pd.qcut(
        frame["prob"].rank(method="first"),
        n_buckets,
        labels=range(1, n_buckets + 1),
    )

    overall_hit_rate = float(y.mean())

    rows = []
    for b, grp in frame.groupby("bucket", observed=True):
        winners = grp[grp["pnl"] > 0]
        row = {
            "bucket":           int(b),
            "score_range":      f"[{grp['prob'].min():.4f}, {grp['prob'].max():.4f}]",
            "n_trades":         len(grp),
            "hit_rate":         round(grp["label"].mean(), 4),
            "avg_pnl":          round(grp["pnl"].mean(), 4),
            "median_pnl":       round(grp["pnl"].median(), 4),
            "avg_pnl_winners":  round(winners["pnl"].mean(), 4) if len(winners) > 0 else float("nan"),
            "lift":             round(grp["label"].mean() / overall_hit_rate, 3)
                                if overall_hit_rate > 0 else float("nan"),
        }
        # pct_positive is identical to hit_rate in positive_pnl mode — omit it
        if label_mode != "positive_pnl":
            row["pct_positive"] = round((grp["pnl"] > 0).mean(), 4)
        rows.append(row)

    report_df = pd.DataFrame(rows)

    # Spearman rank correlation: bucket rank vs hit_rate
    # Strict monotonicity fails on a single noisy bucket even when the overall
    # trend is clearly upward. Spearman measures the overall ranking quality.
    bucket_ranks = list(range(1, n_buckets + 1))
    rho, p_value = spearmanr(bucket_ranks, report_df["hit_rate"].tolist())
    if rho >= 0.8:
        rank_label = f"✓ STRONG  (ρ={rho:.2f})"
    elif rho >= 0.5:
        rank_label = f"~ MODERATE  (ρ={rho:.2f})"
    else:
        rank_label = f"✗ WEAK  (ρ={rho:.2f}) — model ranking unreliable"

    # Warn if avg_pnl_winners is flat (model ranks frequency, not magnitude)
    winner_pnls = report_df["avg_pnl_winners"].dropna()
    winner_spread = winner_pnls.max() - winner_pnls.min() if len(winner_pnls) > 1 else 0
    winner_note = (
        f"avg_pnl_winners spread={winner_spread:.2f}% "
        + ("— model ranks win-frequency only, not win-size" if winner_spread < 1.0 else "— model distinguishes win magnitude ✓")
    )

    log.info(
        "\n══ %s Trade Efficiency Report  (n_buckets=%d) ══\n"
        "  Overall hit rate: %.4f  |  Rank signal: %s\n"
        "  %s\n\n%s",
        split_name,
        n_buckets,
        overall_hit_rate,
        rank_label,
        winner_note,
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
    parser = argparse.ArgumentParser(description="Train XGBClassifier ML Guard")
    parser.add_argument("--dataset",       required=True,       help="Path to trade dataset CSV")
    parser.add_argument("--label-mode",    default="positive_pnl",
                        choices=["quantile", "positive_pnl"],
                        help="'quantile': top-N%% PnL. "
                             "'positive_pnl': any trade with pnl_pct > 0 (default).")
    parser.add_argument("--top-quantile",  type=float, default=0.80,
                        help="PnL quantile cut-off for label=1 (default 0.80 → top 20%%)")
    parser.add_argument("--threshold",     type=float, default=None,
                        help="Override probability threshold (skips auto-tuning)")
    parser.add_argument("--min-precision", type=float, default=0.65,
                        help="Minimum precision target for threshold tuning (default 0.65)")
    parser.add_argument("--min-recall",    type=float, default=0.10,
                        help="Coverage floor: threshold must fire on at least this "
                             "fraction of positives (default 0.10)")
    parser.add_argument("--n-iter",        type=int,   default=20,
                        help="RandomizedSearchCV iterations (default 20)")
    parser.add_argument("--cv-splits",     type=int,   default=5,
                        help="TimeSeriesSplit folds (default 5)")
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

    # ── Label (1 = good trade, 0 = skip) ─────────────────────────────────────
    df["label"] = build_label(df["pnl_pct"], args.label_mode, args.top_quantile)

    # ── Features ─────────────────────────────────────────────────────────────
    feature_cols = select_features(df)
    log.info("Using %d features: %s", len(feature_cols), feature_cols)

    # Let XGBoost handle NaN natively. XGBoost's sparsity-aware split algorithm
    # learns a default direction for missing values, which is superior to
    # filling with a sentinel value like -1 or 0.
    X = df[feature_cols]
    y = df["label"]

    # ── Train / val / test split (chronological, no shuffle) ─────────────────
    n = len(X)
    train_end = int(n * 0.70)
    val_end   = int(n * 0.85)

    X_train, y_train = X.iloc[:train_end],       y.iloc[:train_end]
    X_val,   y_val   = X.iloc[train_end:val_end], y.iloc[train_end:val_end]
    X_test,  y_test  = X.iloc[val_end:],          y.iloc[val_end:]

    # Keep pnl_pct aligned to val/test for the efficiency report
    pnl_val  = df["pnl_pct"].iloc[train_end:val_end].reset_index(drop=True)
    pnl_test = df["pnl_pct"].iloc[val_end:].reset_index(drop=True)

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
        threshold = tune_threshold(best_model, X_val, y_val, args.min_precision, args.min_recall)

    # ── Evaluate on held-out test set ─────────────────────────────────────────
    val_metrics  = evaluate(best_model, X_val,  y_val,  threshold, "Validation")
    test_metrics = evaluate(best_model, X_test, y_test, threshold, "Test")

    # ── Trade Efficiency Report ───────────────────────────────────────────────
    y_val_reset  = y_val.reset_index(drop=True)
    y_test_reset = y_test.reset_index(drop=True)
    trade_efficiency_report(best_model, X_val,  y_val_reset,  pnl_val,  "Validation", args.label_mode)
    trade_efficiency_report(best_model, X_test, y_test_reset, pnl_test, "Test",       args.label_mode)

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

    # Use a fixed date for the file name as requested, or keep the timestamp
    # We will use the date string but allow overriding if needed.
    date_str   = datetime.now().strftime("%Y%m%d") # Changing to just date to match model_clf_20260516.joblib format
    model_path = models_dir / f"model_clf_{date_str}.joblib"
    meta_path  = models_dir / f"model_clf_{date_str}_meta.json"
    fi_path    = models_dir / f"model_clf_{date_str}_feature_importance.csv"

    # Model bundle
    model_bundle = {
        "model":        best_model,
        "feature_cols": feature_cols,
        "threshold":    threshold,
        "top_quantile": args.top_quantile,
        "label_mode":   args.label_mode,
    }
    joblib.dump(model_bundle, model_path)
    log.info("Model saved → %s", model_path)

    # Metadata / metrics JSON
    meta = {
        "trained_at":    date_str,
        "dataset":       str(data_path),
        "label_mode":    args.label_mode,
        "n_features":    len(feature_cols),
        "top_quantile":  args.top_quantile,
        "threshold":     threshold,
        "best_params":   search.best_params_,
        "cv_avg_precision": round(search.best_score_, 4),
        "val_metrics":   val_metrics,
        "test_metrics":  test_metrics,
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    log.info("Metadata saved → %s", meta_path)

    # Feature importance CSV
    fi_df.to_csv(fi_path, index=False)
    log.info("Feature importance saved → %s", fi_path)

    log.info("\n✓ Done. Test precision (Take): %.4f", test_metrics["precision_take"])


if __name__ == "__main__":
    main()