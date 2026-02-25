"""
Divergence Engine — DVL vs Price Structural Divergence Detection.

Implements 5-bar fractal swing detection on price, reads DVL at swing
dates, and classifies the relationship into one of four structural
patterns.

Four concrete marker classes share a ``_DivergenceBase``:

  * ``BullDivergenceMarker``   — Price LL + DVL HL (hidden accumulation)
  * ``BearDivergenceMarker``   — Price HH + DVL LH (distribution)
  * ``BullConfirmationMarker`` — Price HH + DVL HH (healthy trend)
  * ``BearConfirmationMarker`` — Price LL + DVL LL  (confirmed decline)
"""

import numpy as np
import pandas as pd
from typing import Optional
from src.analysis.markers import MarkerInterface


# ────────────────────────────────────────────────────────────────────
# Shared base — swing detection, DVL alignment, significance filter
# ────────────────────────────────────────────────────────────────────

class _DivergenceBase(MarkerInterface):
    """
    Abstract base performing heavy swing detection once and caching
    results as private columns.  Concrete subclasses classify their
    specific pattern from the cached columns.
    """

    # Fractal length (n bars on left, n bars on right) for Swing High/Low
    _SWING_N = 8
    
    # Minimum requirements to pair consecutive swings
    _MIN_SWING_SPACING = 15     # bars
    _MIN_PRICE_ATR     = 0.5    # 0.5 * 50-day ATR difference between price legs
    _MIN_DVL_PCT       = 0.05   # 5% relative difference in DVL between legs

    # --- shared detection (idempotent) ---------------------------------

    @staticmethod
    def _ensure_swing_columns(df: pd.DataFrame) -> pd.DataFrame:
        """
        Run swing detection once per pipeline pass.

        Adds private columns ``_swing_high``, ``_swing_low``,
        ``_div_type_high``, ``_div_type_low`` if not already present.
        """
        if '_swing_high' in df.columns:
            return df          # already computed by a sibling marker

        n = _DivergenceBase._SWING_N

        # --- Swing detection via rolling max/min comparison ---
        swing_high = pd.Series(False, index=df.index)
        swing_low = pd.Series(False, index=df.index)

        highs = df['price_high'].values
        lows = df['price_low'].values

        for i in range(n, len(df) - n):
            left_h = highs[i - n:i]
            right_h = highs[i + 1:i + n + 1]
            if highs[i] > left_h.max() and highs[i] > right_h.max():
                swing_high.iloc[i] = True

            left_l = lows[i - n:i]
            right_l = lows[i + 1:i + n + 1]
            if lows[i] < left_l.min() and lows[i] < right_l.min():
                swing_low.iloc[i] = True

        df['_swing_high'] = swing_high
        df['_swing_low'] = swing_low

        # --- Classify divergences at each swing point ---
        dvl = df['dvl'].values
        atr = df['atr_50'].values

        # Divergence type columns: None / 'div_bull' / 'confirm_bear' etc.
        div_type_low = pd.Series(None, index=df.index, dtype=object)
        div_type_high = pd.Series(None, index=df.index, dtype=object)

        # Collect swing indices
        sh_idxs = df.index[swing_high].tolist()
        sl_idxs = df.index[swing_low].tolist()

        # --- Swing High classification (Bearish Div / Bullish Confirm) ---
        for k in range(1, len(sh_idxs)):
            curr_idx = sh_idxs[k]
            prev_idx = sh_idxs[k - 1]

            curr_pos = df.index.get_loc(curr_idx)
            prev_pos = df.index.get_loc(prev_idx)

            # Minimum spacing check
            if (curr_pos - prev_pos) < _DivergenceBase._MIN_SWING_SPACING:
                continue

            price_curr = highs[curr_pos]
            price_prev = highs[prev_pos]
            dvl_curr = dvl[curr_pos]
            dvl_prev = dvl[prev_pos]

            # Must be Price Higher High
            if not (price_curr > price_prev):
                continue

            # Significance filters
            curr_atr = atr[curr_pos] if not np.isnan(atr[curr_pos]) else 1.0
            if abs(price_curr - price_prev) < _DivergenceBase._MIN_PRICE_ATR * curr_atr:
                continue

            if np.isnan(dvl_curr) or np.isnan(dvl_prev):
                continue

            dvl_base = abs(dvl_prev) if abs(dvl_prev) > 0 else 1.0
            dvl_delta_pct = abs(dvl_curr - dvl_prev) / dvl_base
            if dvl_delta_pct < _DivergenceBase._MIN_DVL_PCT:
                continue

            # Classify
            if dvl_curr < dvl_prev:
                div_type_high.at[curr_idx] = 'div_bear'
            else:
                div_type_high.at[curr_idx] = 'confirm_bull'

        # --- Swing Low classification (Bullish Div / Bearish Confirm) ----
        for k in range(1, len(sl_idxs)):
            curr_idx = sl_idxs[k]
            prev_idx = sl_idxs[k - 1]

            curr_pos = df.index.get_loc(curr_idx)
            prev_pos = df.index.get_loc(prev_idx)

            if (curr_pos - prev_pos) < _DivergenceBase._MIN_SWING_SPACING:
                continue

            price_curr = lows[curr_pos]
            price_prev = lows[prev_pos]
            dvl_curr = dvl[curr_pos]
            dvl_prev = dvl[prev_pos]

            # Must be Price Lower Low
            if not (price_curr < price_prev):
                continue

            curr_atr = atr[curr_pos] if not np.isnan(atr[curr_pos]) else 1.0
            if abs(price_curr - price_prev) < _DivergenceBase._MIN_PRICE_ATR * curr_atr:
                continue

            if np.isnan(dvl_curr) or np.isnan(dvl_prev):
                continue

            dvl_base = abs(dvl_prev) if abs(dvl_prev) > 0 else 1.0
            dvl_delta_pct = abs(dvl_curr - dvl_prev) / dvl_base
            if dvl_delta_pct < _DivergenceBase._MIN_DVL_PCT:
                continue

            if dvl_curr > dvl_prev:
                div_type_low.at[curr_idx] = 'div_bull'
            else:
                div_type_low.at[curr_idx] = 'confirm_bear'

        df['_div_type_high'] = div_type_high
        df['_div_type_low'] = div_type_low
        return df

    # --- debug / introspection (shared by all 4 subclasses) ----------------

    def debug_info(self, df: pd.DataFrame, row_idx: int) -> list[dict]:
        """Trace swing pairing for divergence / confirmation markers."""
        import numpy as np

        r = df.iloc[row_idx]
        row_date = df.index[row_idx]
        checks: list[dict] = []
        marker_name = self.name()
        meta = self.metadata()
        flag_key = meta.get('flag_key', '')
        is_active = bool(r.get(flag_key, False))

        checks.append({
            'label': f'{meta["label"]} Active',
            'value': str(is_active),
            'threshold': '',
            'passed': is_active,
            'detail': flag_key,
        })

        if not is_active:
            # Check if it's even a swing point
            is_sh = bool(r.get('_swing_high', False))
            is_sl = bool(r.get('_swing_low', False))
            checks.append({
                'label': 'Is Swing Point',
                'value': f'SH={is_sh} SL={is_sl}',
                'threshold': 'either True',
                'passed': is_sh or is_sl,
                'detail': 'Not a swing → no divergence possible' if not (is_sh or is_sl) else '',
            })
            return checks

        # --- Active marker — trace the pairing ---
        # Determine which swing type this marker uses
        uses_lows = marker_name in ('div_bull', 'confirm_bear')
        swing_col = '_swing_low' if uses_lows else '_swing_high'
        div_type_col = '_div_type_low' if uses_lows else '_div_type_high'
        price_col = 'price_low' if uses_lows else 'price_high'

        # Find all swing indices of this type
        swing_mask = df[swing_col] == True
        swing_idxs = df.index[swing_mask].tolist()

        if row_date not in swing_idxs:
            checks.append({'label': 'Swing Index', 'value': 'NOT FOUND', 'threshold': '', 'passed': False, 'detail': ''})
            return checks

        k = swing_idxs.index(row_date)
        if k == 0:
            checks.append({'label': 'Prior Swing', 'value': 'NONE (first swing)', 'threshold': '', 'passed': False, 'detail': ''})
            return checks

        prev_swing_date = swing_idxs[k - 1]
        curr_pos = df.index.get_loc(row_date)
        prev_pos = df.index.get_loc(prev_swing_date)
        spacing = curr_pos - prev_pos

        price_curr = df.iloc[curr_pos][price_col]
        price_prev = df.iloc[prev_pos][price_col]
        dvl_curr = r['dvl']
        dvl_prev = df.iloc[prev_pos]['dvl']
        atr = r['atr_50']
        price_delta = abs(price_curr - price_prev)
        min_price_delta = self._MIN_PRICE_ATR * atr

        dvl_base = abs(dvl_prev) if abs(dvl_prev) > 0 else 1.0
        dvl_pct = abs(dvl_curr - dvl_prev) / dvl_base if not (np.isnan(dvl_curr) or np.isnan(dvl_prev)) else 0

        prev_date_str = prev_swing_date.strftime('%Y-%m-%d') if hasattr(prev_swing_date, 'strftime') else str(prev_swing_date)

        checks.extend([
            {'label': 'Paired With',       'value': prev_date_str,           'threshold': '',                           'passed': True,                             'detail': f'{price_col}={price_prev:.2f}'},
            {'label': 'Spacing',           'value': f'{spacing} bars',       'threshold': f'≥ {self._MIN_SWING_SPACING}','passed': spacing >= self._MIN_SWING_SPACING,'detail': ''},
            {'label': f'Price {"LL" if uses_lows else "HH"}',
             'value': f'{price_curr:.2f}', 'threshold': f'{"<" if uses_lows else ">"} {price_prev:.2f}',
             'passed': (price_curr < price_prev) if uses_lows else (price_curr > price_prev),
             'detail': f'Δ={price_delta:.2f}'},
            {'label': 'Price Significance','value': f'{price_delta:.2f}',    'threshold': f'≥ {min_price_delta:.2f} (0.5×ATR)', 'passed': price_delta >= min_price_delta, 'detail': f'ATR={atr:.2f}'},
            {'label': f'DVL {"LL" if "bear" in marker_name else "HH" if "bull" in marker_name else "?"}',
             'value': f'{dvl_curr:.0f}',   'threshold': f'{"<" if "bear" in marker_name else ">"} {dvl_prev:.0f}',
             'passed': (dvl_curr < dvl_prev) if 'bear' in marker_name else (dvl_curr > dvl_prev),
             'detail': f'prev_dvl={dvl_prev:.0f}'},
            {'label': 'DVL Significance',  'value': f'{dvl_pct:.4f}',       'threshold': f'≥ {self._MIN_DVL_PCT}',     'passed': dvl_pct >= self._MIN_DVL_PCT,     'detail': f'Δ%={dvl_pct*100:.1f}%'},
        ])

        return checks


