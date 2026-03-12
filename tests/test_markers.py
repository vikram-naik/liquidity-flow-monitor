"""Tests for individual marker classes using synthetic DataFrames."""

import pandas as pd
import numpy as np
import pytest

# Ensure marker modules are importable
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.analysis.markers.coil import CoilMarker
from src.analysis.markers.ignition import IgnitionMarker
from src.analysis.markers.spring import SpringMarker
from src.analysis.markers.grind import GrindMarker
from src.analysis.markers.intensity import IntensityMarker
from src.analysis.markers.crossover_up import CrossoverUpMarker
from src.analysis.markers.crossover_down import CrossoverDownMarker
from src.analysis.markers.high_score import HighScoreMarker


def _base_df(n=20):
    """Create a minimal DataFrame with all shared prerequisite columns."""
    dates = pd.date_range('2024-01-01', periods=n, freq='B')
    rng = np.random.default_rng(42)

    close = 100 + rng.standard_normal(n).cumsum()
    highs = close + rng.uniform(0.5, 3, n)
    lows = close - rng.uniform(0.5, 3, n)
    opens = close + rng.uniform(-1, 1, n)

    df = pd.DataFrame({
        'price_open': opens,
        'price_high': highs,
        'price_low': lows,
        'price_close': close,
        'delivery_qty': rng.uniform(5000, 50000, n),
        'volume_total': rng.uniform(50000, 200000, n),
    }, index=dates)

    # Shared prerequisites (normally computed in data.py)
    df['prev_close'] = df['price_close'].shift(1).fillna(df['price_open'])
    df['prev_high'] = df['price_high'].shift(1)
    df['deliv_sma_10'] = df['delivery_qty'].rolling(10, min_periods=1).mean()

    tr = np.maximum(
        df['price_high'] - df['price_low'],
        np.maximum(
            abs(df['price_high'] - df['prev_close']),
            abs(df['price_low'] - df['prev_close'])
        )
    )
    df['atr_50'] = tr.rolling(50, min_periods=1).mean()

    # DVL / DAVWAP stubs
    df['davwap'] = close  # price == davwap → near value
    df['dvl_slope_5'] = rng.uniform(-0.1, 0.1, n)
    df['mfm'] = rng.uniform(-1, 1, n)
    df['mcs'] = rng.uniform(-1, 1, n)
    df['mcs_slope_5'] = rng.uniform(-0.05, 0.05, n)
    df['days_since_anchor'] = range(n)

    return df


# ============================================================
# CoilMarker tests
# ============================================================

class TestCoilMarker:
    def test_triggers_on_tight_candle_near_value(self):
        marker = CoilMarker()
        df = _base_df(20)

        # Force a tight candle: range < 0.8 ATR, body < 0.4 ATR, near DAVWAP
        idx = df.index[15]
        atr = df.at[idx, 'atr_50']
        mid = df.at[idx, 'davwap']
        df.at[idx, 'price_high'] = mid + 0.1 * atr
        df.at[idx, 'price_low'] = mid - 0.1 * atr
        df.at[idx, 'price_close'] = mid + 0.05 * atr
        df.at[idx, 'price_open'] = mid - 0.05 * atr
        df.at[idx, 'delivery_qty'] = df.at[idx, 'deliv_sma_10'] * 0.3  # dry
        df.at[idx, 'dvl_slope_5'] = 100  # positive ledger

        df = marker.evaluate(df)
        assert df.at[idx, 'is_coil'] == True
        assert df.at[idx, 'coil_score'] >= 50

    def test_no_trigger_on_wide_candle(self):
        marker = CoilMarker()
        df = _base_df(20)

        # Force a wide candle: range >> ATR
        idx = df.index[15]
        atr = df.at[idx, 'atr_50']
        df.at[idx, 'price_high'] = df.at[idx, 'price_close'] + 3 * atr
        df.at[idx, 'price_low'] = df.at[idx, 'price_close'] - 3 * atr

        df = marker.evaluate(df)
        assert df.at[idx, 'is_coil'] == False

    def test_metadata(self):
        m = CoilMarker()
        meta = m.metadata()
        assert meta['id'] == 'coil'
        assert meta['color'] == '#4dabf7'
        assert meta['is_chart_marker'] == True
        assert meta['screener_name'] == 'SCR: Coil'

    def test_screen(self):
        marker = CoilMarker()
        latest = pd.Series({'is_coil': True})
        assert marker.screen(pd.DataFrame(), latest) == True
        latest = pd.Series({'is_coil': False})
        assert marker.screen(pd.DataFrame(), latest) == False


