#!/usr/bin/env python3
"""
Panel Builder — Step 1 of the XGBoost Divergence Engine pipeline.

Pools all symbols from the database into a single long-format panel dataset,
applies quality gates, runs the engine + feature engineer per symbol, and
writes the result to data/panel.parquet.

Two modes:
    full  — Process all symbols. Apply quality gate. Write from scratch.
    delta — Append new rows to an existing panel. Hard-stop on any anomaly.

Usage:
    python scripts/panel_builder.py --mode full
    python scripts/panel_builder.py --mode full --limit 5
    python scripts/panel_builder.py --mode full --dry-run
    python scripts/panel_builder.py --mode delta
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database import get_db_connection
from src.divergence_engine.engine import DivergenceEngine
from src.divergence_engine.repository import DeliveryRepository
from src.feature_engineer import engineer_features

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("panel_builder")

# Paths
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PANEL_PATH = DATA_DIR / "panel.parquet"
REJECTED_PATH = DATA_DIR / "rejected_symbols.csv"
DELTA_WARNINGS_PATH = DATA_DIR / "delta_warnings.csv"

# Quality gate thresholds
PRICE_COVERAGE_MIN = 0.80
DELIVERY_COVERAGE_MIN = 0.70
ANOMALOUS_MOVE_THRESHOLD = 0.40  # 40%
CA_DATE_TOLERANCE_DAYS = 2  # allow spike within ±2 days of CA ex_date

# Engine output columns that must NOT land in the ML feature panel.
# These are rule-engine labels/annotations, not XGBoost input features.
PANEL_EXCLUDE_COLS: frozenset[str] = frozenset([
    "integrated_state",   # rule-engine string label — not an ML feature
    "coherence_stamp",    # UI annotation e.g. " [Strong]"
])

# Delta mode lookback (enough for 120d window warmup)
DELTA_LOOKBACK_DAYS = 200

# Singleton repo
_repo = DeliveryRepository()


# ──────────────────────────────────────────────────────────────────────────────
# Symbol universe
# ──────────────────────────────────────────────────────────────────────────────

def get_all_symbols() -> list[str]:
    """Return all distinct symbols from nse_delivery_log, excluding indices."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM nse_delivery_log "
            "WHERE symbol NOT LIKE 'NIFTY%' "
            "ORDER BY symbol"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def get_date_spine() -> set[str]:
    """Return all distinct trading dates in the database.

    Uses the most liquid symbol's dates as the reference spine.
    """
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT record_date FROM nse_delivery_log "
            "ORDER BY record_date"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def get_corporate_actions(symbol: str) -> pd.DataFrame:
    """Fetch corporate actions for a symbol."""
    conn = get_db_connection()
    try:
        df = pd.read_sql_query(
            "SELECT ex_date, ca_type, ratio_factor FROM corporate_actions "
            "WHERE symbol = ? ORDER BY ex_date DESC",
            conn,
            params=[symbol],
        )
    finally:
        conn.close()
    if not df.empty:
        df["ex_date"] = pd.to_datetime(df["ex_date"])
    return df


# ──────────────────────────────────────────────────────────────────────────────
# Quality Gate
# ──────────────────────────────────────────────────────────────────────────────

def _is_near_ca(spike_date: pd.Timestamp, ca_actions: pd.DataFrame) -> bool:
    """Check if a spike date is within ±N days of any corporate action."""
    if ca_actions.empty:
        return False
    for _, ca in ca_actions.iterrows():
        delta = abs((spike_date - ca["ex_date"]).days)
        if delta <= CA_DATE_TOLERANCE_DAYS:
            return True
    return False


