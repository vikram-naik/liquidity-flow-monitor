import pandas as pd
import numpy as np
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.analysis.markers.grind import GrindMarker
from src.analysis.markers.bearish_grind import BearishGrindMarker

def create_base_df(n=10):
    df = pd.DataFrame({
        'price_open': [100.0] * n,
        'price_high': [102.0] * n,
        'price_low': [98.0] * n,
        'price_close': [101.0] * n,
        'prev_close': [100.0] * n,
        'davwap': [100.0] * n,
        'atr_50': [2.0] * n,
        'mfm': [0.1] * n,
        'dvl_slope_5': [1.0] * n,
        'mcs': [0.5] * n,
        'is_ignition': [False] * n,
        'is_exhaustion': [False] * n,
        'is_bearish_absorption': [False] * n,
        'is_spring': [False] * n,
        'is_distribution': [False] * n,
        'is_coil': [False] * n
    })
    return df

class TestGrindSequencing:
    def test_g1_g2_g3_successful_sequence(self):
        """Verify standard 3-day progression."""
        marker = GrindMarker()
        df = create_base_df(10)
        atr = 2.0
        
        # Day 1 (idx 5): Expansion >= 0.5 ATR (1.0)
        df.at[5, 'price_close'] = 101.5
        # Day 2 (idx 6): Close > Prev, CumExp >= 0.8 ATR (1.6)
        df.at[6, 'price_close'] = 102.0 # 102.0 - 100.0 = 2.0 (>= 1.6)
        # Day 3 (idx 7): Close > Prev, CumExp >= 1.2 ATR (2.4), MCS > shift(3)
        df.at[7, 'price_close'] = 103.0 # 103.0 - 100.0 = 3.0 (>= 2.4)
        df.at[7, 'mcs'] = 0.8
        df.at[4, 'mcs'] = 0.4 # mcs_prev3
        
        df = marker.evaluate(df)
        
        assert df.at[5, 'grind_level'] == 1
        assert df.at[6, 'grind_level'] == 2
        assert df.at[7, 'grind_level'] == 3

    def test_g1_suppressed_by_priority_prevents_g2_at_live_edge(self):
        """
        Verify that if G1 is suppressed by an Ignition marker at the live edge,
        G2 should NOT appear when it becomes the live edge.
        """
        marker = GrindMarker()
        df = create_base_df(2)
        atr = 2.0
        
        # Day 1 (idx 0): Qualifies for Grind BUT has Ignition
        # Expansion: 101.5 - 100.0 = 1.5 (0.75 ATR)
        df.at[0, 'price_close'] = 101.5
        df.at[0, 'is_ignition'] = True
        
        # Day 2 (idx 1): Qualifies for G2 based on Day 1 price
        # Price: 101.7
        # G2 check: 101.7 - 100.0 (prev origin) = 1.7 (0.85 ATR >= 0.8) -> PASS if G1 was ok
        # G1 check: 101.7 - 101.5 (current origin) = 0.2 (0.1 ATR < 0.5) -> FAIL
        df.at[1, 'price_open'] = 101.5
        df.at[1, 'prev_close'] = 101.5
        df.at[1, 'price_close'] = 101.7
        
        df = marker.evaluate(df)
        
        # Day 1 should be 0 because of Ignition
        assert df.at[0, 'grind_level'] == 0
        # Day 2 SHOULD be 0 because G1 was suppressed AND it doesn't qualify as G1 itself
        assert df.at[1, 'grind_level'] == 0, "G2 should not trigger if G1 was suppressed at live edge"

    def test_ghosting_cleanup_if_g3_fails(self):
        """
        Verify that if we are at the live edge and Day 3 fails, 
        G1 and G2 are removed (ghosted).
        """
        marker = GrindMarker()
        df = create_base_df(3)
        
        # Day 1 (idx 0)
        df.at[0, 'price_close'] = 101.5
        # Day 2 (idx 1)
        df.at[1, 'price_open'] = 101.5
        df.at[1, 'prev_close'] = 101.5
        df.at[1, 'price_close'] = 101.7 
        # Day 3 (idx 2): Fails G3 completion (price drops)
        df.at[2, 'price_open'] = 101.7
        df.at[2, 'prev_close'] = 101.7
        df.at[2, 'price_close'] = 101.0
        
        # First check what happens after Day 2 (Live Edge)
        df_live = marker.evaluate(df.iloc[:2].copy())
        assert df_live.at[0, 'grind_level'] == 1
        assert df_live.at[1, 'grind_level'] == 2
        
        # Now check after Day 3 fails
        df_final = marker.evaluate(df.copy())
        assert df_final.at[0, 'grind_level'] == 0
        assert df_final.at[1, 'grind_level'] == 0
        assert df_final.at[2, 'grind_level'] == 0


class TestBearishGrindSequencing:
    def test_bg1_bg2_bg3_successful_sequence(self):
        """Verify standard 3-day progression for bearish grind."""
        marker = BearishGrindMarker()
        df = create_base_df(10)
        atr = 2.0
        
        # Day 1 (idx 5): Downward Expansion >= 0.5 ATR (1.0)
        df.at[5, 'price_close'] = 98.5
        df.at[5, 'mfm'] = -0.1
        # Day 2 (idx 6): Close < Prev, Down CumExp >= 0.8 ATR (1.6)
        df.at[6, 'price_close'] = 98.0 # 100.0 - 98.0 = 2.0 (>= 1.6)
        # Day 3 (idx 7): Close < Prev, Down CumExp >= 1.2 ATR (2.4), MCS < shift(3)
        df.at[7, 'price_close'] = 97.0 # 100.0 - 97.0 = 3.0 (>= 2.4)
        df.at[7, 'mcs'] = 0.2
        df.at[4, 'mcs'] = 0.5 # mcs_prev3
        df.at[7, 'dvl_slope_5'] = -1.0
        
        df = marker.evaluate(df)
        
        assert df.at[5, 'bearish_grind_level'] == 1
        assert df.at[6, 'bearish_grind_level'] == 2
        assert df.at[7, 'bearish_grind_level'] == 3

    def test_bg1_suppressed_by_priority_prevents_bg2(self):
        """Verify that bg2 is suppressed if bg1 was suppressed."""
        marker = BearishGrindMarker()
        df = create_base_df(10)
        
        # Day 1 (idx 5): Qualifies for Bearish Grind BUT has Distribution
        df.at[5, 'price_close'] = 98.5
        df.at[5, 'mfm'] = -0.1
        df.at[5, 'is_distribution'] = True
        
        # Day 2 (idx 6): Qualifies for BG2
        df.at[6, 'price_close'] = 98.0
        
        df = marker.evaluate(df)
        
        assert df.at[5, 'bearish_grind_level'] == 0
        assert df.at[6, 'bearish_grind_level'] == 0, "BG2 should not trigger if BG1 was suppressed"
