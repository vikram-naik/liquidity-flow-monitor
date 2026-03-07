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

    # --- Score weights ---
    w_cwc = float(thresholds.get("w_cwc", 0.25))
    w_rdv = float(thresholds.get("w_rdv", 0.20))
    w_rdv_con = float(thresholds.get("w_rdv_consistency", 0.15))
    w_depth = float(thresholds.get("w_cwvap_depth", 0.15))
    w_del = float(thresholds.get("w_delivery_pct", 0.10))
    w_pdd = float(thresholds.get("w_pdd", 0.15))

    # --- Classification + Conviction Scoring ---
    states = []
    scores = []

    for _, r in df.iterrows():
        cwc_val = float(r.get('cwc', 1.0))
        rdv_val = float(r.get('rdv', 0.0))
        rdv_con = int(r.get('rdv_consistency', 0))
        cwvap_d = float(r.get('cwvap_dist', 0.0))
        del_pct = float(r.get('delivery_pct', 0.0))
        pdd_val = float(r.get('pdd_30', 0.0))
        coh_val = float(r.get('coherence', 0.0))

        ctx = MarketContext(
            cwvap_dist=cwvap_d,
            cwc=cwc_val,
            rdv=rdv_val,
            rdv_consistency=rdv_con,
            delivery_pct=del_pct,
            pdd_30=pdd_val,
            coherence=coh_val,
        )
        state = engine.classify(ctx)
        states.append(state.label)

        if state is not StateName.NO_SIGNAL:
            cwc_score = max(0.0, 1.0 - cwc_val)
            rdv_score = min(rdv_val / 2.0, 1.0)
            rdv_con_score = rdv_con / 5.0
            depth_score = min(abs(cwvap_d) / 5.0, 1.0)
            del_score = min(del_pct / 80.0, 1.0)
            pdd_score = min(abs(pdd_val) / 20.0, 1.0)

            score = (
                w_cwc * cwc_score
                + w_rdv * rdv_score
                + w_rdv_con * rdv_con_score
                + w_depth * depth_score
                + w_del * del_score
                + w_pdd * pdd_score
            ) * 100
            scores.append(round(score, 1))
        else:
            scores.append(None)

    df['integrated_state'] = states
    df['conviction_score'] = scores
    return df


def compute_verification(
    df: pd.DataFrame,
    horizon: int = 5,
    atr_mult: float = 2.0,
) -> dict:
    """Verify Demand/Supply signals against forward price action.

    Parameters
    ----------
    df : pd.DataFrame
        Full ledger with integrated_state, conviction_score, close, high, low, atr_20.
    horizon : int
        Number of trading days to look forward.
    atr_mult : float
        ATR multiplier for the target barrier.

    Returns
    -------
    dict with 'signals' (list of dicts) and 'stats' (summary dict).
    """
    signals = []

    for i in range(len(df)):
        row = df.iloc[i]
        state = row.get('integrated_state', 'No Signal')
        if state not in ('Demand', 'Supply'):
            continue

        entry_close = float(row['close'])
        atr = float(row.get('atr_20', 0))
        if atr <= 0:
            continue

        barrier = atr_mult * atr
        conv_score = row.get('conviction_score')
        signal_date = str(row['date'])
        if hasattr(row['date'], 'strftime'):
            signal_date = row['date'].strftime('%Y-%m-%d')

        # Look forward
        end_idx = min(i + horizon, len(df) - 1)
        future = df.iloc[i + 1: end_idx + 1]

        result = 'pending'
        if len(future) > 0:
            if state == 'Demand':
                target = entry_close + barrier
                if future['high'].max() >= target:
                    result = 'hit'
                elif len(future) >= horizon:
                    result = 'miss'
            else:  # Supply
                target = entry_close - barrier
                if future['low'].min() <= target:
                    result = 'hit'
                elif len(future) >= horizon:
                    result = 'miss'
        else:
            target = entry_close + barrier if state == 'Demand' else entry_close - barrier

        signals.append({
            'date': signal_date,
            'state': state,
            'conviction_score': conv_score,
            'entry_close': round(entry_close, 2),
            'barrier': round(barrier, 2),
            'target': round(target, 2),
            'result': result,
        })

    total = len(signals)
    hits = sum(1 for s in signals if s['result'] == 'hit')
    misses = sum(1 for s in signals if s['result'] == 'miss')
    pending = sum(1 for s in signals if s['result'] == 'pending')
    hit_rate = round(hits / (hits + misses) * 100, 1) if (hits + misses) > 0 else 0.0

    return {
        'signals': signals,
        'stats': {
            'total': total,
            'hits': hits,
            'misses': misses,
            'pending': pending,
            'hit_rate_pct': hit_rate,
        },
    }