# ────────────────────────────────────────────────────────────────────
# Concrete markers
# ────────────────────────────────────────────────────────────────────

class BullDivergenceMarker(_DivergenceBase):
    """Price Lower Low + DVL Higher Low → Hidden Accumulation."""

    _order = 55

    def name(self) -> str:
        return 'div_bull'

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = self._ensure_swing_columns(df)
        df['is_div_bull'] = df['_div_type_low'] == 'div_bull'
        return df

    def screen(self, df: pd.DataFrame, latest: pd.Series,
               prev: Optional[pd.Series] = None) -> bool:
        return bool(latest.get('is_div_bull', False))

    def metadata(self) -> dict:
        return {
            'id': 'div_bull',
            'label': 'D↑',
            'is_chart_marker': True,
            'color': '#26a69a',
            'shape': 'diamond',
            'position': 'belowBar',
            'score_key': None,
            'flag_key': 'is_div_bull',
            'screener_name': 'SCR: Div-Bull',
            'text_format': 'fixed:D↑',
            'legend_dot_style': 'background:#26a69a; transform: rotate(45deg); border-radius: 2px;',
            'help_title': 'Bullish Divergence (Hidden Accumulation)',
            'help_html': (
                '<div class="guide-section">'
                '<h4>What it is</h4>'
                '<p>A structural disagreement where price makes a <strong>Lower Low</strong> '
                'but the Delivery Volume Ledger (DVL) makes a <strong>Higher Low</strong>. '
                'This signals that institutional accumulation is occurring behind the scenes '
                'during a price decline — smart money is buying while retail is panicking.</p>'
                '</div>'
                '<div class="guide-section">'
                '<h4>Detection Logic</h4>'
                '<ul>'
                '<li><strong>Swing Detection:</strong> 8-bar fractal identifies structural '
                'swing lows on the price series.</li>'
                '<li><strong>DVL Alignment:</strong> DVL is read at both swing dates. '
                'If DVL at the new low exceeds DVL at the prior low, it\'s bullish.</li>'
                '<li><strong>Significance:</strong> Swing amplitude ≥ 0.5 ATR and '
                'DVL delta ≥ 5% of prior DVL value.</li>'
                '</ul>'
                '<p style="margin-top:12px; font-size:12px; color:var(--text-muted);">'
                '<em>For deeper context on the 8-day confirmation lag and structural playbooks, '
                'see the <a href="#" onclick="showMethodology(); closeHelp(); return false;" style="color:var(--blue);">Methodology Guide</a>.</em></p>'
                '</div>'
            ),
        }

    def agg_rules(self) -> dict:
        return {'is_div_bull': 'any'}


