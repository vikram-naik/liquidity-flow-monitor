"""Unit tests for PriceRange basing detection logic.

Tests that basing detection correctly differentiates between:
- Flat consolidation at the bottom (true base)
- Falling through the oversold zone (false base / stair-stepping down)
- ATR-relative range narrowness

These tests use synthetic OHLC data to isolate the basing logic
from live data dependencies.
"""

import numpy as np
import pandas as pd
import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.price_range import PriceRange


def _make_ohlc(closes: list[float], spread_pct: float = 0.5) -> pd.DataFrame:
    """Build a synthetic OHLC DataFrame from a list of close prices.

    Each bar gets high = close * (1 + spread_pct/100) and
    low = close * (1 - spread_pct/100), simulating a tight daily range.
    """
    closes = np.array(closes, dtype=float)
    highs = closes * (1 + spread_pct / 100)
    lows = closes * (1 - spread_pct / 100)
    return pd.DataFrame({
        "high": highs,
        "low": lows,
        "close": closes,
    })


class TestBarsAtBase:
    """bars_at_base: consecutive bars where range_pos_252 < 0.25."""

    def test_flat_base_accumulates(self):
        """Price sits flat near the 252-day low → bars_at_base grows."""
        # 252 bars of data: first 200 at 100, then drop to 80 and stay flat
        prices = [100.0] * 200 + [80.0] * 60
        df = _make_ohlc(prices, spread_pct=0.3)
        prng = PriceRange()
        df = prng.compute_all(df)

        # Last 50 bars should all be deeply oversold with growing bars_at_base
        tail = df.iloc[-50:]
        assert (tail["range_pos_252"] < 0.25).all(), "Should be oversold"
        assert tail["bars_at_base"].iloc[-1] >= 50, (
            f"Expected 50+ bars at base, got {tail['bars_at_base'].iloc[-1]}"
        )
        # bars_at_base should be monotonically increasing during flat base
        diffs = tail["bars_at_base"].diff().dropna()
        assert (diffs == 1).all(), "bars_at_base should increment by 1 each bar in flat base"

    def test_falling_through_oversold_still_accumulates(self):
        """Price falling through oversold zone still accumulates bars_at_base.

        This is the CURRENT behavior — bars_at_base only checks range_pos_252 < 0.25,
        it does NOT check if price is flat. This test documents the behavior that
        the study script must guard against with ATR-relative range checks.
        """
        # Sharp drop: 100 for 200 bars, then stair-step down
        prices = [100.0] * 200
        # Stair-step from 80 → 60 over 60 bars (falling, not flat)
        for i in range(60):
            prices.append(80.0 - i * (20.0 / 60))
        df = _make_ohlc(prices, spread_pct=0.3)
        prng = PriceRange()
        df = prng.compute_all(df)

        # bars_at_base accumulates even during the fall
        last = df.iloc[-1]
        assert last["bars_at_base"] > 0, (
            "bars_at_base accumulates during a fall through oversold — "
            "this is expected; study script must add its own range-width guard"
        )

    def test_resets_on_exit_from_oversold(self):
        """bars_at_base resets to 0 when price exits oversold territory."""
        # Drop to oversold, sit for a while, then recover
        prices = [100.0] * 252 + [75.0] * 20 + [100.0] * 10
        df = _make_ohlc(prices, spread_pct=0.3)
        prng = PriceRange()
        df = prng.compute_all(df)

        # During oversold period, bars_at_base should grow
        oversold_end = df.iloc[271]  # last bar of oversold period
        assert oversold_end["bars_at_base"] >= 15

        # After recovery, bars_at_base should reset
        recovered = df.iloc[-1]
        assert recovered["bars_at_base"] == 0, (
            f"Expected bars_at_base=0 after recovery, got {recovered['bars_at_base']}"
        )

    def test_zero_when_not_oversold(self):
        """Price trending up → bars_at_base stays 0."""
        prices = [50.0 + i * 0.2 for i in range(300)]
        df = _make_ohlc(prices, spread_pct=0.3)
        prng = PriceRange()
        df = prng.compute_all(df)

        assert (df["bars_at_base"] == 0).all()


