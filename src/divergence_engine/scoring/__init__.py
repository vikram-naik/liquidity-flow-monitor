"""
Unified Weighted Scoring Model — replaces binary gates + conviction scoring.

Entry point: ``compute_signal_strength(df, config)``

Each bar gets:
- Dual-direction scoring: both Demand and Supply are scored, winner decides
- A signal_strength (0-100) from weighted scoring of all factors
- A scoring_details list for UI rendering
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
import yaml
from pathlib import Path

from src.divergence_engine.scoring.functions import get_scoring_fn
from src.divergence_engine.state_types import StateName

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_SCORING_YAML = _CONFIG_DIR / "default_rules.yaml"

# Direction-regime context pairs
_COUNTER_TREND = {("Demand", "downtrend"), ("Supply", "uptrend")}
_TREND_ALIGNED = {("Demand", "uptrend"), ("Supply", "downtrend")}


def _load_scoring_config() -> dict:
    """Load the v2 scoring YAML config."""
    with open(_SCORING_YAML) as f:
        return yaml.safe_load(f)


def _regime_context_score(
    regime: str, direction: str, scores_cfg: dict
) -> float:
    """Compute regime score based on direction-regime context.

    Counter-trend signals (Supply in uptrend, Demand in downtrend) get the
    highest score because the best signals are counter-trend.
    """
    regime = str(regime).lower() if regime else ""

    if (direction, regime) in _COUNTER_TREND:
        return scores_cfg.get("counter_trend", 1.0)
    if (direction, regime) in _TREND_ALIGNED:
        return scores_cfg.get("trend_aligned", 0.3)
    if regime == "transition":
        return scores_cfg.get("transition", 0.5)
    return scores_cfg.get("notrend", 0.15)


def _score_bar(
    row: dict,
    direction: str,
    factors: dict,
) -> tuple[float, list[dict]]:
    """Score a single bar against all factors.

    Returns (signal_strength 0-100, scoring_details list).
    """
    details = []
    weighted_sum = 0.0
    total_weight = 0.0
    computed_scores = {}  # factor_name -> score (for requires lookup)

    for factor_name, factor_cfg in factors.items():
        column = factor_cfg["column"]
        scoring_fn_name = factor_cfg["scoring"]
        normalize_params = factor_cfg.get("normalize", {})
        weights = factor_cfg.get("weight", {})
        ui_cfg = factor_cfg.get("ui", {})

        # Get the weight for this direction
        weight = weights.get(direction.lower(), 0.0)
        if weight == 0:
            continue

        # Read value from row
        raw_value = row.get(column, 0.0)
        if raw_value is None or (isinstance(raw_value, float) and math.isnan(raw_value)):
            raw_value = 0.0

        # Apply scoring function
        scoring_fn = get_scoring_fn(scoring_fn_name)
        score = scoring_fn(float(raw_value), normalize_params, direction)

        # Cap by prerequisite factor's score (relative scaling)
        requires = factor_cfg.get("requires")
        if requires:
            req_factor = requires.get("factor")
            req_any = requires.get("any")
            if req_any:
                # requires.any: [f1, f2] — cap by max of listed parents
                req_score = max(
                    computed_scores.get(f, 0.0) for f in req_any
                )
            elif req_factor:
                req_score = computed_scores.get(req_factor, 0.0)
            else:
                req_score = 1.0
            score = min(score, req_score)

        computed_scores[factor_name] = score

        weighted = score * weight
        weighted_sum += weighted
        total_weight += weight

        details.append({
            "factor": factor_name,
            "score": round(score, 4),
            "weight": weight,
            "weighted": round(weighted, 4),
            "raw_value": round(float(raw_value), 4),
            "ui": {
                "label": ui_cfg.get("label", factor_name),
                "description": ui_cfg.get("description", ""),
                "format": ui_cfg.get("format", ".2f"),
            },
        })

    if total_weight > 0:
        signal_strength = (weighted_sum / total_weight) * 100
    else:
        signal_strength = 0.0

    return round(signal_strength, 1), details


def compute_signal_strength(
    df: pd.DataFrame,
    config: dict | None = None,
) -> pd.DataFrame:
    """Apply unified weighted scoring to every bar in the DataFrame.

    Scores each bar for BOTH Demand and Supply directions. The stronger
    score determines direction — no separate direction classifier needed.

    Parameters
    ----------
    df : pd.DataFrame
        The DVL ledger with all prior calculations computed.
        Must have columns: close, cwvap, atr_20, plus all factor columns.
    config : dict, optional
        Scoring config (v2 YAML). If None, loads from default_rules.yaml.

    Returns
    -------
    pd.DataFrame
        Input DataFrame with added columns:
        - cwvap_dist: % distance from CWVAP
        - rdv_consistency: rolling count of RDV >= 1.0 in last 5 days
        - integrated_state: "Demand" / "Supply" / "No Signal"
        - signal_strength: 0-100 unified score
        - scoring_details: list of per-factor breakdown dicts
    """
    if config is None:
        config = _load_scoring_config()

    settings = config.get("settings", {})
    min_strength = settings.get("min_signal_strength", 40)
    factors = config.get("factors", {})
    regime_scores_cfg = settings.get("regime", {}).get("scores", {
        "counter_trend": 1.0, "trend_aligned": 0.3,
        "transition": 0.5, "notrend": 0.15,
    })

    # --- Derived columns ---
    df["cwvap_dist"] = ((df["close"] / df["cwvap"] - 1) * 100).round(4)
    df["rdv_consistency"] = (
        df["rdv"].ge(1.0).astype(int).rolling(5, min_periods=1).sum().astype(int)
    )

    # --- Score each bar for both directions ---
    states = []
    strengths = []
    details_list = []
    scoring_dirs = []
    demand_strengths = []
    supply_strengths = []
    demand_details_list = []
    supply_details_list = []

    for _, r in df.iterrows():
        row_dict = r.to_dict()

        close_val = float(row_dict.get("close", 0.0))
        cwvap_val = float(row_dict.get("cwvap", 0.0))
        regime_val = row_dict.get("regime")

        # Skip bars with bad data
        if close_val == 0 or cwvap_val == 0:
            states.append(StateName.NO_SIGNAL.label)
            strengths.append(None)
            details_list.append(None)
            scoring_dirs.append(None)
            demand_strengths.append(None)
            supply_strengths.append(None)
            demand_details_list.append(None)
            supply_details_list.append(None)
            continue

        # Compute regime context score for each direction and inject into row
        demand_regime_score = _regime_context_score(regime_val, "Demand", regime_scores_cfg)
        supply_regime_score = _regime_context_score(regime_val, "Supply", regime_scores_cfg)

        # Score Demand
        row_dict["regime_score"] = demand_regime_score
        demand_strength, demand_details = _score_bar(row_dict, "Demand", factors)

        # Score Supply
        row_dict["regime_score"] = supply_regime_score
        supply_strength, supply_details = _score_bar(row_dict, "Supply", factors)

        # Winner determines direction
        if demand_strength >= supply_strength:
            winner_dir = "Demand"
            winner_strength = demand_strength
            winner_details = demand_details
        else:
            winner_dir = "Supply"
            winner_strength = supply_strength
            winner_details = supply_details

        if winner_strength >= min_strength:
            states.append(winner_dir)
        else:
            states.append(StateName.NO_SIGNAL.label)

        strengths.append(winner_strength)
        details_list.append(winner_details)
        scoring_dirs.append(winner_dir)
        demand_strengths.append(demand_strength)
        supply_strengths.append(supply_strength)
        demand_details_list.append(demand_details)
        supply_details_list.append(supply_details)

    df["integrated_state"] = states
    df["signal_strength"] = strengths
    df["scoring_details"] = details_list
    df["scoring_direction"] = scoring_dirs
    df["demand_strength"] = demand_strengths
    df["supply_strength"] = supply_strengths
    df["demand_details"] = demand_details_list
    df["supply_details"] = supply_details_list

    return df