# ============================================================
# IgnitionMarker tests
# ============================================================

class TestIgnitionMarker:
    def test_triggers_on_explosive_candle(self):
        marker = IgnitionMarker()
        df = _base_df(20)

        idx = df.index[15]
        atr = df.at[idx, 'atr_50']
        davwap = df.at[idx, 'davwap']

        # Close = open + 2 ATR (massive expansion from near DAVWAP)
        df.at[idx, 'price_open'] = davwap
        df.at[idx, 'prev_close'] = davwap
        df.at[idx, 'price_close'] = davwap + 2 * atr
        df.at[idx, 'price_high'] = davwap + 2.1 * atr
        df.at[idx, 'price_low'] = davwap - 0.1 * atr
        df.at[idx, 'delivery_qty'] = df.at[idx, 'deliv_sma_10'] * 3  # high volume
        df.at[idx, 'dvl_slope_5'] = df.at[idx, 'deliv_sma_10'] * 0.5  # strong slope

        df = marker.evaluate(df)
        assert df.at[idx, 'is_ignition'] == True
        assert df.at[idx, 'ignition_score'] >= 50

    def test_no_trigger_on_down_candle(self):
        marker = IgnitionMarker()
        df = _base_df(20)

        idx = df.index[15]
        # Close < Open → down candle
        df.at[idx, 'price_close'] = df.at[idx, 'price_open'] - 5

        df = marker.evaluate(df)
        assert df.at[idx, 'is_ignition'] == False

    def test_metadata(self):
        m = IgnitionMarker()
        meta = m.metadata()
        assert meta['id'] == 'ignition'
        assert meta['color'] == '#b197fc'
        assert meta['position'] == 'aboveBar'


# ============================================================
# SpringMarker tests
# ============================================================

class TestSpringMarker:
    def test_triggers_on_deep_washout_reversal(self):
        marker = SpringMarker()
        df = _base_df(20)

        idx = df.index[15]
        atr = df.at[idx, 'atr_50']
        davwap = df.at[idx, 'davwap']

        # Origin deeply below DAVWAP: open 2 ATR below DAVWAP
        origin = davwap - 2 * atr
        df.at[idx, 'price_open'] = origin
        df.at[idx, 'prev_close'] = origin
        df.at[idx, 'price_close'] = origin + 1.5 * atr  # strong reversal
        df.at[idx, 'price_high'] = origin + 1.6 * atr
        df.at[idx, 'price_low'] = origin - 0.1 * atr
        df.at[idx, 'mfm'] = 0.5  # positive MFM
        df.at[idx, 'dvl_slope_5'] = 100  # positive ledger

        # Need previous row for shift comparison
        if len(df) > 1:
            prev_idx = df.index[14]
            df.at[prev_idx, 'dvl_slope_5'] = 50  # lower than current

        df = marker.evaluate(df)
        assert df.at[idx, 'is_spring'] == True

    def test_metadata(self):
        m = SpringMarker()
        meta = m.metadata()
        assert meta['id'] == 'spring'
        assert meta['flag_key'] == 'is_spring'
        assert meta['screener_name'] == 'SCR: Spring'


# ============================================================
# GrindMarker tests
# ============================================================

class TestGrindMarker:
    def test_metadata(self):
        m = GrindMarker()
        meta = m.metadata()
        assert meta['id'] == 'grind'
        assert meta['flag_key'] == 'grind_level'
        assert meta['text_format'] == 'grind_level'

    def test_agg_rules(self):
        m = GrindMarker()
        rules = m.agg_rules()
        assert rules == {'grind_level': 'max'}

    def test_screen(self):
        marker = GrindMarker()
        latest = pd.Series({'grind_level': 3})
        assert marker.screen(pd.DataFrame(), latest) == True
        latest = pd.Series({'grind_level': 0})
        assert marker.screen(pd.DataFrame(), latest) == False


# ============================================================
# IntensityMarker tests
# ============================================================

class TestIntensityMarker:
    def test_computes_angles(self):
        marker = IntensityMarker()
        df = _base_df(20)
        df['_cum_dvl_slope'] = df['dvl_slope_5'] * 0.8

        df = marker.evaluate(df)
        assert 'ledger_angle' in df.columns
        assert 'ledger_velocity' in df.columns
        assert 'mcs_angle' in df.columns

    def test_not_a_chart_marker(self):
        m = IntensityMarker()
        assert m.metadata()['is_chart_marker'] == False
        assert m.metadata()['screener_name'] == 'SCR: HH/HL'

    def test_screen_always_false(self):
        m = IntensityMarker()
        assert m.screen(pd.DataFrame(), pd.Series()) == False


