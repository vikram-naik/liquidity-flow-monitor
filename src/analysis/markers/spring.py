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

        # -- Scoring Calculation (0-100) --
        # 1. Discount Depth (20 pts)
        discount_atr = (df['davwap'] - origin_price) / df['atr_50']
        base_disc, max_disc = 1.5, 3.0
        disc_progress = ((discount_atr - base_disc) / (max_disc - base_disc)).clip(0, 1)
        score_disc = disc_progress * 20

        # 2. Expansion (20 pts)
        exp_atr = ignition_expansion / df['atr_50']
        base_exp, max_exp = 0.8, 2.0
        exp_progress = ((exp_atr - base_exp) / (max_exp - base_exp)).clip(0, 1)
        score_exp = exp_progress * 20

        # 3. Flow Quality (20 pts)
        dvl = df['dvl_slope_5']
        prev_dvl = df['dvl_slope_5'].shift(1)
        score_flow = pd.Series(0.0, index=df.index)
        
        # Condition A: positive and strictly accelerating > 1.5x -> 20 pts
        cond_a = (dvl > 0) & (dvl > prev_dvl * 1.5)
        score_flow[cond_a] = 20.0
        
        # Condition B: positive but not super accelerating OR just improving from negative
        cond_b = ~cond_a & ((dvl > 0) | (dvl > prev_dvl))
        delta = dvl - prev_dvl
        # Scale 5 to 20 based on delta magnitude heuristic
        score_flow[cond_b] = (5 + (delta / 1e5)).clip(5, 20)

        # 4. Momentum Quality (MCS) (20 pts)
        mcs = df['mcs']
        prev_mcs = df['mcs'].shift(1)
        mcs_delta = mcs - prev_mcs
        score_mcs = pd.Series(0.0, index=df.index)
        
        cond_mcs_a = (mcs > 0) & (mcs_delta > 0)
        score_mcs[cond_mcs_a] = 20.0
        
        cond_mcs_b = ~cond_mcs_a & (mcs_delta > 0)
        score_mcs[cond_mcs_b] = (5 + (mcs_delta * 100)).clip(5, 15)

        # 5. MFM Quality (20 pts)
        score_mfm = (df['mfm'] * 20).clip(0, 20)

        # Total Score
        raw_score = score_disc + score_exp + score_flow + score_mcs + score_mfm
        df['spring_score'] = raw_score.round().clip(0, 100).fillna(0).astype(int)
        
        # Use just the numeric score as string (arrow identifies the marker type)
        df['spring_score'] = df['spring_score'].astype(str)

        # Nullify score where marker is not active
        df.loc[~df['is_spring'], 'spring_score'] = pd.NA

        return df

    def debug_info(self, df: pd.DataFrame, row_idx: int) -> list[dict]:
        r = df.iloc[row_idx]
        atr = r['atr_50']
        origin = min(r['price_open'], r.get('prev_close', r['price_open']))
        expansion = r['price_close'] - origin
        discount_atr = (r['davwap'] - origin) / atr if atr else 0
        exp_atr = expansion / atr if atr else 0
        mfm = r.get('mfm', 0)
        dvl_slope = r.get('dvl_slope_5', 0)
        prev_dvl_slope = df.iloc[row_idx - 1].get('dvl_slope_5', 0) if row_idx >= 1 else 0
        is_spring = bool(r.get('is_spring', False))
        score = r.get('spring_score', 'S0')

        return [
            {'label': 'Spring Active',       'value': str(is_spring),        'threshold': '',            'passed': is_spring,                  'detail': f'Score: {score}'},
            {'label': 'Deep Discount ≥ 1.5 ATR', 'value': f'{discount_atr:.2f}', 'threshold': '≥ 1.5', 'passed': discount_atr >= 1.5,        'detail': f'origin={origin:.1f} DAVWAP={r["davwap"]:.1f}'},
            {'label': 'Expansion ≥ 0.8 ATR', 'value': f'{exp_atr:.2f}',     'threshold': '≥ 0.8',      'passed': exp_atr >= 0.8,             'detail': f'exp={expansion:.1f}'},
            {'label': 'MFM > 0',             'value': f'{mfm:.4f}',         'threshold': '> 0',         'passed': mfm > 0,                    'detail': ''},
            {'label': 'Flow Rotation',       'value': f'{dvl_slope:.1E}',   'threshold': '> 0 or improving', 'passed': dvl_slope > 0 or dvl_slope > prev_dvl_slope, 'detail': f'prev_slope={prev_dvl_slope:.1E}'},
        ]

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
            'score_key': 'spring_score',
            'flag_key': 'is_spring',
            'screener_name': 'SCR: Spring',
            'text_format': 'score',
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
                '<div class="guide-section">'
                '<h4>Scoring Logic (0-100)</h4>'
                '<p>Springs are scored across 5 pillars (20 pts each) to measure reversal conviction. '
                'Scores above <strong>S70</strong> indicate massive, high-probability capitulation bottoms.</p>'
                '<ul>'
                '<li><strong>Discount Depth (20):</strong> Scales from 1.5 ATR to $\geq$ 3.0 ATR depth.</li>'
                '<li><strong>Expansion (20):</strong> Scales from 0.8 ATR to $\geq$ 2.0 ATR body expansion.</li>'
                '<li><strong>Flow Quality (20):</strong> Rewards sharply positive/accelerating 5-Day Cumulative Ledger slopes.</li>'
                '<li><strong>Momentum (MCS) (20):</strong> Rewards breadth momentum rotating upward.</li>'
                '<li><strong>Close Quality (20):</strong> Measures MFM (closing near the dead high scores 20).</li>'
                '</ul>'
                '</div>'
            ),
        }

    def agg_rules(self) -> dict:
        return {'is_spring': 'any', 'spring_score': 'last'}