class BearDivergenceMarker(_DivergenceBase):
    """Price Higher High + DVL Lower High → Distribution."""

    _order = 55

    def name(self) -> str:
        return 'div_bear'

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = self._ensure_swing_columns(df)
        df['is_div_bear'] = df['_div_type_high'] == 'div_bear'
        return df

    def screen(self, df: pd.DataFrame, latest: pd.Series,
               prev: Optional[pd.Series] = None) -> bool:
        return bool(latest.get('is_div_bear', False))

    def metadata(self) -> dict:
        return {
            'id': 'div_bear',
            'label': 'D↓',
            'is_chart_marker': True,
            'color': '#ef5350',
            'shape': 'diamond',
            'position': 'aboveBar',
            'score_key': None,
            'flag_key': 'is_div_bear',
            'screener_name': 'SCR: Div-Bear',
            'text_format': 'fixed:D↓',
            'legend_dot_style': 'background:#ef5350; transform: rotate(45deg); border-radius: 2px;',
            'help_title': 'Bearish Divergence (Distribution)',
            'help_html': (
                '<div class="guide-section">'
                '<h4>What it is</h4>'
                '<p>A structural disagreement where price makes a <strong>Higher High</strong> '
                'but the DVL makes a <strong>Lower High</strong>. '
                'This signals distribution — smart money is selling into the rally '
                'while retail is buying the breakout.</p>'
                '</div>'
                '<div class="guide-section">'
                '<h4>Detection Logic</h4>'
                '<ul>'
                '<li><strong>Swing Detection:</strong> 8-bar fractal identifies structural '
                'swing highs on the price series.</li>'
                '<li><strong>DVL Alignment:</strong> DVL is read at both swing dates. '
                'If DVL at the new high is lower than DVL at the prior high, it\'s bearish.</li>'
                '<li><strong>Significance:</strong> Swing amplitude ≥ 0.5 ATR and '
                'DVL delta ≥ 5% of prior DVL value.</li>'
                '</ul>'
                '<p style="margin-top:12px; font-size:12px; color:var(--text-muted);">'
                '<em>For deeper context on the 8-day confirmation lag and structural playbooks, '
                'see the <a href="#" onclick="showMethodology(); closeHelp(); return false;" style="color:var(--blue);">Methodology Guide</a>.</em></p>'
                '</div>'
            ),
        }

    def agg_rules(self) -> dict:
        return {'is_div_bear': 'any'}


