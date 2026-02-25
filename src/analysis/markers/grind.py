"""
Grind Marker — Composite 3-Day Progressive Buildup.

Isolates a 3-day sequence of "slow grind" upward breakouts that fail standard
single-day Ignition criteria, but mathematically achieve a commanding structural
breakaway collectively.  Represented as **Gold Up-Arrows (G1/G2/G3)** beneath
the candle.

Progressive triggers:
  G1 (Initiation):  Expansion ≥ 0.5 ATR from within 1.0 ATR of DAVWAP, +MFM
  G2 (Continuation): Close > G1, cumulative expansion ≥ 0.8 ATR
  G3 (Completion):   Close > G2, cumulative expansion ≥ 1.2 ATR, +DVL slope,
                     MCS improving

Includes "Ghosting" mechanic for live-edge progressive display.
"""

import pandas as pd
from src.analysis.markers import MarkerInterface


class GrindMarker(MarkerInterface):
    """Composite 3-Day Progressive Buildup marker."""

    _order = 40

    def name(self) -> str:
        return 'grind'

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        origin_price = df[['price_open', 'prev_close']].min(axis=1)
        dist_origin_davwap = (origin_price - df['davwap']) / df['atr_50']

        # Day 1: Expansion >= 0.5 ATR, near DAVWAP, +MFM
        is_grind_day1 = (
            (df['price_close'] - origin_price >= 0.5 * df['atr_50']) &
            (dist_origin_davwap.abs() <= 1.0) &
            (df['mfm'] > 0)
        )

        price_close_prev1 = df['price_close'].shift(1)
        origin_prev1 = origin_price.shift(1)
        atr_prev1 = df['atr_50'].shift(1)

        # Day 2: Close > Day 1, cumulative expansion >= 0.8 ATR
        is_grind_day2 = (
            is_grind_day1.shift(1, fill_value=False) &
            (df['price_close'] > price_close_prev1) &
            (df['price_close'] - origin_prev1 >= 0.8 * atr_prev1)
        )

        origin_prev2 = origin_price.shift(2)
        atr_prev2 = df['atr_50'].shift(2)

        # Day 3: Close > Day 2, cumulative >= 1.2 ATR, +DVL slope, MCS improving
        mcs_prev3 = df['mcs'].shift(3)
        is_grind_day3 = (
            is_grind_day2.shift(1, fill_value=False) &
            (df['price_close'] > price_close_prev1) &
            (df['price_close'] - origin_prev2 >= 1.2 * atr_prev2) &
            (df['dvl_slope_5'] > 0) &
            (df['mcs'] > mcs_prev3)
        )

        df['grind_level'] = 0

        # Retroactive valid assignment
        df.loc[is_grind_day3, 'grind_level'] = 3
        df.loc[is_grind_day3.shift(-1, fill_value=False), 'grind_level'] = 2
        df.loc[is_grind_day3.shift(-2, fill_value=False), 'grind_level'] = 1

        # Progressive (Ghosting) — handle live edge (last 2 rows max)
        if len(df) > 0:
            last_idx = df.index[-1]

            if is_grind_day2.at[last_idx] and df.at[last_idx, 'grind_level'] == 0:
                df.at[last_idx, 'grind_level'] = 2
                if len(df) > 1:
                    prev_idx = df.index[-2]
                    df.at[prev_idx, 'grind_level'] = 1

            elif is_grind_day1.at[last_idx] and df.at[last_idx, 'grind_level'] == 0:
                df.at[last_idx, 'grind_level'] = 1

        return df

    def debug_info(self, df: pd.DataFrame, row_idx: int) -> list[dict]:
        r = df.iloc[row_idx]
        atr = r['atr_50']
        level = int(r.get('grind_level', 0))
        origin = min(r['price_open'], r.get('prev_close', r['price_open']))
        dist_origin = abs(origin - r['davwap']) / atr if atr else 0
        expansion = r['price_close'] - origin
        mfm = r.get('mfm', 0)

        checks = [
            {'label': 'Grind Level', 'value': f'G{level}' if level > 0 else '0', 'threshold': '', 'passed': level > 0, 'detail': ''},
        ]

        # G1 checks
        g1_exp = expansion >= 0.5 * atr
        g1_prox = dist_origin <= 1.0
        g1_mfm = mfm > 0
        checks.extend([
            {'label': 'G1: Exp ≥ 0.5×ATR',  'value': f'{expansion:.1f}',   'threshold': f'≥ {0.5*atr:.1f}', 'passed': g1_exp,  'detail': f'origin={origin:.1f}'},
            {'label': 'G1: Prox ≤ 1.0 ATR', 'value': f'{dist_origin:.2f}', 'threshold': '≤ 1.0',            'passed': g1_prox, 'detail': f'DAVWAP={r["davwap"]:.1f}'},
            {'label': 'G1: MFM > 0',        'value': f'{mfm:.4f}',         'threshold': '> 0',              'passed': g1_mfm,  'detail': ''},
        ])

        # G2/G3 require prior row context
        if row_idx >= 1:
            prev = df.iloc[row_idx - 1]
            prev_origin = min(prev['price_open'], prev.get('prev_close', prev['price_open']))
            cum_exp_2 = r['price_close'] - prev_origin
            g2_higher = r['price_close'] > prev['price_close']
            g2_cum = cum_exp_2 >= 0.8 * prev['atr_50']
            checks.extend([
                {'label': 'G2: Close > Prev', 'value': f'{r["price_close"]:.1f}',  'threshold': f'> {prev["price_close"]:.1f}', 'passed': g2_higher, 'detail': ''},
                {'label': 'G2: CumExp ≥ 0.8×ATR', 'value': f'{cum_exp_2:.1f}',      'threshold': f'≥ {0.8*prev["atr_50"]:.1f}', 'passed': g2_cum,    'detail': ''},
            ])

        if row_idx >= 2:
            prev2 = df.iloc[row_idx - 2]
            prev2_origin = min(prev2['price_open'], prev2.get('prev_close', prev2['price_open']))
            cum_exp_3 = r['price_close'] - prev2_origin
            g3_dvl = r.get('dvl_slope_5', 0) > 0
            mcs_prev3 = df.iloc[row_idx - 3]['mcs'] if row_idx >= 3 else 0
            g3_mcs = r['mcs'] > mcs_prev3
            checks.extend([
                {'label': 'G3: CumExp ≥ 1.2×ATR', 'value': f'{cum_exp_3:.1f}',   'threshold': f'≥ {1.2*prev2["atr_50"]:.1f}', 'passed': cum_exp_3 >= 1.2 * prev2['atr_50'], 'detail': ''},
                {'label': 'G3: DVL Slope > 0',    'value': f'{r.get("dvl_slope_5",0):.1E}', 'threshold': '> 0',            'passed': g3_dvl,  'detail': ''},
                {'label': 'G3: MCS Improving',    'value': f'{r["mcs"]:.2f}',      'threshold': f'> {mcs_prev3:.2f}',      'passed': g3_mcs,  'detail': ''},
            ])

        return checks

    def screen(self, df: pd.DataFrame, latest: pd.Series, prev=None) -> bool:
        return int(latest.get('grind_level', 0)) > 0

    def metadata(self) -> dict:
        return {
            'id': 'grind',
            'label': 'Grind (G)',
            'is_chart_marker': True,
            'color': '#fcc419',
            'shape': 'arrowUp',
            'position': 'belowBar',
            'score_key': None,
            'flag_key': 'grind_level',
            'screener_name': 'SCR: Grind',
            'text_format': 'grind_level',  # display G1/G2/G3
            'legend_dot_style': (
                'background:#fcc419; '
                'clip-path: polygon(50% 0%, 0% 100%, 100% 100%); '
                'border-radius: 0;'
            ),
            'help_title': 'Grind Marker (Hidden Accumulation)',
            'help_html': (
                '<div class="guide-section">'
                '<h4>What it is</h4>'
                '<p>The Grind marker, or "Composite Ignition", isolates a 3-day '
                'sequence of "slow grind" upward breakouts that fail standard '
                'single-day Ignition criteria, but mathematically achieve a '
                'commanding structural breakaway collectively.</p>'
                '</div>'
                '<div class="guide-section">'
                '<h4>Progressive Triggers (G1 &rarr; G2 &rarr; G3)</h4>'
                '<ul>'
                '<li><strong>G1 (Initiation):</strong> Day 1 begins a move from within '
                '<code>1.0 ATR</code> of DAVWAP, expanding initially at least '
                '<code>0.5 ATR</code>.</li>'
                '<li><strong>G2 (Continuation):</strong> Day 2 closes higher, and '
                'cumulative expansion from G1 origin exceeds <code>0.8 ATR</code>.</li>'
                '<li><strong>G3 (Completion):</strong> Day 3 closes higher still, '
                'pushing the cumulative expansion from G1 origin beyond '
                '<code>1.2 ATR</code>. The 5-Day Momentum Ledger slope must be '
                'actively rising. Additionally, the Money Capacity Score (MCS) '
                'must be tracking strictly positively, or greater than Day 0 '
                '(the day prior to G1 initiation).</li>'
                '</ul>'
                '</div>'
                '<div class="guide-section">'
                '<h4>Ghosting Mechanic</h4>'
                '<p>The system gives you a live edge. If today acts like a strong '
                'Day 1, you will see a <strong>G1</strong> badge immediately. However, '
                'if this sequence fails to mature into a full 3-day Grind over the '
                'next few days, the isolated <strong>G1</strong> marker is retroactively '
                '"ghosted" (erased) to keep the historical chart pristine.</p>'
                '</div>'
            ),
        }

    def agg_rules(self) -> dict:
        return {'grind_level': 'max'}
