"""
Bearish Grind Marker — Composite 3-Day Progressive Markdown.

Isolates a 3-day sequence of "slow grind" downward breakouts that fail standard
single-day Distribution criteria, but mathematically achieve a commanding 
structural breakdown collectively. Represented as **Red Down-Arrows (BG1/BG2/BG3)** 
above the candle.

Progressive triggers:
  BG1 (Initiation):  Expansion >= 0.5 ATR (downward) from within 1.0 ATR of DAVWAP, -MFM
  BG2 (Continuation): Close < BG1, cumulative downward expansion >= 0.8 ATR
  BG3 (Completion):   Close < BG2, cumulative downward expansion >= 1.2 ATR, -DVL slope,
                      MCS degrading

Includes "Ghosting" mechanic for live-edge progressive display.
"""

import pandas as pd
from src.analysis.markers import MarkerInterface


class BearishGrindMarker(MarkerInterface):
    """Composite 3-Day Progressive Markdown marker."""

    _order = 41

    def name(self) -> str:
        return 'bearish_grind'

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        origin_price = df[['price_open', 'prev_close']].max(axis=1)
        dist_origin_davwap = (origin_price - df['davwap']) / df['atr_50']

        # Day 1: Downward Expansion >= 0.5 ATR, near DAVWAP, -MFM
        is_bg_day1 = (
            (origin_price - df['price_close'] >= 0.5 * df['atr_50']) &
            (dist_origin_davwap.abs() <= 1.0) &
            (df['mfm'] < 0)
        )

        price_close_prev1 = df['price_close'].shift(1)
        origin_prev1 = origin_price.shift(1)
        atr_prev1 = df['atr_50'].shift(1)

        # Day 2: Close < Day 1, cumulative downward expansion >= 0.8 ATR
        is_bg_day2 = (
            is_bg_day1.shift(1, fill_value=False) &
            (df['price_close'] < price_close_prev1) &
            (origin_prev1 - df['price_close'] >= 0.8 * atr_prev1)
        )

        origin_prev2 = origin_price.shift(2)
        atr_prev2 = df['atr_50'].shift(2)

        # Day 3: Close < Day 2, cumulative >= 1.2 ATR, -DVL slope, MCS degrading
        mcs_prev3 = df['mcs'].shift(3)
        is_bg_day3 = (
            is_bg_day2.shift(1, fill_value=False) &
            (df['price_close'] < price_close_prev1) &
            (origin_prev2 - df['price_close'] >= 1.2 * atr_prev2) &
            (df['dvl_slope_5'] < 0) &
            (df['mcs'] < mcs_prev3)
        )

        df['bearish_grind_level'] = 0

        # Retroactive valid assignment
        df.loc[is_bg_day3, 'bearish_grind_level'] = 3
        df.loc[is_bg_day3.shift(-1, fill_value=False), 'bearish_grind_level'] = 2
        df.loc[is_bg_day3.shift(-2, fill_value=False), 'bearish_grind_level'] = 1

        # Progressive (Ghosting) — handle live edge (last 2 rows max)
        if len(df) > 0:
            last_idx = df.index[-1]

            if is_bg_day2.at[last_idx] and df.at[last_idx, 'bearish_grind_level'] == 0:
                df.at[last_idx, 'bearish_grind_level'] = 2
                if len(df) > 1:
                    prev_idx = df.index[-2]
                    df.at[prev_idx, 'bearish_grind_level'] = 1

            elif is_bg_day1.at[last_idx] and df.at[last_idx, 'bearish_grind_level'] == 0:
                df.at[last_idx, 'bearish_grind_level'] = 1

        # Mutual Exclusion: Suppress if higher-priority (tactical/initiation) markers triggered on this candle
        priority_flags = ['is_exhaustion', 'is_bearish_absorption', 'is_spring', 'is_distribution', 'is_ignition', 'is_coil']
        for flag in priority_flags:
            if flag in df.columns:
                df.loc[df[flag].fillna(False), 'bearish_grind_level'] = 0

        return df

    def debug_info(self, df: pd.DataFrame, row_idx: int) -> list[dict]:
        r = df.iloc[row_idx]
        atr = r['atr_50']
        level = int(r.get('bearish_grind_level', 0))
        origin = max(r['price_open'], r.get('prev_close', r['price_open']))
        dist_origin = abs(origin - r['davwap']) / atr if atr else 0
        expansion = origin - r['price_close']
        mfm = r.get('mfm', 0)

        checks = [
            {'label': 'Bearish Grind Level', 'value': f'BG{level}' if level > 0 else '0', 'threshold': '', 'passed': level > 0, 'detail': ''},
        ]

        # BG1 checks
        bg1_exp = expansion >= 0.5 * atr
        bg1_prox = dist_origin <= 1.0
        bg1_mfm = mfm < 0
        checks.extend([
            {'label': 'BG1: Down Exp >= 0.5xATR',  'value': f'{expansion:.1f}',   'threshold': f'>= {0.5*atr:.1f}', 'passed': bg1_exp,  'detail': f'origin={origin:.1f}'},
            {'label': 'BG1: Prox <= 1.0 ATR', 'value': f'{dist_origin:.2f}', 'threshold': '<= 1.0',            'passed': bg1_prox, 'detail': f'DAVWAP={r["davwap"]:.1f}'},
            {'label': 'BG1: MFM < 0',        'value': f'{mfm:.4f}',         'threshold': '< 0',              'passed': bg1_mfm,  'detail': ''},
        ])

        # BG2/BG3 require prior row context
        if row_idx >= 1:
            prev = df.iloc[row_idx - 1]
            prev_origin = max(prev['price_open'], prev.get('prev_close', prev['price_open']))
            cum_exp_2 = prev_origin - r['price_close']
            bg2_lower = r['price_close'] < prev['price_close']
            bg2_cum = cum_exp_2 >= 0.8 * prev['atr_50']
            checks.extend([
                {'label': 'BG2: Close < Prev', 'value': f'{r["price_close"]:.1f}',  'threshold': f'< {prev["price_close"]:.1f}', 'passed': bg2_lower, 'detail': ''},
                {'label': 'BG2: Down CumExp >= 0.8xATR', 'value': f'{cum_exp_2:.1f}', 'threshold': f'>= {0.8*prev["atr_50"]:.1f}', 'passed': bg2_cum, 'detail': ''},
            ])

        if row_idx >= 2:
            prev2 = df.iloc[row_idx - 2]
            prev2_origin = max(prev2['price_open'], prev2.get('prev_close', prev2['price_open']))
            cum_exp_3 = prev2_origin - r['price_close']
            bg3_dvl = r.get('dvl_slope_5', 0) < 0
            mcs_prev3 = df.iloc[row_idx - 3]['mcs'] if row_idx >= 3 else 0
            bg3_mcs = r['mcs'] < mcs_prev3
            checks.extend([
                {'label': 'BG3: Down CumExp >= 1.2xATR', 'value': f'{cum_exp_3:.1f}', 'threshold': f'>= {1.2*prev2["atr_50"]:.1f}', 'passed': cum_exp_3 >= 1.2 * prev2['atr_50'], 'detail': ''},
                {'label': 'BG3: DVL Slope < 0',    'value': f'{r.get("dvl_slope_5",0):.1E}', 'threshold': '< 0',            'passed': bg3_dvl,  'detail': ''},
                {'label': 'BG3: MCS Degrading',    'value': f'{r["mcs"]:.2f}',      'threshold': f'< {mcs_prev3:.2f}',      'passed': bg3_mcs,  'detail': ''},
            ])

        return checks

    def screen(self, df: pd.DataFrame, latest: pd.Series, prev=None) -> bool:
        return int(latest.get('bearish_grind_level', 0)) > 0

    def metadata(self) -> dict:
        return {
            'id': 'bearish_grind',
            'label': 'Bear Grind (BG)',
            'is_chart_marker': True,
            'marker_type': 'bearish',
            'color': '#ff4d4d',
            'shape': 'arrowDown',
            'position': 'aboveBar',
            'score_key': None,
            'flag_key': 'bearish_grind_level',
            'screener_name': 'SCR: Bear Grind',
            'text_format': 'bearish_grind_level',
            'legend_dot_style': (
                'background:#ff4d4d; '
                'clip-path: polygon(50% 100%, 0% 0%, 100% 0%); '
                'border-radius: 0;'
            ),
            'help_title': 'Bearish Grind Marker',
            'help_html': (
                '<div class="guide-section">'
                '<h4>What it is</h4>'
                '<p>The Bear Grind marker isolates a 3-day sequence of "slow grind" '
                'downward breakdowns that mathematically achieve a commanding '
                'structural markdown collectively.</p>'
                '</div>'
            ),
        }

    def agg_rules(self) -> dict:
        return {'bearish_grind_level': 'max'}