class BullConfirmationMarker(_DivergenceBase):
    """Price Higher High + DVL Higher High → Healthy Trend."""

    _order = 55

    def name(self) -> str:
        return 'confirm_bull'

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = self._ensure_swing_columns(df)
        df['is_confirm_bull'] = df['_div_type_high'] == 'confirm_bull'
        return df

    def screen(self, df: pd.DataFrame, latest: pd.Series,
               prev: Optional[pd.Series] = None) -> bool:
        return bool(latest.get('is_confirm_bull', False))

    def metadata(self) -> dict:
        return {
            'id': 'confirm_bull',
            'label': 'C↑',
            'is_chart_marker': True,
            'color': '#66bb6a',
            'shape': 'diamond',
            'position': 'belowBar',
            'score_key': None,
            'flag_key': 'is_confirm_bull',
            'screener_name': 'SCR: Confirm-Bull',
            'text_format': 'fixed:C↑',
            'legend_dot_style': 'background:#66bb6a; transform: rotate(45deg); border-radius: 2px;',
            'help_title': 'Bullish Confirmation (Healthy Trend)',
            'help_html': (
                '<div class="guide-section">'
                '<h4>What it is</h4>'
                '<p>Price makes a <strong>Higher High</strong> and DVL also makes a '
                '<strong>Higher High</strong>. The money flow structurally supports '
                'the price advance — a confirmed healthy trend.</p>'
                '</div>'
                '<div class="guide-section">'
                '<h4>Interpretation</h4>'
                '<p>This is a positive signal: institutions are actively accumulating '
                'on successive breakouts. Coil & Ignition markers near confirmations '
                'carry higher conviction.</p>'
                '<p style="margin-top:12px; font-size:12px; color:var(--text-muted);">'
                '<em>For deeper context on the 8-day confirmation lag and structural playbooks, '
                'see the <a href="#" onclick="showMethodology(); closeHelp(); return false;" style="color:var(--blue);">Methodology Guide</a>.</em></p>'
                '</div>'
            ),
        }

    def agg_rules(self) -> dict:
        return {'is_confirm_bull': 'any'}


