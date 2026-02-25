"""
Ignition Marker — Markup Initiation / Expansion.

Isolates a single day of explosive, structurally significant markup initiated
near value.  Represented as a **Purple Up-Arrow** above the candle.

Trigger criteria (all must be true):
  - Strong up-candle (Close > Open)
  - Body expansion ≥ 0.8 × ATR₅₀
  - Move originated near DAVWAP (within 1.0 × ATR₅₀)

Score pillars (0-100):
  - Expansion Geometry (30 pts)
  - Volume Conviction  (30 pts)
  - Value Origin Proximity (20 pts)
  - Ledger Acceleration (20 pts)
  - Structural Baseline (10 pts bonus)

Score ≥ 50 triggers the marker.
"""

import pandas as pd
from src.analysis.markers import MarkerInterface


class IgnitionMarker(MarkerInterface):
    """Markup Initiation / Expansion marker."""

    _order = 20

    def name(self) -> str:
        return 'ignition'

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        is_up_candle = df['price_close'] > df['price_open']
        origin_price = df[['price_open', 'prev_close']].min(axis=1)
        dist_origin_davwap = (origin_price - df['davwap']) / df['atr_50']

        ignition_expansion = df['price_close'] - origin_price

        # Hard triggers
        ignition_hard_trigger = (
            is_up_candle &
            (ignition_expansion >= (0.8 * df['atr_50'])) &
            (dist_origin_davwap.abs() <= 1.0)
        )

        # Pillar 1: Expansion Geometry (30 pts)
        body_expansion_atr = ignition_expansion / df['atr_50']
        score_geom = 30 * ((body_expansion_atr - 0.8).clip(0, 1.2) / 1.2)

        # Pillar 2: Volume Conviction (30 pts)
        vol_ratio = df['delivery_qty'] / df['deliv_sma_10']
        score_vol = 30 * ((vol_ratio - 1.0).clip(0, 1.5) / 1.5)

        # Pillar 3: Value Origin Proximity (20 pts)
        score_prox = 20 * (1.0 - dist_origin_davwap.abs()).clip(0, 1)

        # Pillar 4: Ledger Acceleration (20 pts)
        score_ledger = 20 * (
            (df['dvl_slope_5'] / df['deliv_sma_10']) - 0.2
        ).clip(0, 0.5) / 0.5

        # Pillar 5: Structural Baseline (10 pts)
        score_base = 10

        df['ignition_score'] = (
            score_geom + score_vol + score_prox + score_ledger + score_base
        ).round(0)
        df['is_ignition'] = ignition_hard_trigger & (df['ignition_score'] >= 50)
        df['is_ignition'] = df['is_ignition'].fillna(False)

        return df

    def debug_info(self, df: pd.DataFrame, row_idx: int) -> list[dict]:
        r = df.iloc[row_idx]
        atr = r['atr_50']
        is_up = r['price_close'] > r['price_open']
        origin = min(r['price_open'], r.get('prev_close', r['price_open']))
        expansion = r['price_close'] - origin
        dist_origin = abs(origin - r['davwap']) / atr if atr else 0
        deliv = r['delivery_qty']
        deliv_sma = r['deliv_sma_10'] if r['deliv_sma_10'] > 0 else 1
        dvl_slope = r.get('dvl_slope_5', 0)

        req_exp = 0.8 * atr
        exp_atr = expansion / atr if atr else 0
        vol_ratio = deliv / deliv_sma

        # Scoring (mirrors evaluate)
        s_geom = round(30 * max(0, min(1.2, exp_atr - 0.8)) / 1.2, 1)
        s_vol  = round(30 * max(0, min(1.5, vol_ratio - 1.0)) / 1.5, 1)
        s_prox = round(20 * max(0, min(1, 1.0 - abs(dist_origin))), 1)
        ledger_norm = (dvl_slope / deliv_sma - 0.2) if deliv_sma else 0
        s_ledger = round(20 * max(0, min(0.5, ledger_norm)) / 0.5, 1)
        s_base = 10
        total = s_geom + s_vol + s_prox + s_ledger + s_base

        return [
            {'label': 'Up Candle',           'value': str(is_up),        'threshold': 'True',            'passed': is_up,                    'detail': f'O={r["price_open"]:.1f} C={r["price_close"]:.1f}'},
            {'label': 'Expansion ≥ 0.8×ATR', 'value': f'{expansion:.1f}','threshold': f'≥ {req_exp:.1f}','passed': expansion >= req_exp,     'detail': f'origin={origin:.1f}'},
            {'label': 'Origin Prox ≤ 1 ATR', 'value': f'{dist_origin:.2f}','threshold':'≤ 1.0',         'passed': abs(dist_origin) <= 1.0,  'detail': f'DAVWAP={r["davwap"]:.1f}'},
            {'label': 'Expansion (30)',      'value': f'{s_geom}',       'threshold': '',                'passed': True,                     'detail': f'exp/ATR={exp_atr:.2f}'},
            {'label': 'Volume   (30)',       'value': f'{s_vol}',        'threshold': '',                'passed': True,                     'detail': f'vol_ratio={vol_ratio:.2f}'},
            {'label': 'Proximity(20)',       'value': f'{s_prox}',       'threshold': '',                'passed': True,                     'detail': ''},
            {'label': 'Ledger   (20)',       'value': f'{s_ledger}',     'threshold': '',                'passed': True,                     'detail': f'slope={dvl_slope:.1E}'},
            {'label': 'Baseline (10)',       'value': f'{s_base}',       'threshold': '',                'passed': True,                     'detail': 'fixed'},
            {'label': 'Total Score',         'value': f'{total:.0f}',    'threshold': '≥ 50',            'passed': total >= 50,              'detail': f'is_ignition={r.get("is_ignition", False)}'},
        ]

    def screen(self, df: pd.DataFrame, latest: pd.Series, prev=None) -> bool:
        return bool(latest.get('is_ignition', False))

    def metadata(self) -> dict:
        return {
            'id': 'ignition',
            'label': 'Ignition',
            'is_chart_marker': True,
            'color': '#b197fc',
            'shape': 'arrowUp',
            'position': 'aboveBar',
            'score_key': 'ignition_score',
            'flag_key': 'is_ignition',
            'screener_name': 'SCR: Ignition',
            'text_format': 'score',
            'legend_dot_style': (
                'background:#b197fc; '
                'clip-path: polygon(50% 0%, 0% 100%, 100% 100%); '
                'border-radius: 0;'
            ),
            'help_title': 'Ignition Marker (Breakout)',
            'help_html': (
                '<div class="guide-section">'
                '<h4>What it is</h4>'
                '<p>The Ignition marker isolates a single day of explosive, '
                'structurally significant markup initiated near value. It is '
                'represented by a <strong>Purple Up-Arrow</strong> over the candle.</p>'
                '</div>'
                '<div class="guide-section">'
                '<h4>Triggers &amp; Scoring (0-100)</h4>'
                '<ul>'
                '<li><strong>Expansion (30 pts):</strong> The candle\'s net expansion '
                'must inherently be huge (<code>&gt;= 0.8 * Average True Range</code>).</li>'
                '<li><strong>Volume (30 pts):</strong> Delivery volume must significantly '
                'exceed the 10-day moving average.</li>'
                '<li><strong>Proximity (20 pts):</strong> The move must have '
                '<strong>originated</strong> strictly within <code>1.0 ATR</code> '
                'of the DAVWAP.</li>'
                '<li><strong>Ledger (20 pts):</strong> The 5-day Momentum Ledger slope '
                'must be strongly accelerating.</li>'
                '</ul>'
                '<p>A score &gt;= 50 triggers the marker.</p>'
                '</div>'
            ),
        }

    def agg_rules(self) -> dict:
        return {'is_ignition': 'any', 'ignition_score': 'last'}
