"""
Exhaustion Marker — Top-Heavy Reversal Warning.

Identifies when price is heavily extended above value (DAVWAP) but institutional 
buying has dried up, leaving the stock vulnerable to a sharp mean reversion or 
structural breakdown. Represented as a **Yellow Circle** with "E" text.

Trigger criteria (all must be true):
  - Extension: High is >= 1.5 * ATR50 above DAVWAP
  - Divergence: DVL slope is flat or negative (<= 0)
  
Score pillars (0-100):
  - Extension Depth (30 pts)
  - Flow Divergence (30 pts)
  - Volume Dryness (20 pts)
  - MCS Loss (20 pts)

Score >= 50 triggers the marker.
"""

import pandas as pd
from src.analysis.markers import MarkerInterface


class ExhaustionMarker(MarkerInterface):
    """Top-Heavy Reversal Warning (Exhaustion) marker."""

    _order = 32

    def name(self) -> str:
        return 'exhaustion'

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        ext_atr = (df['price_high'] - df['davwap']) / df['atr_50']
        
        # Hard triggers
        ex_hard_trigger = (
            (ext_atr >= 1.5) &
            (df['dvl_slope_5'] <= 0)
        )

        # Pillar 1: Extension Depth (30 pts)
        score_ext = 30 * ((ext_atr - 1.5).clip(0, 1.5) / 1.5)

        # Pillar 2: Flow Divergence (30 pts)
        # Deeply negative DVL slope gets full points
        flow_norm = -df['dvl_slope_5'] / df['deliv_sma_10']
        score_flow = 30 * (flow_norm.clip(0, 0.5) / 0.5)

        # Pillar 3: Volume Dryness (20 pts)
        # Lower volume gets more points
        vol_ratio = df['delivery_qty'] / df['deliv_sma_10']
        score_dry = 20 * (1.0 - vol_ratio).clip(0, 1)

        # Pillar 4: MCS Loss (20 pts)
        # MCS dropping vs previous day
        mcs_delta = df['mcs'].shift(1) - df['mcs']
        score_mcs = 20 * (mcs_delta.clip(0, 0.5) / 0.5)

        df['exhaustion_score'] = (score_ext + score_flow + score_dry + score_mcs).round(0)
        df['is_exhaustion'] = ex_hard_trigger & (df['exhaustion_score'] >= 50)
        
        # Mutual Exclusion: Suppress if higher-priority markers triggered on this candle
        for higher in ['is_bearish_absorption', 'is_distribution', 'is_spring', 'is_ignition', 'is_coil']:
            if higher in df.columns:
                df['is_exhaustion'] = df['is_exhaustion'] & ~df[higher].fillna(False)

        df['is_exhaustion'] = df['is_exhaustion'].fillna(False)

        return df

    def debug_info(self, df: pd.DataFrame, row_idx: int) -> list[dict]:
        r = df.iloc[row_idx]
        atr = r['atr_50']
        ext_atr = (r['price_high'] - r['davwap']) / atr if atr else 0
        dvl_slope = r.get('dvl_slope_5', 0)
        deliv = r['delivery_qty']
        deliv_sma = r['deliv_sma_10'] if r['deliv_sma_10'] > 0 else 1
        vol_ratio = deliv / deliv_sma
        mcs = r.get('mcs', 0)
        prev_mcs = df.iloc[row_idx - 1].get('mcs', 0) if row_idx > 0 else 0
        mcs_delta = prev_mcs - mcs

        # Scoring
        s_ext = round(30 * max(0, min(1.5, ext_atr - 1.5)) / 1.5, 1)
        flow_norm = -dvl_slope / deliv_sma if deliv_sma else 0
        s_flow = round(30 * max(0, min(0.5, flow_norm)) / 0.5, 1)
        s_dry = round(20 * max(0, min(1.0, 1.0 - vol_ratio)), 1)
        s_mcs = round(20 * max(0, min(0.5, mcs_delta)) / 0.5, 1)
        total = s_ext + s_flow + s_dry + s_mcs

        return [
            {'label': 'Extension >= 1.5 ATR','value': f'{ext_atr:.2f}',  'threshold': '>= 1.5', 'passed': ext_atr >= 1.5,    'detail': f'DAVWAP={r["davwap"]:.1f}'},
            {'label': 'Flat/Negative Flow',  'value': f'{dvl_slope:.1E}', 'threshold': '<= 0',   'passed': dvl_slope <= 0,    'detail': ''},
            {'label': 'Extension (30)',      'value': f'{s_ext}',        'threshold': '',       'passed': True,              'detail': ''},
            {'label': 'Flow Divergence (30)','value': f'{s_flow}',       'threshold': '',       'passed': True,              'detail': ''},
            {'label': 'Volume Dryness (20)', 'value': f'{s_dry}',        'threshold': '',       'passed': True,              'detail': f'vol_ratio={vol_ratio:.2f}'},
            {'label': 'MCS Loss (20)',       'value': f'{s_mcs}',        'threshold': '',       'passed': True,              'detail': f'delta={mcs_delta:.2f}'},
            {'label': 'Total Score',         'value': f'{total:.0f}',     'threshold': '>= 50',  'passed': total >= 50,       'detail': f'is_ex={r.get("is_exhaustion", False)}'},
        ]

    def screen(self, df: pd.DataFrame, latest: pd.Series, prev=None) -> bool:
        return bool(latest.get('is_exhaustion', False))

    def metadata(self) -> dict:
        return {
            'id': 'exhaustion',
            'label': 'Exhaustion (E)',
            'is_chart_marker': True,
            'marker_type': 'bearish',
            'color': '#facc15', # Yellow
            'shape': 'circle',
            'position': 'aboveBar',
            'score_key': 'exhaustion_score',
            'flag_key': 'is_exhaustion',
            'screener_name': 'SCR: Exhaustion',
            'text_format': 'fixed:E',
            'legend_dot_style': 'background:#facc15; border-radius: 50%;',
            'help_title': 'Exhaustion Marker (E)',
            'help_html': (
                '<div class="guide-section">'
                '<h4>What it is</h4>'
                '<p>The Exhaustion marker identifies when price is heavily extended '
                'above value, but institutional buying has dried up. It warns of a '
                'potential top or severe mean reversion. It is represented by a '
                '<strong>Yellow Circle with "E"</strong> above the candle.</p>'
                '</div>'
                '<div class="guide-section">'
                '<h4>Triggers &amp; Scoring (0-100)</h4>'
                '<ul>'
                '<li><strong>Extension Depth (30 pts):</strong> Price highs must be '
                '<code>&gt;= 1.5 ATR</code> above the DAVWAP.</li>'
                '<li><strong>Flow Divergence (30 pts):</strong> The 5-day Momentum Ledger '
                'slope must be flat or strictly negative (diverging from the price trend).</li>'
                '<li><strong>Volume Dryness (20 pts):</strong> Upward or sideways price action '
                'is occurring on below-average volume.</li>'
                '<li><strong>MCS Loss (20 pts):</strong> Breadth/momentum is actively decaying '
                'compared to the previous day.</li>'
                '</ul>'
                '<p>A score &gt;= 50 triggers the marker.</p>'
                '</div>'
            ),
        }

    def agg_rules(self) -> dict:
        return {'is_exhaustion': 'any', 'exhaustion_score': 'last'}