class BearConfirmationMarker(_DivergenceBase):
    """Price Lower Low + DVL Lower Low → Confirmed Decline."""

    _order = 55

    def name(self) -> str:
        return 'confirm_bear'

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = self._ensure_swing_columns(df)
        df['is_confirm_bear'] = df['_div_type_low'] == 'confirm_bear'
        return df

    def screen(self, df: pd.DataFrame, latest: pd.Series,
               prev: Optional[pd.Series] = None) -> bool:
        return bool(latest.get('is_confirm_bear', False))

    def metadata(self) -> dict:
        return {
            'id': 'confirm_bear',
            'label': 'C↓',
            'is_chart_marker': True,
            'color': '#ef9a9a',
            'shape': 'diamond',
            'position': 'aboveBar',
            'score_key': None,
            'flag_key': 'is_confirm_bear',
            'screener_name': 'SCR: Confirm-Bear',
            'text_format': 'fixed:C↓',
            'legend_dot_style': 'background:#ef9a9a; transform: rotate(45deg); border-radius: 2px;',
            'help_title': 'Bearish Confirmation (Confirmed Decline)',
            'help_html': (
                '<div class="guide-section">'
                '<h4>What it is</h4>'
                '<p>Price makes a <strong>Lower Low</strong> and DVL also makes a '
                '<strong>Lower Low</strong>. The money flow confirms the decline — '
                'institutions are actively distributing on successive breakdowns.</p>'
                '</div>'
                '<div class="guide-section">'
                '<h4>Interpretation</h4>'
                '<p>A negative signal: the downtrend is structurally supported by '
                'institutional selling. Spring markers in this context carry lower '
                'conviction — the reversal thesis is weakened.</p>'
                '<p style="margin-top:12px; font-size:12px; color:var(--text-muted);">'
                '<em>For deeper context on the 8-day confirmation lag and structural playbooks, '
                'see the <a href="#" onclick="showMethodology(); closeHelp(); return false;" style="color:var(--blue);">Methodology Guide</a>.</em></p>'
                '</div>'
            ),
        }

    def agg_rules(self) -> dict:
        return {'is_confirm_bear': 'any'}
