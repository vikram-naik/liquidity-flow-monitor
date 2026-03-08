"""
Module 9 — Conviction-Gated Demand/Supply Markers.

Applies conviction gates (CWC, RDV, RDV consistency) and CWVAP direction
to classify each bar as Demand, Supply, or No Signal. Computes a conviction
score (0-100) for signal bars.
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from src.divergence_engine.state_types import MarketContext, StateName
from src.divergence_engine.rule_engine import RuleEngine
from src.divergence_engine import config_manager


def apply_integrated_matrix(df: pd.DataFrame, db_conn=None) -> pd.DataFrame:
    """Apply conviction-gated markers to the DVL ledger.

    Parameters
    ----------
    df : pd.DataFrame
        The DVL ledger with all prior calculations computed.
    db_conn : sqlite3.Connection, optional
        If provided, loads user-tuned thresholds from the database.
    """
    if db_conn is None:
        import sqlite3
        from src.database import DB_PATH
        _conn = sqlite3.connect(DB_PATH, timeout=10)
        _conn.row_factory = sqlite3.Row
        try:
            config = config_manager.get_config(_conn)
        finally:
            _conn.close()
    else:
        config = config_manager.get_config(db_conn)
    thresholds = config["thresholds"]

    engine = RuleEngine(config)

    # --- CWVAP distance ---
    df['cwvap_dist'] = ((df['close'] / df['cwvap'] - 1) * 100).round(4)

    # --- RDV consistency: days with RDV >= 1.0 in last 5 ---
    df['rdv_consistency'] = (
        df['rdv'].ge(1.0).astype(int).rolling(5, min_periods=1).sum().astype(int)
    )

    # --- Accumulation score weights (must sum to 1.0) ---
    w_acc_cwc = float(thresholds.get("w_acc_cwc", 0.25))
    w_acc_rdv = float(thresholds.get("w_acc_rdv", 0.20))
    w_acc_rdv_con = float(thresholds.get("w_acc_rdv_consistency", 0.15))
    w_acc_depth = float(thresholds.get("w_acc_cwvap_depth", 0.15))
    w_acc_del = float(thresholds.get("w_acc_delivery_pct", 0.10))
    w_acc_coh = float(thresholds.get("w_acc_coherence", 0.15))

    # --- Divergence score weights (must sum to 1.0) ---
    w_div_psz_d = float(thresholds.get("w_div_psz_delta", 0.40))
    w_div_pdd = float(thresholds.get("w_div_pdd", 0.35))
    w_div_psz_ext = float(thresholds.get("w_div_psz_extreme", 0.25))

    # --- Conviction blend ---
    w_conv_acc = float(thresholds.get("w_conviction_accum", 0.50))
    w_conv_div = float(thresholds.get("w_conviction_diverg", 0.50))

    # --- Classification + Conviction Scoring ---
    states = []
    conv_scores = []
    acc_scores = []
    div_scores = []
    gate_results_list = []

    for _, r in df.iterrows():
        cwc_val = float(r.get('cwc', 1.0))
        rdv_val = float(r.get('rdv', 0.0))
        rdv_con = int(r.get('rdv_consistency', 0))
        cwvap_d = float(r.get('cwvap_dist', 0.0))
        del_pct = float(r.get('delivery_pct', 0.0))
        pdd_val = float(r.get('pdd_30', 0.0))
        coh_val = float(r.get('coherence', 0.0))
        psz_raw = r.get('price_slope_z', 0.0)
        psz_val = float(psz_raw) if pd.notna(psz_raw) else 0.0
        psz_d3_raw = r.get('psz_delta_3d', 0.0)
        psz_d3_val = float(psz_d3_raw) if pd.notna(psz_d3_raw) else 0.0

        ctx = MarketContext(
            cwvap_dist=cwvap_d,
            cwc=cwc_val,
            rdv=rdv_val,
            rdv_consistency=rdv_con,
            delivery_pct=del_pct,
            pdd_30=pdd_val,
            coherence=coh_val,
            price_slope_z=psz_val,
            psz_delta_3d=psz_d3_val,
        )
        state, gate_details = engine.classify_with_gates(ctx)
        states.append(state.label)
        gate_results_list.append(gate_details)

        if state is not StateName.NO_SIGNAL:
            # --- Accumulation score: is smart money building/unwinding? ---
            acc = (
                w_acc_cwc * max(0.0, 1.0 - cwc_val)
                + w_acc_rdv * min(rdv_val / 2.0, 1.0)
                + w_acc_rdv_con * (rdv_con / 5.0)
                + w_acc_depth * min(abs(cwvap_d) / 5.0, 1.0)
                + w_acc_del * min(del_pct / 80.0, 1.0)
                + w_acc_coh * min(coh_val, 1.0)
            ) * 100

            # --- Divergence score: is price disconnected from flow + turning? ---
            psz_delta_score = min(abs(psz_d3_val) / 0.15, 1.0)
            pdd_score = min(abs(pdd_val) / 20.0, 1.0)
            psz_extreme_score = min(abs(psz_val) / 0.5, 1.0)  # how stretched the trend is

            div = (
                w_div_psz_d * psz_delta_score
                + w_div_pdd * pdd_score
                + w_div_psz_ext * psz_extreme_score
            ) * 100

            score = w_conv_acc * acc + w_conv_div * div
            acc_scores.append(round(acc, 1))
            div_scores.append(round(div, 1))
            conv_scores.append(round(score, 1))
        else:
            acc_scores.append(None)
            div_scores.append(None)
            conv_scores.append(None)

    df['integrated_state'] = states
    df['accum_score'] = acc_scores
    df['diverg_score'] = div_scores
    df['conviction_score'] = conv_scores
    df['gate_results'] = gate_results_list
    return df