def quality_gate_full(
    symbol: str,
    df: pd.DataFrame,
    total_trading_days: int,
    ca_actions: pd.DataFrame,
) -> tuple[bool, str]:
    """Full quality gate for initial panel build.

    Returns (passed, reason).
    """
    if len(df) < 10:
        return False, f"insufficient_data={len(df)}_rows"

    # 1. Price coverage
    price_days = df["close"].notna().sum()
    coverage = price_days / total_trading_days
    if coverage < PRICE_COVERAGE_MIN:
        return False, f"price_coverage={coverage:.1%}"

    # 2. Anomalous moves not explained by corporate actions
    returns = df["close"].pct_change().abs()
    spike_mask = returns > ANOMALOUS_MOVE_THRESHOLD
    if spike_mask.any():
        for idx in df.index[spike_mask]:
            spike_date = df.loc[idx, "date"]
            spike_pct = returns.loc[idx]
            if not _is_near_ca(spike_date, ca_actions):
                return False, (
                    f"anomalous_move={spike_date.date()} "
                    f"({spike_pct:.0%}) — no matching CA"
                )

    # 3. Delivery coverage — count days where delivery_qty is recorded AND positive.
    # Explicit notna() guard: NaN > 0 is False in pandas but the intent is clearer
    # spelled out, and guards against future upstream NaN-injection bugs.
    delivery_days = (df["delivery_qty"].notna() & (df["delivery_qty"] > 0)).sum()
    del_coverage = delivery_days / total_trading_days
    if del_coverage < DELIVERY_COVERAGE_MIN:
        return False, f"delivery_coverage={del_coverage:.1%}"

    return True, "PASSED"


def quality_gate_delta(
    symbol: str,
    df: pd.DataFrame,
    ca_actions: pd.DataFrame,
) -> tuple[bool, str]:
    """Delta quality gate — anomalous-move check only.

    If this fails, the entire delta run must abort.
    """
    returns = df["close"].pct_change().abs()
    spike_mask = returns > ANOMALOUS_MOVE_THRESHOLD
    if spike_mask.any():
        for idx in df.index[spike_mask]:
            spike_date = df.loc[idx, "date"]
            spike_pct = returns.loc[idx]
            if not _is_near_ca(spike_date, ca_actions):
                return False, (
                    f"anomalous_move={spike_date.date()} "
                    f"({spike_pct:.0%}) — likely unadjusted CA"
                )
    return True, "OK"


# ──────────────────────────────────────────────────────────────────────────────
# Engine + Feature Pipeline (per symbol)
# ──────────────────────────────────────────────────────────────────────────────

def process_symbol(
    symbol: str,
    df_raw: pd.DataFrame | None = None,
    start_date: str | None = None,
) -> pd.DataFrame:
    """Run engine + feature_engineer for a single symbol.

    Returns a DataFrame with a 'symbol' column added, with engine label
    columns (integrated_state, coherence_stamp) stripped so they cannot
    accidentally appear as XGBoost features in the panel.
    """
    if df_raw is not None:
        engine = DivergenceEngine(ticker=symbol, df=df_raw)
    else:
        engine = DivergenceEngine(ticker=symbol, start_date=start_date)

    result = engine.run()
    df_feat = engineer_features(result.ledger)
    df_feat["symbol"] = symbol

    # Drop engine label/annotation columns — they are rule-engine outputs,
    # not ML input features. Drop only what exists to stay forward-compatible.
    drop_cols = [c for c in PANEL_EXCLUDE_COLS if c in df_feat.columns]
    if drop_cols:
        df_feat = df_feat.drop(columns=drop_cols)

    return df_feat


# ──────────────────────────────────────────────────────────────────────────────
# Full Build
# ──────────────────────────────────────────────────────────────────────────────