class TestBaseTightness:
    """base_tightness: range_width_10 / range_width_63."""

    def test_tight_consolidation(self):
        """Flat price → range_width_10 ≈ range_width_63 → tightness ≈ 1.0."""
        prices = [100.0] * 100
        df = _make_ohlc(prices, spread_pct=0.5)
        prng = PriceRange()
        df = prng.compute_all(df)

        last = df.iloc[-1]
        assert 0.8 <= last["base_tightness"] <= 1.2, (
            f"Flat price should have tightness near 1.0, got {last['base_tightness']:.2f}"
        )

    def test_recent_compression_after_drop(self):
        """Big drop then flat → range_width_10 << range_width_63 → tightness < 0.3."""
        # 63-bar window sees the full drop; 10-bar window sees only the flat part
        prices = [100.0] * 50 + [80.0 - i for i in range(20)] + [60.0] * 30
        df = _make_ohlc(prices, spread_pct=0.3)
        prng = PriceRange()
        df = prng.compute_all(df)

        last = df.iloc[-1]
        assert last["base_tightness"] < 0.30, (
            f"Recent compression after drop should give tightness < 0.30, got {last['base_tightness']:.2f}"
        )


class TestRangeWidthAsBasingProxy:
    """Verify range_width_10 correctly reflects basing quality.

    The study uses range_width_10 (in ATR terms) to distinguish between:
    - A flat base (narrow 10d range relative to ATR)
    - A falling knife passing through oversold (wide range relative to ATR)
    """

    def test_flat_base_narrow_range(self):
        """Flat prices → range_width_10 is small."""
        prices = [100.0] * 252 + [75.0] * 20  # flat at bottom
        df = _make_ohlc(prices, spread_pct=0.3)
        prng = PriceRange()
        df = prng.compute_all(df)

        last = df.iloc[-1]
        # range_width_10 = (high_10 - low_10) / close * 100
        # With spread_pct=0.3, high/low are ±0.3% from close
        # so range should be roughly 0.6%
        assert last["range_width_10"] < 1.0, (
            f"Flat base should have narrow range_width_10, got {last['range_width_10']:.2f}%"
        )

    def test_falling_knife_wide_range(self):
        """Stair-stepping down → range_width_10 is large."""
        prices = [100.0] * 252
        # Sharp 10-bar drop: 75 → 60
        for i in range(10):
            prices.append(75.0 - i * 1.5)
        df = _make_ohlc(prices, spread_pct=0.3)
        prng = PriceRange()
        df = prng.compute_all(df)

        last = df.iloc[-1]
        # 15-point drop over 10 bars on a ~65 close = ~23% range
        assert last["range_width_10"] > 10.0, (
            f"Falling knife should have wide range_width_10, got {last['range_width_10']:.2f}%"
        )

    def test_titan_scenario_volatile_false_base(self):
        """Simulate TITAN-like scenario: volatile stock with wide daily range
        passing through oversold zone. bars_at_base accumulates but
        range_width_10 remains wide relative to the stock's volatility.

        TITAN 2025-02-27: ATR ~76, close ~3223, rw_10 = 4.56%,
        rw10_in_ATRs = 1.93. This was NOT a proper base.
        """
        # Simulate: high-vol stock, 252 bars at 3500, then declining
        np.random.seed(42)
        base_prices = [3500.0] * 220
        # Decline from 3500 → 3200 with high daily volatility
        for i in range(40):
            base_prices.append(3500.0 - i * 7.5 + np.random.randn() * 30)
        df = _make_ohlc(base_prices, spread_pct=1.5)  # 1.5% daily spread = high vol
        prng = PriceRange()
        df = prng.compute_all(df)

        last = df.iloc[-1]
        # Should be in oversold territory with bars_at_base > 0
        assert last["bars_at_base"] > 5, "Should have bars_at_base in oversold"
        # But range_width_10 should be wide (not a tight base)
        assert last["range_width_10"] > 3.0, (
            f"Volatile stock in decline should have wide rw_10, got {last['range_width_10']:.2f}%"
        )
