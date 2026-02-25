"""
Spring Marker — Deep Washout & Reversal.

Identifies high Risk-to-Reward (RRR) reversal setups where price is deeply
discounted but institutional accumulation (smart money) is beginning to rotate
upward.  Represented as a **Cyan Up-Arrow (S)** beneath the candle.

Trigger criteria (all must be true):
  - Deep Discount: Origin ≥ 1.5 ATR below DAVWAP
  - Strong Reversal: Expansion ≥ 0.8 ATR AND MFM > 0
  - Flow Rotation: Ledger slope is strictly positive or improving
"""

import pandas as pd
from src.analysis.markers import MarkerInterface


class SpringMarker(MarkerInterface):
    """Deep Washout & Reversal marker."""

    _order = 30

    def name(self) -> str:
        return 'spring'

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        origin_price = df[['price_open', 'prev_close']].min(axis=1)
        ignition_expansion = df['price_close'] - origin_price

        # 1. Deep Discount: Origin >= 1.5 ATR below DAVWAP
        deep_discount = (df['davwap'] - origin_price) / df['atr_50'] >= 1.5

        # 2. Strong Reversal: Expansion >= 0.8 ATR AND MFM > 0
        strong_reversal = (
            (ignition_expansion / df['atr_50'] >= 0.8) &
            (df['mfm'] > 0)
        )

        # 3. Flow Rotation: Ledger Slope improving or positive
        ledger_improving = (
            (df['dvl_slope_5'] > df['dvl_slope_5'].shift(1)) |
            (df['dvl_slope_5'] > 0)
        )

        df['is_spring'] = deep_discount & strong_reversal & ledger_improving
        df['is_spring'] = df['is_spring'].fillna(False)

        return df

    def screen(self, df: pd.DataFrame, latest: pd.Series, prev=None) -> bool:
        return bool(latest.get('is_spring', False))

    def metadata(self) -> dict:
        return {
            'id': 'spring',
            'label': 'Spring',
            'is_chart_marker': True,
            'color': '#00e5ff',
            'shape': 'arrowUp',
            'position': 'belowBar',
            'score_key': None,
            'flag_key': 'is_spring',
            'screener_name': 'SCR: Spring',
            'text_format': 'fixed:S',  # always show 'S'
            'legend_dot_style': (
                'background:#00e5ff; '
                'clip-path: polygon(50% 0%, 0% 100%, 100% 100%); '
                'border-radius: 0;'
            ),
            'help_title': 'Spring Marker (Deep Washout & Reversal)',
            'help_html': (
                '<div class="guide-section">'
                '<h4>What it is</h4>'
                '<p>The Spring marker identifies high Risk-to-Reward (RRR) reversal '
                'setups where price is deeply discounted, but institutional '
                'accumulation (smart money) is beginning to rotate upward. '
                'It is represented by a <strong>Cyan Up-Arrow (S)</strong> '
                'beneath the candle.</p>'
                '</div>'
                '<div class="guide-section">'
                '<h4>Triggers</h4>'
                '<ul>'
                '<li><strong>Deep Discount:</strong> The origin of the move must be '
                'heavily discounted, strictly <code>&gt;= 1.5 ATR</code> below the '
                'Delivery Anchored VWAP.</li>'
                '<li><strong>Strong Reversal:</strong> The day\'s expansion off the lows '
                'must be <code>&gt;= 0.8 ATR</code> AND the candle must close in the '
                'upper half of its range (Positive MFM).</li>'
                '<li><strong>Momentum Rotation:</strong> Despite the markdown, the '
                'underlying 5-Day Cumulative Volume Ledger must be actively curling '
                'upward or strictly positive.</li>'
                '</ul>'
                '</div>'
            ),
        }

    def agg_rules(self) -> dict:
        return {'is_spring': 'any'}