# ============================================================
# Crossover Markers
# ============================================================

class TestCrossoverUpMarker:
    def test_triggers_on_crossover(self):
        m = CrossoverUpMarker()
        prev = pd.Series({'price_close': 95, 'davwap': 100})   # below
        latest = pd.Series({'price_close': 105, 'davwap': 100}) # above
        assert m.screen(pd.DataFrame(), latest, prev) == True

    def test_no_trigger_when_already_above(self):
        m = CrossoverUpMarker()
        prev = pd.Series({'price_close': 105, 'davwap': 100})   # already above
        latest = pd.Series({'price_close': 110, 'davwap': 100})
        assert m.screen(pd.DataFrame(), latest, prev) == False

    def test_no_trigger_without_prev(self):
        m = CrossoverUpMarker()
        latest = pd.Series({'price_close': 105, 'davwap': 100})
        assert m.screen(pd.DataFrame(), latest, None) == False


class TestCrossoverDownMarker:
    def test_triggers_on_crossover(self):
        m = CrossoverDownMarker()
        prev = pd.Series({'price_close': 105, 'davwap': 100})   # above
        latest = pd.Series({'price_close': 95, 'davwap': 100})  # below
        assert m.screen(pd.DataFrame(), latest, prev) == True

    def test_no_trigger_when_already_below(self):
        m = CrossoverDownMarker()
        prev = pd.Series({'price_close': 95, 'davwap': 100})   # already below
        latest = pd.Series({'price_close': 90, 'davwap': 100})
        assert m.screen(pd.DataFrame(), latest, prev) == False


# ============================================================
# HighScore Marker
# ============================================================

class TestHighScoreMarker:
    def test_triggers_on_high_ignition_score(self):
        m = HighScoreMarker()
        latest = pd.Series({'ignition_score': 95, 'coil_score': 30})
        assert m.screen(pd.DataFrame(), latest) == True

    def test_triggers_on_high_coil_score(self):
        m = HighScoreMarker()
        latest = pd.Series({'ignition_score': 30, 'coil_score': 92})
        assert m.screen(pd.DataFrame(), latest) == True

    def test_no_trigger_on_low_scores(self):
        m = HighScoreMarker()
        latest = pd.Series({'ignition_score': 50, 'coil_score': 60})
        assert m.screen(pd.DataFrame(), latest) == False


# ============================================================
# Divergence Markers
# ============================================================

from src.analysis.markers.divergence import (
    BullDivergenceMarker, BearDivergenceMarker,
    BullConfirmationMarker, BearConfirmationMarker,
)


def _divergence_df():
    """
    Create a 40-bar DataFrame with deliberate swings for divergence testing.

    Bars 0-9:  flat
    Bar  10:   Swing Low #1 (price_low=90,   DVL=1000)
    Bars 11-24: rising
    Bar  25:   Swing Low #2 (price_low=85 < 90 → LL,  DVL=1500 > 1000 → HL)
    → Expected: Bullish Divergence at bar 25

    Bar 12:    Swing High #1 (price_high=115, DVL=2000)
    Bar 28:    Swing High #2 (price_high=120 > 115 → HH, DVL=1800 < 2000 → LH)
    → Expected: Bearish Divergence at bar 28
    """
    n = 40
    dates = pd.date_range('2024-01-01', periods=n, freq='B')

    # Construct smooth price series with controlled swings
    close = np.full(n, 100.0)
    highs = np.full(n, 102.0)
    lows = np.full(n, 98.0)

    # Swing Low #1 at bar 10
    lows[10] = 90.0
    close[10] = 92.0
    for i in range(5, 10):
        lows[i] = 91 + (10 - i) * 1.5  # descending: 98.5, 97, 95.5, 94, 92.5
    for i in range(11, 16):
        lows[i] = 91 + (i - 10) * 1.5  # ascending: 92.5, 94, 95.5, 97, 98.5

    # Swing Low #2 at bar 25 — Lower Low than bar 10
    lows[25] = 85.0
    close[25] = 87.0
    for i in range(20, 25):
        lows[i] = 85 + (25 - i) * 2.0  # descending into the low
    for i in range(26, 31):
        lows[i] = 85 + (i - 25) * 2.0  # ascending from the low

    # Swing High #1 at bar 12
    highs[12] = 115.0
    close[12] = 113.0
    for i in range(7, 12):
        highs[i] = 102 + (i - 7) * 2.6
    for i in range(13, 18):
        highs[i] = 115 - (i - 12) * 2.6

    # Swing High #2 at bar 28 — Higher High
    highs[28] = 120.0
    close[28] = 118.0
    for i in range(23, 28):
        highs[i] = 102 + (i - 23) * 3.6
    for i in range(29, 34):
        highs[i] = 120 - (i - 28) * 3.6

    # DVL: bullish div on lows (HL), bearish div on highs (LH)
    dvl = np.full(n, 1500.0)
    dvl[10] = 1000.0  # Low swing #1 DVL
    dvl[25] = 1500.0  # Low swing #2 DVL — Higher Low (1500 > 1000) → Bull Div
    dvl[12] = 2000.0  # High swing #1 DVL
    dvl[28] = 1800.0  # High swing #2 DVL — Lower High (1800 < 2000) → Bear Div

    df = pd.DataFrame({
        'price_open': close,
        'price_high': highs,
        'price_low': lows,
        'price_close': close,
        'dvl': dvl,
        'atr_50': np.full(n, 3.0),  # ATR=3 so swings of 5+ easily pass 0.5*ATR
    }, index=dates)

    return df


