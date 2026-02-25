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
        assert m.metadata()['screener_name'] is None

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
        latest = pd.Series({'ignition_score': 60, 'coil_score': 70})
        assert m.screen(pd.DataFrame(), latest) == False