def build_full(limit: int | None = None, dry_run: bool = False) -> None:
    """Full panel build: quality gate → engine → feature_engineer → parquet."""
    logger.info("Starting FULL panel build")

    symbols = get_all_symbols()
    if limit:
        symbols = symbols[:limit]
        logger.info("Limited to first %d symbols", limit)

    logger.info("Total symbols to process: %d", len(symbols))

    date_spine = get_date_spine()
    total_trading_days = len(date_spine)
    logger.info("Trading date spine: %d days", total_trading_days)

    panels: list[pd.DataFrame] = []
    rejected: list[dict] = []
    errors: list[dict] = []

    t0 = time.time()

    for symbol in tqdm(symbols, desc="Building panel", unit="sym"):
        try:
            # Load raw adjusted data
            df_raw = _repo.fetch_adjusted_data(symbol)

            if df_raw.empty:
                rejected.append({"symbol": symbol, "reason": "no_data"})
                continue

            # Quality gate
            ca_actions = get_corporate_actions(symbol)
            passed, reason = quality_gate_full(
                symbol, df_raw, total_trading_days, ca_actions
            )

            if not passed:
                rejected.append({"symbol": symbol, "reason": reason})
                continue

            if dry_run:
                panels.append(None)  # just count passes
                continue

            # Engine + feature engineering
            df_feat = process_symbol(symbol, df_raw=df_raw)
            panels.append(df_feat)

        except Exception as e:
            errors.append({"symbol": symbol, "error": str(e)})
            logger.error("Error processing %s: %s", symbol, e)

    elapsed = time.time() - t0

    # Write rejected symbols
    if rejected:
        pd.DataFrame(rejected).to_csv(REJECTED_PATH, index=False)
        logger.info("Rejected %d symbols → %s", len(rejected), REJECTED_PATH)

    if errors:
        errors_path = DATA_DIR / "panel_errors.csv"
        pd.DataFrame(errors).to_csv(errors_path, index=False)
        logger.warning("%d symbols failed with errors → %s", len(errors), errors_path)

    if dry_run:
        passed_count = len(panels)
        logger.info(
            "DRY RUN complete in %.1fs: %d passed, %d rejected, %d errors",
            elapsed, passed_count, len(rejected), len(errors),
        )
        return

    # Concatenate and write parquet
    valid_panels = [p for p in panels if p is not None]
    if not valid_panels:
        logger.error("No symbols survived quality gate. Aborting.")
        sys.exit(1)

    panel = pd.concat(valid_panels, ignore_index=True)
    panel = panel.sort_values(["symbol", "date"]).reset_index(drop=True)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(PANEL_PATH, index=False, engine="pyarrow")

    logger.info(
        "FULL BUILD complete in %.1fs: %d symbols, %d rows, %d columns → %s",
        elapsed,
        panel["symbol"].nunique(),
        len(panel),
        len(panel.columns),
        PANEL_PATH,
    )
    logger.info(
        "Rejected: %d | Errors: %d | Passed: %d",
        len(rejected), len(errors), panel["symbol"].nunique(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Delta Build
# ──────────────────────────────────────────────────────────────────────────────

def build_delta() -> None:
    """Append new rows to existing panel. Hard-stop on any quality gate failure."""

    if not PANEL_PATH.exists():
        logger.error("No existing panel at %s. Run --mode full first.", PANEL_PATH)
        sys.exit(1)

    logger.info("Starting DELTA panel build")
    existing = pd.read_parquet(PANEL_PATH)

    # Ensure date column is datetime
    if not pd.api.types.is_datetime64_any_dtype(existing["date"]):
        existing["date"] = pd.to_datetime(existing["date"])

    last_date = existing["date"].max()
    symbols = sorted(existing["symbol"].unique())
    logger.info(
        "Existing panel: %d symbols, last date: %s",
        len(symbols), last_date.date(),
    )

    # Compute start_date for lookback window
    start_date = (last_date - timedelta(days=DELTA_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    # Phase 1: Quality check ALL symbols before modifying anything.
    # Also stores the fetched DataFrames so Phase 2 can reuse them directly —
    # guaranteeing that the data quality-checked and the data processed are
    # identical (no second independent fetch).
    logger.info("Phase 1: Quality gate check on all %d symbols...", len(symbols))
    gate_failures: list[dict] = []
    phase1_data: dict[str, pd.DataFrame] = {}  # symbol → quality-checked df_raw

    for symbol in tqdm(symbols, desc="Quality check", unit="sym"):
        try:
            df_raw = _repo.fetch_adjusted_data(symbol, start_date=start_date)
            if df_raw.empty:
                continue

            ca_actions = get_corporate_actions(symbol)
            passed, reason = quality_gate_delta(symbol, df_raw, ca_actions)

            if not passed:
                gate_failures.append({"symbol": symbol, "reason": reason})
                logger.error(
                    "QUALITY GATE FAILURE: %s — %s", symbol, reason
                )
            else:
                phase1_data[symbol] = df_raw  # store only on pass
        except Exception as e:
            gate_failures.append({"symbol": symbol, "reason": f"error: {e}"})
            logger.error("Error checking %s: %s", symbol, e)

    # Hard stop if any failures
    if gate_failures:
        pd.DataFrame(gate_failures).to_csv(DELTA_WARNINGS_PATH, index=False)
        logger.error(
            "ABORTING DELTA: %d symbol(s) failed quality gate. "
            "Parquet NOT modified. Details in %s",
            len(gate_failures), DELTA_WARNINGS_PATH,
        )
        for f in gate_failures:
            logger.error("  → %s: %s", f["symbol"], f["reason"])
        sys.exit(1)

    logger.info("Phase 1 complete: all symbols passed quality gate.")

    # Phase 2: Process symbols using the data already fetched in Phase 1.
    # Passing df_raw directly avoids a second independent fetch and ensures
    # the quality-checked data is exactly what the engine processes.
    logger.info("Phase 2: Computing features for new rows...")
    new_rows: list[pd.DataFrame] = []
    errors: list[dict] = []

    for symbol in tqdm(symbols, desc="Computing delta", unit="sym"):
        df_raw = phase1_data.get(symbol)
        if df_raw is None:
            continue  # symbol had empty data in Phase 1 — skip silently
        try:
            df_feat = process_symbol(symbol, df_raw=df_raw)
            delta = df_feat[df_feat["date"] > last_date]
            if not delta.empty:
                new_rows.append(delta)
        except Exception as e:
            errors.append({"symbol": symbol, "error": str(e)})
            logger.error("Error processing %s: %s", symbol, e)

    if errors:
        # Errors during computation are also a hard stop
        errors_path = DATA_DIR / "delta_errors.csv"
        pd.DataFrame(errors).to_csv(errors_path, index=False)
        logger.error(
            "ABORTING DELTA: %d symbol(s) failed during computation. "
            "Parquet NOT modified. Details in %s",
            len(errors), errors_path,
        )
        sys.exit(1)

    if not new_rows:
        logger.info("No new rows to append. Panel is up to date.")
        return

    # Phase 3: Append to parquet
    delta_df = pd.concat(new_rows, ignore_index=True)
    panel = pd.concat([existing, delta_df], ignore_index=True)
    panel = panel.sort_values(["symbol", "date"]).reset_index(drop=True)
    panel.to_parquet(PANEL_PATH, index=False, engine="pyarrow")

    new_date = panel["date"].max()
    logger.info(
        "DELTA complete: +%d rows (%d symbols with new data). "
        "Panel now %d rows, last date: %s",
        len(delta_df),
        delta_df["symbol"].nunique(),
        len(panel),
        new_date.date(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Panel Builder — Step 1 of the XGBoost pipeline",
    )
    parser.add_argument(
        "--mode",
        choices=["full", "delta"],
        required=True,
        help="full = build from scratch; delta = append new rows",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N symbols (for testing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run quality gate only, do not compute features or write parquet",
    )
    args = parser.parse_args()

    if args.mode == "full":
        build_full(limit=args.limit, dry_run=args.dry_run)
    elif args.mode == "delta":
        if args.dry_run:
            logger.error("--dry-run is not supported in delta mode")
            sys.exit(1)
        if args.limit:
            logger.error("--limit is not supported in delta mode")
            sys.exit(1)
        build_delta()


if __name__ == "__main__":
    main()
