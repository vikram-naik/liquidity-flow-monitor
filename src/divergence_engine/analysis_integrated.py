"""
Module 9 — Integrated State Matrix.

Applies the 4-pillar classification (trajectory, value zone, MFM, coherence)
to each bar in the DVL ledger. Rules are loaded from YAML config and can be
user-tuned via the API.

Uses the RuleEngine (Specification pattern) for classification instead of
hard-coded if/else logic.
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from src.divergence_engine.state_types import (
    MarketContext,
    Trajectory,
    ValueZone,
)
from src.divergence_engine.rule_engine import RuleEngine
from src.divergence_engine import config_manager


def _calc_angle(series: pd.Series, window: int = 5) -> pd.Series:
    """Calculate the rolling linear regression slope (velocity) of a series."""
    def _slope(arr):
        x = np.arange(len(arr))
        return np.polyfit(x, arr, 1)[0]
    return series.rolling(window, min_periods=window).apply(_slope, raw=True).fillna(0.0)


def apply_integrated_matrix(df: pd.DataFrame, db_conn=None) -> pd.DataFrame:
    """Apply the 4-pillar Integrated State Matrix to the DVL ledger.

    Parameters
    ----------
    df : pd.DataFrame
        The DVL ledger with all prior calculations computed.
    db_conn : sqlite3.Connection, optional
        If provided, loads user-tuned thresholds from the database.
    """
    # Load config (factory defaults + any user overrides)
    config = config_manager.get_config(db_conn)
    thresholds = config["thresholds"]

    # Build the rule engine from config
    engine = RuleEngine(config)

    # --- 1. Trajectory Angles ---
    angle_window = int(thresholds.get("angle_window", 5))
    df['price_slope_angle'] = _calc_angle(df['price_slope_z'], angle_window).round(4)
    df['rdv_slope_angle'] = _calc_angle(df['rdv_slope_z'], angle_window).round(4)

    # --- 2. Value Zones ---
    chop_band = float(thresholds.get("cwvap_chop_band", 1.5))
    premium_boundary = float(thresholds.get("premium_boundary", 3.0))
    discount_boundary = float(thresholds.get("discount_boundary", -3.0))

    cwvap_dist = (df['close'] / df['cwvap'] - 1) * 100
    cpoc_dist = (df['close'] / df['cpoc'] - 1) * 100
    df['cwvap_dist'] = cwvap_dist
    df['cpoc_dist'] = cpoc_dist

    def _classify_zone(row):
        cw = row['cwvap_dist']
        cp = row['cpoc_dist']
        if pd.isna(cp):
            cp = cw
        zone = ValueZone.classify(cw, cp, chop_band, premium_boundary, discount_boundary)
        return zone.label

    df['value_zone'] = df.apply(_classify_zone, axis=1)

    # --- 3. Coherence Stamps ---
    coh_strong = float(thresholds.get("coherence_strong", 0.6))
    coh_weak = float(thresholds.get("coherence_weak", 0.3))

    def _coherence_stamp(c):
        if c > coh_strong:
            return " [Strong]"
        if c < coh_weak:
            return " [Weak]"
        return ""

    df['coherence_stamp'] = df['coherence'].apply(_coherence_stamp)

    # --- 4. Rule Engine Classification ---
    # Build a ValueZone reverse lookup from label → enum
    _zone_lookup = {z.label: z for z in ValueZone}

    states = []
    for _, r in df.iterrows():
        ctx = MarketContext(
            trajectory=Trajectory.from_angles(
                r['price_slope_angle'], r['rdv_slope_angle']
            ),
            value_zone=_zone_lookup.get(r['value_zone'], ValueZone.FAIR_MIXED),
            price_z=r['price_slope_z'],
            rdv_z=r['rdv_slope_z'],
            mfm=r.get('mfm', 0),
            coherence=r.get('coherence', 0),
        )
        state = engine.classify(ctx)
        states.append(state.label + r['coherence_stamp'])

    df['integrated_state'] = states
    return df
