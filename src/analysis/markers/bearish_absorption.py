"""
Bearish Absorption Marker (BD) — Silent Distribution.

Identifies when volume is spiking on negative price action (closes near the low) 
while the DVL slope is heavily declining, indicating institutional selling pressure 
that is absorbing any buying attempts. Represented as an **Orange Circle** with "BD" text.

Trigger criteria (all must be true):
  - Spiking Volume: delivery_qty > 1.2 * deliv_sma_10
  - Poor Close: MFM < -0.5 (closed in the lower 25% of the range)
  - Negative Flow: dvl_slope_5 < 0

Score pillars (0-100):
  - Volume Spike (30 pts)
  - Flow Weakness (30 pts)
  - Price Rejection (20 pts): Upper wick relative to true range
  - Close Pain (20 pts): Proximity to the absolute low

Score >= 50 triggers the marker.
"""

import pandas as pd
from src.analysis.markers import MarkerInterface


class BearishAbsorptionMarker(MarkerInterface):
    """Silent Distribution (Absorption) marker."""

    _order = 31

    def name(self) -> str:
        return 'bearish_absorption'

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        vol_ratio = df['delivery_qty'] / df['deliv_sma_10']
        
        # Upper wick measurement handling inside bars (high - max(open, close))
        upper_wick = df['price_high'] - df[['price_open', 'price_close']].max(axis=1)
        true_range = df['atr_50']  # Compare against ATR

        # Hard triggers
        bd_hard_trigger = (
            (vol_ratio > 1.2) &
            (df['mfm'] < -0.5) &
            (df['dvl_slope_5'] < 0)
        )

        # Pillar 1: Volume Spike (30 pts)
        score_vol = 30 * ((vol_ratio - 1.2).clip(0, 1.5) / 1.5)

        # Pillar 2: Flow Weakness (30 pts)
        # We want deeply negative slopes. Normalize by deliv_sma_10.
        flow_norm = -df['dvl_slope_5'] / df['deliv_sma_10']
        score_flow = 30 * (flow_norm.clip(0, 0.5) / 0.5)

        # Pillar 3: Price Rejection (20 pts)
        # A large upper wick indicates sellers rejected higher prices.
        wick_ratio = upper_wick / true_range
        score_rej = 20 * (wick_ratio.clip(0, 0.5) / 0.5)

        # Pillar 4: Close Pain (20 pts)
        # MFM scales from -0.5 to -1.0
        score_pain = 20 * ((-df['mfm'] - 0.5).clip(0, 0.5) / 0.5)

        df['bd_score'] = (score_vol + score_flow + score_rej + score_pain).round(0)
        df['is_bearish_absorption'] = bd_hard_trigger & (df['bd_score'] >= 50)

        # Mutual Exclusion: Suppress if higher-priority markers triggered on this candle
        for higher in ['is_spring', 'is_distribution', 'is_ignition', 'is_coil']:
            if higher in df.columns:
                df['is_bearish_absorption'] = df['is_bearish_absorption'] & ~df[higher].fillna(False)

        df['is_bearish_absorption'] = df['is_bearish_absorption'].fillna(False)

        return df

    def debug_info(self, df: pd.DataFrame, row_idx: int) -> list[dict]:
        r = df.iloc[row_idx]
        atr = r['atr_50']
        deliv = r['delivery_qty']
        deliv_sma = r['deliv_sma_10'] if r['deliv_sma_10'] > 0 else 1
        vol_ratio = deliv / deliv_sma
        mfm = r.get('mfm', 0)
        dvl_slope = r.get('dvl_slope_5', 0)
        upper_wick = r['price_high'] - max(r['price_open'], r['price_close'])
        wick_ratio = upper_wick / atr if atr else 0

        # Scoring
        s_vol = round(30 * max(0, min(1.5, vol_ratio - 1.2)) / 1.5, 1)
        flow_norm = -dvl_slope / deliv_sma if deliv_sma else 0
        s_flow = round(30 * max(0, min(0.5, flow_norm)) / 0.5, 1)
        s_rej = round(20 * max(0, min(0.5, wick_ratio)) / 0.5, 1)
        s_pain = round(20 * max(0, min(0.5, -mfm - 0.5)) / 0.5, 1)
        total = s_vol + s_flow + s_rej + s_pain

        return [
            {'label': 'Vol Ratio > 1.2',    'value': f'{vol_ratio:.2f}','threshold': '> 1.2',  'passed': vol_ratio > 1.2,     'detail': ''},
            {'label': 'MFM < -0.5',         'value': f'{mfm:.2f}',      'threshold': '< -0.5', 'passed': mfm < -0.5,          'detail': ''},
            {'label': 'Flow < 0',           'value': f'{dvl_slope:.1E}', 'threshold': '< 0',    'passed': dvl_slope < 0,       'detail': ''},
            {'label': 'Volume (30)',        'value': f'{s_vol}',        'threshold': '',       'passed': True,                'detail': ''},
            {'label': 'Flow Weakness (30)', 'value': f'{s_flow}',       'threshold': '',       'passed': True,                'detail': ''},
            {'label': 'Price Rejection(20)','value': f'{s_rej}',        'threshold': '',       'passed': True,                'detail': f'wick={upper_wick:.1f}'},
            {'label': 'Close Pain (20)',    'value': f'{s_pain}',       'threshold': '',       'passed': True,                'detail': ''},
            {'label': 'Total Score',        'value': f'{total:.0f}',     'threshold': '>= 50',  'passed': total >= 50,         'detail': f'is_bd={r.get("is_bearish_absorption", False)}'},
        ]

    def screen(self, df: pd.DataFrame, latest: pd.Series, prev=None) -> bool:
        return bool(latest.get('is_bearish_absorption', False))

    def metadata(self) -> dict:
        return {
            'id': 'bearish_absorption',
            'label': 'Bear Absorption (BD)',
            'is_chart_marker': True,
            'marker_type': 'bearish',
            'color': '#ff8c00', # Orange
            'shape': 'circle',
            'position': 'aboveBar',
            'score_key': 'bd_score',
            'flag_key': 'is_bearish_absorption',
            'screener_name': 'SCR: Bear Absorption',
            'text_format': 'fixed:BD',
            'legend_dot_style': 'background:#ff8c00; border-radius: 50%;',
            'help_title': 'Bearish Absorption Marker (BD)',
            'help_html': (
                '<div class="guide-section">'
                '<h4>What it is</h4>'
                '<p>The Bearish Absorption marker (Silent Distribution) identifies when volume is spiking '
                'on negative price action (closing near the low) while institutional flow (DVL) is heavily '
                'declining. It represents sellers absorbing any buying attempts. '
                'It is represented by an <strong>Orange Circle with "BD"</strong> above the candle.</p>'
                '</div>'
                '<div class="guide-section">'
                '<h4>Triggers &amp; Scoring (0-100)</h4>'
                '<ul>'
                '<li><strong>Spiking Volume (30 pts):</strong> Delivery volume must be <code>&gt; 1.2x</code> the 10-day average.</li>'
                '<li><strong>Flow Weakness (30 pts):</strong> The 5-day Momentum Ledger slope must be strongly negative.</li>'
                '<li><strong>Price Rejection (20 pts):</strong> Presence of an upper wick relative to ATR, indicating failed buying attempts.</li>'
                '<li><strong>Close Pain (20 pts):</strong> Evaluates MFM (closing in the bottom quartile of range).</li>'
                '</ul>'
                '<p>A score &gt;= 50 triggers the marker.</p>'
                '</div>'
            ),
        }

    def agg_rules(self) -> dict:
        return {'is_bearish_absorption': 'any', 'bd_score': 'last'}
