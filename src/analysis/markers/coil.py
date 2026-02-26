"""
Coil Marker — Volatility Compression & Accumulation.

Identifies extreme volatility compression near DAVWAP value, often preceding
an explosive move.  Represented as a **Blue Circle** beneath the candle.

Trigger criteria (all must be true):
  - Total range < 0.8 × ATR₅₀
  - Body size  < 0.4 × ATR₅₀
  - Distance from DAVWAP ≤ 1.0 × ATR₅₀

Score pillars (0-100):
  - Geometry  (30 pts): rewards extreme compression vs normal ATR
  - Dryness   (30 pts): rewards extreme volume exhaustion
  - Ledger    (20 pts): flow must be non-negative
  - Proximity (20 pts): rewards closeness to DAVWAP in ATR units

Score ≥ 50 triggers the marker.
"""

import pandas as pd
from src.analysis.markers import MarkerInterface


class CoilMarker(MarkerInterface):
    """Volatility Compression & Accumulation marker."""

    _order = 10  # evaluation order

    def name(self) -> str:
        return 'coil'

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        range_total = df['price_high'] - df['price_low']
        body_top = df[['price_open', 'price_close']].max(axis=1)
        body_bot = df[['price_open', 'price_close']].min(axis=1)
        body_size = body_top - body_bot

        dist_raw_davwap = df['price_close'] - df['davwap']
        dist_atr_davwap = dist_raw_davwap.abs() / df['atr_50']

        # Hard triggers
        coil_hard_trigger = (
            (range_total < (0.8 * df['atr_50'])) &
            (body_size < (0.4 * df['atr_50'])) &
            (dist_atr_davwap <= 1.0)
        )

        # Pillar 1: Geometry (30 pts)
        score_geom = 30 * (1.0 - (range_total / df['atr_50'])).clip(0, 1)

        # Pillar 2: Dryness (30 pts)
        score_dry = 30 * (1.0 - (df['delivery_qty'] / df['deliv_sma_10'])).clip(0, 1)

        # Pillar 3: Ledger Divergence (20 pts)
        score_ledger = pd.Series(0.0, index=df.index, dtype=float)
        score_ledger[df['dvl_slope_5'] > 0] = 20
        score_ledger[
            (df['dvl_slope_5'] <= 0) &
            (df['dvl_slope_5'] > -(df['deliv_sma_10'] * 0.1))
        ] = 10

        # Pillar 4: Value Proximity (20 pts)
        score_prox = 10 + 10 * (1.0 - dist_atr_davwap).clip(0, 1)

        df['coil_score'] = (score_geom + score_dry + score_ledger + score_prox).round(0)
        df['is_coil'] = coil_hard_trigger & (df['coil_score'] >= 50)
        df['is_coil'] = df['is_coil'].fillna(False)

        return df

    def debug_info(self, df: pd.DataFrame, row_idx: int) -> list[dict]:
        r = df.iloc[row_idx]
        atr = r['atr_50']
        rng = r['price_high'] - r['price_low']
        body = abs(r['price_open'] - r['price_close'])
        dist_atr = abs(r['price_close'] - r['davwap']) / atr if atr else 0
        dvl_slope = r.get('dvl_slope_5', 0)
        deliv = r['delivery_qty']
        deliv_sma = r['deliv_sma_10'] if r['deliv_sma_10'] > 0 else 1

        req_rng = 0.8 * atr
        req_bdy = 0.4 * atr

        # Scoring (mirrors evaluate)
        s_geom = round(30 * max(0, min(1, 1.0 - rng / atr)), 1)
        s_dry  = round(30 * max(0, min(1, 1.0 - deliv / deliv_sma)), 1)
        s_ledger = 20 if dvl_slope > 0 else (10 if dvl_slope > -(deliv_sma * 0.1) else 0)
        s_prox = round(10 + 10 * max(0, min(1, 1.0 - dist_atr)), 1)
        total = s_geom + s_dry + s_ledger + s_prox

        return [
            {'label': 'Range < 0.8×ATR',  'value': f'{rng:.1f}', 'threshold': f'< {req_rng:.1f}', 'passed': rng < req_rng,  'detail': ''},
            {'label': 'Body < 0.4×ATR',   'value': f'{body:.1f}','threshold': f'< {req_bdy:.1f}', 'passed': body < req_bdy, 'detail': ''},
            {'label': 'Prox DAVWAP ≤ 1 ATR','value':f'{dist_atr:.2f}','threshold':'≤ 1.0',       'passed': dist_atr <= 1.0, 'detail': f'DAVWAP={r["davwap"]:.1f}'},
            {'label': 'Geometry (30)',     'value': f'{s_geom}',  'threshold': '',                 'passed': True,            'detail': 'pillar'},
            {'label': 'Dryness  (30)',     'value': f'{s_dry}',   'threshold': '',                 'passed': True,            'detail': f'deliv/sma={deliv/deliv_sma:.2f}'},
            {'label': 'Ledger   (20)',     'value': f'{s_ledger}','threshold': '',                 'passed': True,            'detail': f'slope={dvl_slope:.1E}'},
            {'label': 'Proximity(20)',     'value': f'{s_prox}',  'threshold': '',                 'passed': True,            'detail': ''},
            {'label': 'Total Score',       'value': f'{total:.0f}','threshold': '≥ 50',            'passed': total >= 50,     'detail': f'is_coil={r.get("is_coil", False)}'},
        ]

    def screen(self, df: pd.DataFrame, latest: pd.Series, prev=None) -> bool:
        return bool(latest.get('is_coil', False))

    def metadata(self) -> dict:
        return {
            'id': 'coil',
            'label': 'Coil',
            'is_chart_marker': True,
            'marker_type': 'bullish',
            'color': '#4dabf7',
            'shape': 'circle',
            'position': 'belowBar',
            'score_key': 'coil_score',
            'flag_key': 'is_coil',
            'screener_name': 'SCR: Coil',
            'text_format': 'score',  # display score value on daily
            'legend_dot_style': 'background:#4dabf7; border-radius: 50%;',
            'help_title': 'Coil Marker (Compression)',
            'help_html': (
                '<div class="guide-section">'
                '<h4>What it is</h4>'
                '<p>The Coil marker highlights extreme volatility compression '
                'near value, often preceding an explosive move. It is '
                'represented by a <strong>Blue Circle</strong> beneath the candle.</p>'
                '</div>'
                '<div class="guide-section">'
                '<h4>Triggers &amp; Scoring (0-100)</h4>'
                '<ul>'
                '<li><strong>Geometry (30 pts):</strong> The candle\'s Total Range and Body size '
                'must be drastically smaller than the 50-day ATR '
                '(<code>Range &lt; 0.8 ATR</code> and <code>Body &lt; 0.4 ATR</code>).</li>'
                '<li><strong>Dryness (30 pts):</strong> Institutional volume is drying up '
                '(well below average), indicating supply exhaustion.</li>'
                '<li><strong>Proximity (20 pts):</strong> The candle is occurring very close '
                'to the DAVWAP (within <code>1.0 ATR</code>).</li>'
                '<li><strong>Ledger (20 pts):</strong> Despite the dryness, the underlying '
                'Momentum Ledger remains positive or stable.</li>'
                '</ul>'
                '<p>A score &gt;= 50 triggers the marker.</p>'
                '</div>'
            ),
        }

    def agg_rules(self) -> dict:
        return {'is_coil': 'any', 'coil_score': 'last'}
