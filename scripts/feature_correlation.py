#!/usr/bin/env python3
"""
Feature Correlation Study
─────────────────────────
Empirical analysis of which features predict forward returns,
which are redundant, and how correlations change across regimes.

Outputs:
  - Console tables with significance markers
  - Heatmap PNGs (feature_fwd_return, cross_corr, lag_analysis, regime splits)
  - Optional CSVs (--csv flag)

Usage:
  venv/bin/python3 scripts/feature_correlation.py --ticker RELIANCE
  venv/bin/python3 scripts/feature_correlation.py --watchlist "My Watchlist" --csv
  venv/bin/python3 scripts/feature_correlation.py --ticker RELIANCE --horizons 3 5 10 --max-lag 10
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.feature_selection import mutual_info_regression
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Add project root to sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.divergence_engine.utils import load_symbol_data
from src.divergence_engine.base_calc import BaseCalculator
from src.divergence_engine.dvl_ledger import DVLLedger
from src.divergence_engine.cwvap import CompositeVWAP
from src.divergence_engine.cwc import CrossWindowCoherence
from src.divergence_engine.mcs import MoneyCompositeScore
from src.divergence_engine.analysis import compute_trend_participation

# ── Feature list ─────────────────────────────────────────────────────────────

FEATURES = [
    # CTS
    "cts", "cts_slope", "cts_accel",
    # PSZ
    "price_slope_z", "psz_smooth", "psz_v",
    # MCS
    "mcs_composite", "mcs_composite_slope",
    # CWC
    "cwc", "cwc_slope", "cwc_delta",
    # Delivery
    "rdv", "rdv_slope_z", "accum_div", "distrib_div", "cdvl", "pdd_120",
    "velocity_10_norm", "velocity_30_norm", "velocity_60_norm", "velocity_120_norm",
    # Price
    "mfm",
    # Coherence
    "coherence",
]

# Liquidity tiers — terciles of median daily delivery_qty per symbol
N_LIQUIDITY_TIERS = 3
TIER_LABELS = {0: "low_liquidity", 1: "mid_liquidity", 2: "high_liquidity"}

WARMUP_ROWS = 120
MIN_USABLE_BARS = 250
REGIME_MIN_BARS = 30


# ── Pipeline ─────────────────────────────────────────────────────────────────

def compute_full_pipeline(ticker: str) -> pd.DataFrame | None:
    """Run BaseCalc → DVL → CWVAP(causal_savgol) → CWC → MCS → trend_participation."""
    df = load_symbol_data(ticker)
    if df is None or len(df) < WARMUP_ROWS + MIN_USABLE_BARS:
        print(f"  [{ticker}] Insufficient data ({len(df) if df is not None else 0} bars), skipping")
        return None

    base_calc = BaseCalculator()
    dvl_ledger = DVLLedger()
    cwvap_calc = CompositeVWAP(cts_strategy="causal_savgol")
    cwc_calc = CrossWindowCoherence()
    mcs_calc = MoneyCompositeScore()

    df = base_calc.compute_all(df)
    df = dvl_ledger.compute_all(df)
    df = cwvap_calc.compute_all(df)
    df = cwc_calc.compute_all(df)
    df = mcs_calc.compute_all(df)
    df = compute_trend_participation(df)
    return df


def compute_forward_returns(df: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    """ATR-normalized forward returns: how many ATRs price moved in N bars."""
    for n in horizons:
        raw_ret = df["close"].shift(-n) / df["close"] - 1.0
        atr_frac = df["atr_20"] / df["close"]
        df[f"fwd_ret_{n}_atr"] = raw_ret / atr_frac
    return df


def get_feature_columns(df: pd.DataFrame) -> list[str]:
    """Return features that actually exist in the DataFrame."""
    return [f for f in FEATURES if f in df.columns]


# ── Watchlist ────────────────────────────────────────────────────────────────

def get_watchlist_symbols(watchlist_name: str) -> list[str]:
    """Retrieve symbols from a named watchlist in the database."""
    from src.database import get_db_connection
    conn = get_db_connection()
    query = """
        SELECT symbol FROM watchlist_items
        JOIN watchlists ON watchlists.id = watchlist_items.watchlist_id
        WHERE watchlists.name = ?
        ORDER BY watchlist_items.display_order;
    """
    try:
        df_items = pd.read_sql_query(query, conn, params=[watchlist_name])
        return df_items["symbol"].tolist()
    finally:
        conn.close()


# ── Analysis functions ───────────────────────────────────────────────────────

def _spearman_with_pval(x: pd.Series, y: pd.Series) -> tuple[float, float]:
    """Spearman rank correlation with pairwise NaN drop."""
    mask = x.notna() & y.notna()
    if mask.sum() < 10:
        return np.nan, np.nan
    rho, p = stats.spearmanr(x[mask], y[mask])
    return rho, p


def _sig_marker(p: float) -> str:
    if p < 0.01:
        return "**"
    elif p < 0.05:
        return "*"
    return ""


def feature_vs_forward_return(
    df: pd.DataFrame, features: list[str], horizons: list[int]
) -> pd.DataFrame:
    """Spearman rho of each feature vs each forward-return horizon."""
    rows = []
    for feat in features:
        row = {"feature": feat}
        for n in horizons:
            target = f"fwd_ret_{n}_atr"
            rho, p = _spearman_with_pval(df[feat], df[target])
            row[f"rho_{n}"] = rho
            row[f"p_{n}"] = p
        rows.append(row)
    return pd.DataFrame(rows).set_index("feature")


def feature_cross_correlation(df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    """Pairwise Spearman between all features."""
    n_feat = len(features)
    matrix = np.full((n_feat, n_feat), np.nan)
    for i in range(n_feat):
        for j in range(i, n_feat):
            rho, _ = _spearman_with_pval(df[features[i]], df[features[j]])
            matrix[i, j] = rho
            matrix[j, i] = rho
    return pd.DataFrame(matrix, index=features, columns=features)


def lag_analysis(
    df: pd.DataFrame, features: list[str], target: str, max_lag: int
) -> pd.DataFrame:
    """Shift each feature by 1..max_lag bars and correlate with target."""
    rows = []
    for feat in features:
        row = {"feature": feat}
        # lag=0 (contemporaneous)
        rho, _ = _spearman_with_pval(df[feat], df[target])
        row["lag_0"] = rho
        for lag in range(1, max_lag + 1):
            rho, _ = _spearman_with_pval(df[feat].shift(lag), df[target])
            row[f"lag_{lag}"] = rho
        rows.append(row)
    return pd.DataFrame(rows).set_index("feature")


def regime_conditional(
    df: pd.DataFrame, features: list[str], target: str
) -> dict[str, pd.DataFrame]:
    """Split correlations by gradient_shape and mcs_composite sign."""
    results = {}

    # --- gradient_shape regimes ---
    if "gradient_shape" in df.columns:
        regimes = df["gradient_shape"].dropna().unique()
        regime_rows = []
        for regime in sorted(regimes):
            sub = df[df["gradient_shape"] == regime]
            if len(sub) < REGIME_MIN_BARS:
                continue
            row = {"regime": regime, "n_bars": len(sub)}
            for feat in features:
                rho, p = _spearman_with_pval(sub[feat], sub[target])
                row[feat] = rho
                row[f"{feat}_p"] = p
            regime_rows.append(row)
        if regime_rows:
            results["gradient_shape"] = pd.DataFrame(regime_rows).set_index("regime")

    # --- mcs_composite sign ---
    if "mcs_composite" in df.columns:
        bull = df[df["mcs_composite"] >= 0]
        bear = df[df["mcs_composite"] < 0]
        mcs_rows = []
        for label, sub in [("bull (mcs>=0)", bull), ("bear (mcs<0)", bear)]:
            if len(sub) < REGIME_MIN_BARS:
                continue
            row = {"regime": label, "n_bars": len(sub)}
            for feat in features:
                rho, p = _spearman_with_pval(sub[feat], sub[target])
                row[feat] = rho
                row[f"{feat}_p"] = p
            mcs_rows.append(row)
        if mcs_rows:
            results["mcs_sign"] = pd.DataFrame(mcs_rows).set_index("regime")

    return results


# ── Per-stock correlation ────────────────────────────────────────────────

def per_stock_correlation(
    df: pd.DataFrame, features: list[str], horizons: list[int]
) -> pd.DataFrame:
    """Compute Spearman per symbol, then report mean/std/median across stocks."""
    symbols = df["symbol"].unique()
    per_sym = {feat: {n: [] for n in horizons} for feat in features}

    for sym in symbols:
        sub = df[df["symbol"] == sym]
        if len(sub) < MIN_USABLE_BARS:
            continue
        for feat in features:
            for n in horizons:
                target = f"fwd_ret_{n}_atr"
                rho, p = _spearman_with_pval(sub[feat], sub[target])
                if not np.isnan(rho):
                    per_sym[feat][n].append(rho)

    rows = []
    for feat in features:
        row = {"feature": feat}
        for n in horizons:
            vals = per_sym[feat][n]
            if vals:
                arr = np.array(vals)
                row[f"mean_{n}"] = np.mean(arr)
                row[f"median_{n}"] = np.median(arr)
                row[f"std_{n}"] = np.std(arr)
                row[f"n_stocks_{n}"] = len(vals)
                # % of stocks where rho has consistent sign with mean
                sign_mean = np.sign(np.mean(arr))
                row[f"sign_agree_{n}"] = np.mean(np.sign(arr) == sign_mean) if sign_mean != 0 else np.nan
            else:
                row[f"mean_{n}"] = np.nan
                row[f"median_{n}"] = np.nan
                row[f"std_{n}"] = np.nan
                row[f"n_stocks_{n}"] = 0
                row[f"sign_agree_{n}"] = np.nan
        rows.append(row)
    return pd.DataFrame(rows).set_index("feature")


# ── Liquidity-tiered correlation ─────────────────────────────────────────

def assign_liquidity_tiers(df: pd.DataFrame) -> pd.DataFrame:
    """Assign each bar a liquidity tier based on per-symbol median delivery_qty."""
    if "delivery_qty" not in df.columns:
        return df
    sym_median = df.groupby("symbol")["delivery_qty"].median()
    # Tercile boundaries
    boundaries = sym_median.quantile([1/3, 2/3]).values
    tier_map = {}
    for sym, med in sym_median.items():
        if med <= boundaries[0]:
            tier_map[sym] = 0
        elif med <= boundaries[1]:
            tier_map[sym] = 1
        else:
            tier_map[sym] = 2
    df["liquidity_tier"] = df["symbol"].map(tier_map)
    return df


def liquidity_tiered_correlation(
    df: pd.DataFrame, features: list[str], horizons: list[int]
) -> dict[str, pd.DataFrame]:
    """Run feature_vs_forward_return per liquidity tier."""
    results = {}
    for tier_idx, tier_label in TIER_LABELS.items():
        sub = df[df["liquidity_tier"] == tier_idx]
        n_symbols = sub["symbol"].nunique()
        if len(sub) < MIN_USABLE_BARS:
            continue
        result = feature_vs_forward_return(sub, features, horizons)
        result["n_bars"] = len(sub)
        result["n_symbols"] = n_symbols
        results[tier_label] = result
    return results


# ── Quintile analysis (nonlinear effects) ────────────────────────────────

def quintile_analysis(
    df: pd.DataFrame, features: list[str], horizons: list[int]
) -> pd.DataFrame:
    """Bin each feature into quintiles, compute mean fwd return per bin.
    Reports Q5-Q1 spread and monotonicity score."""
    rows = []
    for feat in features:
        row = {"feature": feat}
        for n in horizons:
            target = f"fwd_ret_{n}_atr"
            mask = df[feat].notna() & df[target].notna()
            sub = df.loc[mask, [feat, target]].copy()
            if len(sub) < 100:
                row[f"spread_{n}"] = np.nan
                row[f"mono_{n}"] = np.nan
                for q in range(1, 6):
                    row[f"Q{q}_{n}"] = np.nan
                continue
            try:
                sub["qbin"] = pd.qcut(sub[feat], 5, labels=False, duplicates="drop")
            except ValueError:
                row[f"spread_{n}"] = np.nan
                row[f"mono_{n}"] = np.nan
                for q in range(1, 6):
                    row[f"Q{q}_{n}"] = np.nan
                continue
            means = sub.groupby("qbin")[target].mean()
            for q in range(5):
                row[f"Q{q+1}_{n}"] = means.get(q, np.nan)
            # Spread: Q5 - Q1
            q1 = means.get(0, np.nan)
            q5 = means.get(means.index.max(), np.nan)
            row[f"spread_{n}"] = q5 - q1 if not (np.isnan(q1) or np.isnan(q5)) else np.nan
            # Monotonicity: Spearman of quintile rank vs mean return
            if len(means) >= 3:
                mono_rho, _ = stats.spearmanr(means.index, means.values)
                row[f"mono_{n}"] = mono_rho
            else:
                row[f"mono_{n}"] = np.nan
        rows.append(row)
    return pd.DataFrame(rows).set_index("feature")


def mutual_information_analysis(
    df: pd.DataFrame, features: list[str], target: str, n_sample: int = 50000
) -> pd.DataFrame:
    """Mutual information between each feature and the target.
    Subsample for speed on large datasets."""
    # Build clean matrix
    cols = features + [target]
    sub = df[cols].dropna()
    if len(sub) > n_sample:
        sub = sub.sample(n=n_sample, random_state=42)
    if len(sub) < 100:
        return pd.DataFrame()
    X = sub[features].values
    y = sub[target].values
    mi = mutual_info_regression(X, y, random_state=42, n_neighbors=5)
    result = pd.DataFrame({"feature": features, "MI_bits": mi}).set_index("feature")
    return result.sort_values("MI_bits", ascending=False)


# ── Plotting ─────────────────────────────────────────────────────────────────

def plot_heatmap(
    data: pd.DataFrame | np.ndarray,
    title: str,
    filename: str,
    output_dir: str,
    row_labels: list[str] | None = None,
    col_labels: list[str] | None = None,
    annotate: bool = True,
    vmin: float = -1.0,
    vmax: float = 1.0,
    figsize: tuple[int, int] | None = None,
):
    """matplotlib imshow heatmap — no seaborn dependency."""
    if isinstance(data, pd.DataFrame):
        values = data.values
        if row_labels is None:
            row_labels = list(data.index)
        if col_labels is None:
            col_labels = list(data.columns)
    else:
        values = data

    n_rows, n_cols = values.shape
    if figsize is None:
        figsize = (max(8, n_cols * 0.7 + 3), max(6, n_rows * 0.45 + 2))

    fig, ax = plt.subplots(figsize=figsize)
    im = ax.imshow(values, aspect="auto", cmap="RdBu_r", vmin=vmin, vmax=vmax)

    if col_labels:
        ax.set_xticks(range(n_cols))
        ax.set_xticklabels(col_labels, rotation=45, ha="right", fontsize=8)
    if row_labels:
        ax.set_yticks(range(n_rows))
        ax.set_yticklabels(row_labels, fontsize=8)

    if annotate and n_rows * n_cols <= 600:
        for i in range(n_rows):
            for j in range(n_cols):
                val = values[i, j]
                if np.isnan(val):
                    continue
                color = "white" if abs(val) > 0.5 else "black"
                ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                        fontsize=6, color=color)

    ax.set_title(title, fontsize=11, pad=10)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    plt.tight_layout()

    path = os.path.join(output_dir, filename)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Saved: {path}")


def _cluster_order(corr_matrix: pd.DataFrame) -> list[str]:
    """Simple hierarchical-ish ordering by first principal axis."""
    # Use absolute correlation distance for ordering
    vals = corr_matrix.values.copy()
    np.fill_diagonal(vals, 0)
    # Order by sum of absolute correlations (most connected first)
    order = np.argsort(-np.nansum(np.abs(vals), axis=1))
    return [corr_matrix.index[i] for i in order]


# ── Console display ──────────────────────────────────────────────────────────

def print_fwd_return_table(result: pd.DataFrame, horizons: list[int]):
    """Print sorted correlation table with significance markers."""
    print("\n" + "=" * 70)
    print("FEATURE vs FORWARD RETURN (Spearman rho)")
    print("=" * 70)

    for n in horizons:
        rho_col = f"rho_{n}"
        p_col = f"p_{n}"
        sorted_df = result.sort_values(rho_col, key=abs, ascending=False)
        print(f"\n--- Forward {n}-bar ATR-normalised return ---")
        print(f"{'Feature':<25} {'rho':>8} {'p-value':>10} {'sig':>4}")
        print("-" * 50)
        for feat, row in sorted_df.iterrows():
            rho = row[rho_col]
            p = row[p_col]
            if np.isnan(rho):
                continue
            sig = _sig_marker(p)
            print(f"{feat:<25} {rho:>8.4f} {p:>10.2e} {sig:>4}")


def print_redundancy(cross_corr: pd.DataFrame, threshold: float = 0.7):
    """Print top redundant feature pairs."""
    print("\n" + "=" * 70)
    print(f"REDUNDANT FEATURE PAIRS (|rho| > {threshold})")
    print("=" * 70)
    features = cross_corr.index.tolist()
    pairs = []
    for i in range(len(features)):
        for j in range(i + 1, len(features)):
            rho = cross_corr.iloc[i, j]
            if abs(rho) > threshold:
                pairs.append((features[i], features[j], rho))
    pairs.sort(key=lambda x: abs(x[2]), reverse=True)
    if not pairs:
        print("  No pairs above threshold.")
    for a, b, rho in pairs:
        print(f"  {a:<25} ↔ {b:<25}  rho={rho:+.3f}")


def print_regime_table(regime_results: dict[str, pd.DataFrame], features: list[str]):
    """Print regime-conditional correlations."""
    for regime_type, df_regime in regime_results.items():
        print(f"\n{'=' * 70}")
        print(f"REGIME-CONDITIONAL CORRELATIONS: {regime_type}")
        print("=" * 70)
        # Only show feature columns (not p-value columns or n_bars)
        feat_cols = [f for f in features if f in df_regime.columns]
        for regime_name, row in df_regime.iterrows():
            n = int(row["n_bars"])
            print(f"\n  {regime_name} (n={n})")
            print(f"  {'Feature':<25} {'rho':>8} {'sig':>4}")
            print(f"  {'-' * 40}")
            # Sort by absolute rho
            feat_rhos = [(f, row[f], row.get(f"{f}_p", np.nan)) for f in feat_cols]
            feat_rhos.sort(key=lambda x: abs(x[1]) if not np.isnan(x[1]) else 0, reverse=True)
            for feat, rho, p in feat_rhos:
                if np.isnan(rho):
                    continue
                sig = _sig_marker(p) if not np.isnan(p) else ""
                print(f"  {feat:<25} {rho:>8.4f} {sig:>4}")


def print_per_stock_table(result: pd.DataFrame, horizons: list[int]):
    """Print per-stock averaged correlations."""
    print("\n" + "=" * 70)
    print("PER-STOCK AVERAGED CORRELATIONS (mean rho across stocks)")
    print("=" * 70)
    for n in horizons:
        mean_col = f"mean_{n}"
        std_col = f"std_{n}"
        med_col = f"median_{n}"
        agree_col = f"sign_agree_{n}"
        sorted_df = result.sort_values(mean_col, key=abs, ascending=False)
        print(f"\n--- Forward {n}-bar ---")
        print(f"{'Feature':<25} {'mean':>8} {'median':>8} {'std':>8} {'sign%':>7}")
        print("-" * 60)
        for feat, row in sorted_df.iterrows():
            mean = row[mean_col]
            if np.isnan(mean):
                continue
            med = row[med_col]
            std = row[std_col]
            agree = row[agree_col]
            agree_s = f"{agree*100:.0f}%" if not np.isnan(agree) else "   -"
            print(f"{feat:<25} {mean:>8.4f} {med:>8.4f} {std:>8.4f} {agree_s:>7}")


def print_liquidity_tier_table(tier_results: dict[str, pd.DataFrame], horizons: list[int]):
    """Print liquidity-tiered correlation comparison."""
    print("\n" + "=" * 70)
    print("LIQUIDITY-TIERED CORRELATIONS")
    print("=" * 70)
    for n in horizons:
        rho_col = f"rho_{n}"
        print(f"\n--- Forward {n}-bar ---")
        header = f"{'Feature':<25}"
        for tier_label in TIER_LABELS.values():
            header += f" {tier_label:>16}"
        print(header)
        print("-" * (25 + 17 * len(TIER_LABELS)))

        # Collect all features
        all_feats = set()
        for res in tier_results.values():
            all_feats.update(res.index.tolist())

        for feat in sorted(all_feats):
            line = f"{feat:<25}"
            for tier_label in TIER_LABELS.values():
                if tier_label in tier_results and feat in tier_results[tier_label].index:
                    rho = tier_results[tier_label].loc[feat, rho_col]
                    p = tier_results[tier_label].loc[feat, f"p_{n}"]
                    sig = _sig_marker(p)
                    line += f" {rho:>12.4f}{sig:<4}"
                else:
                    line += f" {'—':>16}"
            print(line)

    # Print tier sizes
    print("\nTier sizes:")
    for tier_label, res in tier_results.items():
        n_bars = int(res["n_bars"].iloc[0])
        n_sym = int(res["n_symbols"].iloc[0])
        print(f"  {tier_label}: {n_bars:,} bars, {n_sym} symbols")


def print_quintile_table(result: pd.DataFrame, horizons: list[int]):
    """Print quintile spreads and monotonicity."""
    print("\n" + "=" * 70)
    print("QUINTILE ANALYSIS (Q5-Q1 spread, monotonicity)")
    print("=" * 70)
    for n in horizons:
        spread_col = f"spread_{n}"
        mono_col = f"mono_{n}"
        sorted_df = result.sort_values(spread_col, key=abs, ascending=False)
        print(f"\n--- Forward {n}-bar ---")
        print(f"{'Feature':<25} {'Q1':>8} {'Q2':>8} {'Q3':>8} {'Q4':>8} {'Q5':>8} {'spread':>8} {'mono':>6}")
        print("-" * 85)
        for feat, row in sorted_df.iterrows():
            spread = row[spread_col]
            if np.isnan(spread):
                continue
            qs = [row.get(f"Q{q}_{n}", np.nan) for q in range(1, 6)]
            mono = row[mono_col]
            qs_str = " ".join(f"{q:>8.4f}" if not np.isnan(q) else f"{'—':>8}" for q in qs)
            mono_str = f"{mono:>6.2f}" if not np.isnan(mono) else f"{'—':>6}"
            print(f"{feat:<25} {qs_str} {spread:>8.4f} {mono_str}")


def print_mutual_info(mi_result: pd.DataFrame):
    """Print mutual information ranking."""
    if mi_result.empty:
        return
    print("\n" + "=" * 70)
    print("MUTUAL INFORMATION (nonlinear dependency, in nats)")
    print("=" * 70)
    print(f"{'Feature':<25} {'MI':>10}")
    print("-" * 36)
    for feat, row in mi_result.iterrows():
        print(f"{feat:<25} {row['MI_bits']:>10.6f}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Feature Correlation Study")
    parser.add_argument("--ticker", default=None, help="Single ticker to analyse")
    parser.add_argument("--watchlist", default=None, help="Watchlist name from DB")
    parser.add_argument("--horizons", nargs="+", type=int, default=[5, 10, 20],
                        help="Forward return horizons in bars (default: 5 10 20)")
    parser.add_argument("--max-lag", type=int, default=5,
                        help="Max lag for lag analysis (default: 5)")
    parser.add_argument("--csv", action="store_true", help="Export CSV files")
    parser.add_argument("--output-dir", default="output/correlation_study",
                        help="Directory for output files")
    args = parser.parse_args()

    # Resolve symbols
    if args.ticker:
        symbols = [args.ticker]
    elif args.watchlist:
        symbols = get_watchlist_symbols(args.watchlist)
        if not symbols:
            print(f"No symbols found in watchlist: {args.watchlist}")
            sys.exit(1)
        print(f"Watchlist '{args.watchlist}': {len(symbols)} symbols")
    else:
        print("Provide --ticker or --watchlist")
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    max_horizon = max(args.horizons)

    # ── Build pooled dataset ─────────────────────────────────────────────
    all_dfs = []
    for sym in symbols:
        print(f"Processing {sym}...")
        try:
            df = compute_full_pipeline(sym)
        except Exception as e:
            print(f"  [{sym}] Pipeline error: {e}")
            continue
        if df is None:
            continue

        df = compute_forward_returns(df, args.horizons)

        # Drop warmup + trailing NaN from forward returns
        df = df.iloc[WARMUP_ROWS:]
        df = df.iloc[: len(df) - max_horizon]

        features = get_feature_columns(df)
        fwd_cols = [f"fwd_ret_{n}_atr" for n in args.horizons]
        keep_cols = features + fwd_cols + ["close", "atr_20"]
        if "delivery_qty" in df.columns:
            keep_cols.append("delivery_qty")
        if "gradient_shape" in df.columns:
            keep_cols.append("gradient_shape")
        if "mcs_composite" in df.columns and "mcs_composite" not in keep_cols:
            keep_cols.append("mcs_composite")

        keep_cols = [c for c in keep_cols if c in df.columns]
        df_clean = df[keep_cols].copy()
        df_clean["symbol"] = sym

        usable = df_clean[features].dropna(how="all").shape[0]
        if usable < MIN_USABLE_BARS:
            print(f"  [{sym}] Only {usable} usable bars after cleanup, skipping")
            continue

        all_dfs.append(df_clean)
        print(f"  [{sym}] {usable} usable bars")

    if not all_dfs:
        print("No usable data. Exiting.")
        sys.exit(1)

    pooled = pd.concat(all_dfs, ignore_index=True)
    features = get_feature_columns(pooled)
    n_total = len(pooled)
    print(f"\nPooled dataset: {n_total} bars, {len(all_dfs)} symbols, {len(features)} features")

    # ── 1. Feature vs Forward Return ─────────────────────────────────────
    print("\nComputing feature vs forward return correlations...")
    fwd_result = feature_vs_forward_return(pooled, features, args.horizons)
    print_fwd_return_table(fwd_result, args.horizons)

    # Heatmap: features × horizons (rho values only)
    rho_cols = [f"rho_{n}" for n in args.horizons]
    heatmap_data = fwd_result[rho_cols].copy()
    heatmap_data.columns = [f"fwd_{n}" for n in args.horizons]
    plot_heatmap(heatmap_data, "Feature vs Forward Return (Spearman rho)",
                 "feature_fwd_return_heatmap.png", args.output_dir)

    # ── 2. Feature Cross-Correlation ─────────────────────────────────────
    print("\nComputing feature cross-correlations...")
    cross_corr = feature_cross_correlation(pooled, features)
    print_redundancy(cross_corr)

    # Clustered heatmap
    ordered = _cluster_order(cross_corr)
    cross_corr_ordered = cross_corr.loc[ordered, ordered]
    plot_heatmap(cross_corr_ordered, "Feature Cross-Correlation (Spearman)",
                 "feature_cross_corr.png", args.output_dir)

    # ── 3. Lag Analysis ──────────────────────────────────────────────────
    # Use the middle horizon as target
    mid_horizon = args.horizons[len(args.horizons) // 2]
    target_col = f"fwd_ret_{mid_horizon}_atr"
    print(f"\nComputing lag analysis (target: fwd_ret_{mid_horizon}_atr, max_lag={args.max_lag})...")
    lag_result = lag_analysis(pooled, features, target_col, args.max_lag)

    print(f"\n{'=' * 70}")
    print(f"LAG ANALYSIS — target: fwd_ret_{mid_horizon}_atr")
    print("=" * 70)
    lag_cols = [f"lag_{i}" for i in range(args.max_lag + 1)]
    print(lag_result[lag_cols].round(4).to_string())

    plot_heatmap(lag_result[lag_cols], f"Lag Analysis vs fwd_ret_{mid_horizon}_atr",
                 "feature_lag_analysis.png", args.output_dir,
                 col_labels=["lag_0"] + [f"lag_{i}" for i in range(1, args.max_lag + 1)])

    # ── 4. Regime-Conditional ────────────────────────────────────────────
    print(f"\nComputing regime-conditional correlations...")
    regime_results = regime_conditional(pooled, features, target_col)
    print_regime_table(regime_results, features)

    # Regime heatmaps
    for regime_type, df_regime in regime_results.items():
        feat_cols = [f for f in features if f in df_regime.columns]
        if not feat_cols:
            continue
        hmap_data = df_regime[feat_cols].astype(float)
        plot_heatmap(hmap_data, f"Regime Correlations: {regime_type}",
                     f"regime_{regime_type}.png", args.output_dir)

    # ── 5. Per-stock Correlation ────────────────────────────────────────
    print("\nComputing per-stock averaged correlations...")
    per_stock_result = per_stock_correlation(pooled, features, args.horizons)
    print_per_stock_table(per_stock_result, args.horizons)

    # Heatmap: per-stock mean rho
    mean_cols = [f"mean_{n}" for n in args.horizons]
    ps_heatmap = per_stock_result[mean_cols].copy()
    ps_heatmap.columns = [f"fwd_{n}" for n in args.horizons]
    plot_heatmap(ps_heatmap, "Per-Stock Averaged Correlation (mean rho)",
                 "per_stock_fwd_return.png", args.output_dir)

    # ── 6. Liquidity-Tiered Correlation ──────────────────────────────────
    if "delivery_qty" in pooled.columns and pooled["symbol"].nunique() > 5:
        print("\nAssigning liquidity tiers and computing tiered correlations...")
        pooled = assign_liquidity_tiers(pooled)
        tier_results = liquidity_tiered_correlation(pooled, features, args.horizons)
        print_liquidity_tier_table(tier_results, args.horizons)

        # Heatmap per tier
        for tier_label, res in tier_results.items():
            rho_cols_tier = [f"rho_{n}" for n in args.horizons]
            hmap = res[rho_cols_tier].copy()
            hmap.columns = [f"fwd_{n}" for n in args.horizons]
            n_sym = int(res["n_symbols"].iloc[0])
            plot_heatmap(hmap, f"Feature vs Fwd Return — {tier_label} ({n_sym} stocks)",
                         f"tier_{tier_label}.png", args.output_dir)

    # ── 7. Quintile Analysis (nonlinear) ─────────────────────────────────
    print("\nComputing quintile analysis...")
    quintile_result = quintile_analysis(pooled, features, args.horizons)
    print_quintile_table(quintile_result, args.horizons)

    # Quintile spread heatmap
    spread_cols = [f"spread_{n}" for n in args.horizons]
    q_heatmap = quintile_result[spread_cols].copy()
    q_heatmap.columns = [f"fwd_{n}" for n in args.horizons]
    max_abs = max(q_heatmap.abs().max().max(), 0.01)
    plot_heatmap(q_heatmap, "Quintile Spread (Q5 - Q1)",
                 "quintile_spread.png", args.output_dir,
                 vmin=-max_abs, vmax=max_abs)

    # ── 8. Mutual Information ────────────────────────────────────────────
    print(f"\nComputing mutual information (target: fwd_ret_{mid_horizon}_atr)...")
    mi_result = mutual_information_analysis(pooled, features, target_col)
    print_mutual_info(mi_result)

    # ── CSV export ───────────────────────────────────────────────────────
    if args.csv:
        fwd_result.to_csv(os.path.join(args.output_dir, "feature_correlations.csv"))
        cross_corr.to_csv(os.path.join(args.output_dir, "cross_correlation_matrix.csv"))
        lag_result.to_csv(os.path.join(args.output_dir, "lag_analysis.csv"))
        for regime_type, df_regime in regime_results.items():
            df_regime.to_csv(os.path.join(args.output_dir, f"regime_{regime_type}.csv"))
        per_stock_result.to_csv(os.path.join(args.output_dir, "per_stock_correlations.csv"))
        if "liquidity_tier" in pooled.columns:
            for tier_label, res in tier_results.items():
                res.to_csv(os.path.join(args.output_dir, f"tier_{tier_label}.csv"))
        quintile_result.to_csv(os.path.join(args.output_dir, "quintile_analysis.csv"))
        if not mi_result.empty:
            mi_result.to_csv(os.path.join(args.output_dir, "mutual_information.csv"))
        print(f"\nCSVs saved to {args.output_dir}/")

    print(f"\nDone. N={n_total} pooled bars across {len(all_dfs)} symbols.")


if __name__ == "__main__":
    main()