class TestBullDivergenceMarker:
    def test_detects_bull_divergence(self):
        """Price LL + DVL HL → bullish divergence at bar 25."""
        m = BullDivergenceMarker()
        df = _divergence_df()
        df = m.evaluate(df)

        # Bar 25 should have is_div_bull=True
        assert df.iloc[25]['is_div_bull'] == True

    def test_no_false_positives_on_flat(self):
        """Flat series should produce no divergences."""
        m = BullDivergenceMarker()
        df = _base_df(30)
        # Make DVL flat and positive
        df['dvl'] = 1000.0
        df = m.evaluate(df)
        assert df['is_div_bull'].sum() == 0

    def test_metadata(self):
        m = BullDivergenceMarker()
        meta = m.metadata()
        assert meta['id'] == 'div_bull'
        assert meta['color'] == '#26a69a'
        assert meta['shape'] == 'diamond'
        assert meta['position'] == 'belowBar'
        assert meta['screener_name'] == 'SCR: Div-Bull'

    def test_screen(self):
        m = BullDivergenceMarker()
        assert m.screen(pd.DataFrame(), pd.Series({'is_div_bull': True})) == True
        assert m.screen(pd.DataFrame(), pd.Series({'is_div_bull': False})) == False

    def test_agg_rules(self):
        m = BullDivergenceMarker()
        assert m.agg_rules() == {'is_div_bull': 'any'}


class TestBearDivergenceMarker:
    def test_detects_bear_divergence(self):
        """Price HH + DVL LH → bearish divergence at bar 28."""
        m = BearDivergenceMarker()
        df = _divergence_df()
        df = m.evaluate(df)

        # Bar 28 should have is_div_bear=True
        assert df.iloc[28]['is_div_bear'] == True

    def test_metadata(self):
        m = BearDivergenceMarker()
        meta = m.metadata()
        assert meta['id'] == 'div_bear'
        assert meta['color'] == '#ef5350'
        assert meta['position'] == 'aboveBar'
        assert meta['screener_name'] == 'SCR: Div-Bear'

    def test_screen(self):
        m = BearDivergenceMarker()
        assert m.screen(pd.DataFrame(), pd.Series({'is_div_bear': True})) == True
        assert m.screen(pd.DataFrame(), pd.Series({'is_div_bear': False})) == False


class TestBullConfirmationMarker:
    def test_metadata(self):
        m = BullConfirmationMarker()
        meta = m.metadata()
        assert meta['id'] == 'confirm_bull'
        assert meta['color'] == '#66bb6a'
        assert meta['screener_name'] == 'SCR: Confirm-Bull'

    def test_screen(self):
        m = BullConfirmationMarker()
        assert m.screen(pd.DataFrame(), pd.Series({'is_confirm_bull': True})) == True


class TestBearConfirmationMarker:
    def test_metadata(self):
        m = BearConfirmationMarker()
        meta = m.metadata()
        assert meta['id'] == 'confirm_bear'
        assert meta['color'] == '#ef9a9a'
        assert meta['screener_name'] == 'SCR: Confirm-Bear'

    def test_screen(self):
        m = BearConfirmationMarker()
        assert m.screen(pd.DataFrame(), pd.Series({'is_confirm_bear': True})) == True

