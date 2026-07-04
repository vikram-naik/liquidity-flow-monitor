import pytest
import numpy as np
from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState
from src.trading.signals.savgol_cts.exits.cwvap_guard import apply_cwvap_guard

def test_cwvap_guard_hybrid_exit_enabled_triggers_on_negative_momentum():
    """Verify hybrid active exit triggers when price is below CWVAP, PNL < 0, and momentum fades."""
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.hybrid_exit_enabled = True
    
    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0
    )
    
    st = SavgolCTSExitState()
    
    # Below CWVAP (close=95, cwvap=100)
    # PNL is negative (close=95 < entry=100)
    # Momentum is fading (fas=-0.1, psz_v=-0.1)
    row = {
        "close": 95.0,
        "cwvap": 100.0,
        "fas": -0.1,
        "psz_v": -0.1
    }
    
    reason, next_state = apply_cwvap_guard(row, trade, None, st.to_int(), cfg, [], 1)
    
    assert reason == ExitReason.CWVAP_EXHAUSTION

def test_cwvap_guard_hybrid_exit_enabled_ignores_positive_pnl():
    """Verify hybrid active exit ignores winning trades even if below CWVAP."""
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.hybrid_exit_enabled = True
    
    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=90.0,
        entry_idx=0, atr_at_entry=2.0
    )
    
    st = SavgolCTSExitState()
    
    # Below CWVAP (close=95, cwvap=100)
    # PNL is POSITIVE (close=95 > entry=90)
    # Momentum is fading (fas=-0.1, psz_v=-0.1)
    row = {
        "close": 95.0,
        "cwvap": 100.0,
        "fas": -0.1,
        "psz_v": -0.1
    }
    
    reason, next_state = apply_cwvap_guard(row, trade, None, st.to_int(), cfg, [], 1)
    
    # Should not initiate exit
    assert reason is None

def test_cwvap_guard_hybrid_exit_disabled():
    """Verify hybrid active exit does not trigger if disabled via config."""
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.hybrid_exit_enabled = False
    
    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0
    )
    
    st = SavgolCTSExitState()
    
    row = {
        "close": 95.0,
        "cwvap": 100.0,
        "fas": -0.1,
        "psz_v": -0.1
    }
    
    reason, next_state = apply_cwvap_guard(row, trade, None, st.to_int(), cfg, [], 1)
    
    assert reason is None


def test_cwvap_guard_cwc_slope_early_release():
    """Verify that a suppressed exit is released early when cwc_slope drops below threshold."""
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.cwc_slope_early_release_enabled = True
    cfg.cwvap_guard.cwc_slope_early_release_threshold = -0.01

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0
    )

    # State: Suppressed exit is active
    st = SavgolCTSExitState()
    st.exit_suppressed = True

    # Row features: Price > CWVAP, but cwc_slope is -0.015 (< -0.01)
    row = {
        "close": 105.0,
        "cwvap": 100.0,
        "cwc_slope": -0.015,
        "price_slope_z": 0.5,
        "cts": 0.5
    }

    reason, next_state = apply_cwvap_guard(row, trade, None, st.to_int(), cfg, [], 1)

    # Should be released early
    assert reason == ExitReason.CWC_SLOPE_EARLY_RELEASE
    next_st = SavgolCTSExitState.from_int(next_state)
    assert next_st.exit_suppressed is False


def test_cwvap_guard_cwc_slope_immediate_release():
    """Verify that an exit proposed on the current bar is released immediately if cwc_slope is below threshold."""
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.cwc_slope_early_release_enabled = True
    cfg.cwvap_guard.cwc_slope_early_release_threshold = -0.01

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0
    )

    st = SavgolCTSExitState()

    row = {
        "close": 105.0,
        "cwvap": 100.0,
        "cwc_slope": -0.015,
        "price_slope_z": 0.5,
        "cts": 0.5
    }

    reason, next_state = apply_cwvap_guard(row, trade, ExitReason.ST_CROSS, st.to_int(), cfg, [], 1)

    # Should not be suppressed; should be released immediately with the proposed reason
    assert reason == ExitReason.ST_CROSS
    next_st = SavgolCTSExitState.from_int(next_state)
    assert next_st.exit_suppressed is False


def test_cwvap_guard_cwc_slope_early_release_bypassed_if_cts_above_st():
    """Verify that early release is NOT triggered when cts is above or equal to cts_sell_threshold."""
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.cwc_slope_early_release_enabled = True
    cfg.cwvap_guard.cwc_slope_early_release_threshold = -0.01

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0
    )

    # Scenario 1: CTS (0.8) is strictly above ST (0.5)
    st1 = SavgolCTSExitState()
    st1.exit_suppressed = True
    row1 = {
        "close": 105.0,
        "cwvap": 100.0,
        "cwc_slope": -0.015,
        "price_slope_z": 0.5,
        "cts": 0.8,
        "cts_sell_threshold": 0.5
    }
    reason1, next_state1 = apply_cwvap_guard(row1, trade, None, st1.to_int(), cfg, [], 1)
    assert reason1 is None
    assert SavgolCTSExitState.from_int(next_state1).exit_suppressed is True

    # Scenario 2: CTS (0.5) is exactly equal to ST (0.5)
    st2 = SavgolCTSExitState()
    st2.exit_suppressed = True
    row2 = {
        "close": 105.0,
        "cwvap": 100.0,
        "cwc_slope": -0.015,
        "price_slope_z": 0.5,
        "cts": 0.5,
        "cts_sell_threshold": 0.5
    }
    reason2, next_state2 = apply_cwvap_guard(row2, trade, None, st2.to_int(), cfg, [], 1)
    assert reason2 is None
    assert SavgolCTSExitState.from_int(next_state2).exit_suppressed is True


def test_cwvap_guard_va_high_suppression():
    """Verify that structural exits are suppressed when price is above VA High."""
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.va_high_breakout_suppression_enabled = True

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0
    )

    st = SavgolCTSExitState()
    row = {
        "close": 110.0,
        "cwvap": 102.0,
        "va_high": 105.0,  # close > va_high (breakout!)
        "price_slope_z": -0.1,  # fading momentum
        "cts": -0.1
    }

    # PRT_ST_CROSS should normally be triggered/released, but since close > va_high, it should be suppressed
    reason, next_state = apply_cwvap_guard(row, trade, ExitReason.PRT_ST_CROSS, st.to_int(), cfg, [], 1)
    
    assert reason is None
    next_st = SavgolCTSExitState.from_int(next_state)
    assert next_st.exit_suppressed is True
    assert next_st.suppressed_this_bar is True


def test_cwvap_guard_va_high_bypass():
    """Verify that critical bypass reasons (like HARD_STOP) are NOT suppressed above VA High."""
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.va_high_breakout_suppression_enabled = True

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=120.0,
        entry_idx=0, atr_at_entry=2.0
    )

    st = SavgolCTSExitState()
    row = {
        "close": 110.0,
        "cwvap": 102.0,
        "va_high": 105.0,  # close > va_high
        "price_slope_z": -0.1,
        "cts": -0.1
    }

    # HARD_STOP is a capital protection rule and must bypass suppression
    reason, next_state = apply_cwvap_guard(row, trade, ExitReason.HARD_STOP, st.to_int(), cfg, [], 1)
    
    assert reason == ExitReason.HARD_STOP


# ---------------------------------------------------------------------------
# Regression tests for climax trail touch-and-go VA High fix
# Pinned to the ADANIPORTS 2026-04-01 entry / 2026-04-24 near-exit scenario.
# ---------------------------------------------------------------------------

def test_st_cross_not_bypassed_when_climax_hit_above_va():
    """
    Regression: When climax_hit_above_va=True, ST_CROSS must NOT bypass CWVAP
    suppression. Previously it was unconditionally added to the bypass list
    whenever cts < cts_sell_threshold, causing an early exit even while the
    climax trail was committed to holding above VA High.

    ADANIPORTS 2026-04-24: cts=0.9912 < cts_st=1.0 fired ST_CROSS, but price
    was well above CWVAP (1585 vs 1499) with climax_hit_above_va=True and the
    bar's high (1618.80) above VA High (1589.35).
    """
    cfg = SavgolCTSExitConfig()

    trade = Trade(
        symbol="ADANIPORTS", entry_date="2026-04-02", entry_price=1354.4,
        entry_idx=10, atr_at_entry=35.0
    )

    st = SavgolCTSExitState()
    st.climax_hit_above_va = True
    st.exit_suppressed = True

    row = {
        "close": 1585.10,
        "high": 1618.80,
        "low": 1556.50,
        "open": 1612.00,
        "cwvap": 1498.56,
        "va_high": 1589.35,
        "cts": 0.9912,
        "cts_sell_threshold": 1.0,
        "price_slope_z": 0.315,
        "cwc_slope": 0.058,
    }

    reason, _ = apply_cwvap_guard(
        row, trade, ExitReason.ST_CROSS, st.to_int(), cfg, None, 20
    )

    assert reason is None, (
        f"ST_CROSS should be suppressed when climax_hit_above_va=True and "
        f"intraday high ({row['high']}) > va_high ({row['va_high']}), got {reason}"
    )


def test_climax_trail_not_released_on_intraday_wick_below_va_high():
    """
    Regression: When climax_hit_above_va=True and close dips just below va_high
    but the bar's HIGH was above va_high (intraday wick), the trail release must
    be suppressed — the bar did not genuinely break below VA High.
    """
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.climax_va_intraday_guard_enabled = True

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0
    )

    st = SavgolCTSExitState()
    st.climax_hit_above_va = True

    row = {
        "close": 109.0,
        "high": 112.0,       # touched above VA High intraday
        "low": 107.0,
        "cwvap": 100.0,
        "va_high": 110.0,
        "cts": 0.90,
        "cts_sell_threshold": 1.0,
        "price_slope_z": 0.3,
        "cwc_slope": 0.05,
    }

    reason, _ = apply_cwvap_guard(row, trade, None, st.to_int(), cfg, None, 5)

    assert reason is None, (
        f"Climax trail should NOT be released when high ({row['high']}) > va_high "
        f"({row['va_high']}) even if close ({row['close']}) < va_high. Got {reason}"
    )


def test_climax_trail_not_released_when_cts_at_maximum():
    """
    Regression: When climax_hit_above_va=True, close < va_high, but CTS == 1.0,
    the trail must stay alive — exiting at peak momentum leaves significant gains.
    """
    cfg = SavgolCTSExitConfig()

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0
    )

    st = SavgolCTSExitState()
    st.climax_hit_above_va = True

    row = {
        "close": 108.0,
        "high": 109.5,       # entire bar below va_high — intraday guard inactive
        "low": 106.0,
        "cwvap": 100.0,
        "va_high": 110.0,
        "cts": 1.0,           # peak momentum
        "cts_sell_threshold": 1.0,
        "price_slope_z": 0.2,
        "cwc_slope": -0.005,
    }

    reason, _ = apply_cwvap_guard(row, trade, None, st.to_int(), cfg, None, 5)

    assert reason is None, (
        f"Climax trail should NOT be released when cts ({row['cts']}) == 1.0, "
        f"even though close ({row['close']}) < va_high ({row['va_high']}). Got {reason}"
    )


def test_climax_trail_released_when_full_bar_below_va_high_and_cts_fading():
    """
    Complement: When the full bar (including high) is below va_high AND CTS has
    faded below 1.0, the climax trail SHOULD release as STRUCTURAL_CLIMAX.
    Ensures the new guards don't over-suppress genuine breakdowns.
    """
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.climax_va_intraday_guard_enabled = True

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0
    )

    st = SavgolCTSExitState()
    st.climax_hit_above_va = True

    row = {
        "close": 108.0,
        "high": 109.0,       # below va_high=110 — intraday guard inactive
        "low": 106.0,
        "cwvap": 100.0,
        "va_high": 110.0,
        "cts": 0.80,          # faded, not at peak
        "cts_sell_threshold": 1.0,
        "price_slope_z": -0.1,
        "cwc_slope": -0.02,
    }

    reason, _ = apply_cwvap_guard(row, trade, None, st.to_int(), cfg, None, 5)

    assert reason == ExitReason.STRUCTURAL_CLIMAX, (
        f"Climax trail SHOULD be released when full bar is below va_high and "
        f"cts ({row['cts']}) < 1.0. Got {reason}"
    )


def test_parabolic_low_break_coherence_suppression():
    """Verify that parabolic low break exit is suppressed when coherence is high."""
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.expert_exits_enabled = True
    cfg.cwvap_guard.cwc_slope_early_release_enabled = False
    cfg.cwvap_guard.peak_pnl_trigger = 10.0
    cfg.cwvap_guard.parabolic_cwc_min = 0.35
    cfg.cwvap_guard.overextended_rp_threshold = 0.90
    cfg.cwvap_guard.uptrend_low_break_buffer_atr = 0.30

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0
    )

    st = SavgolCTSExitState()
    st.exit_suppressed = True  # suppressed by CWVAP

    # 1. overextended in the last 4 bars (RP10=0.95)
    # 2. is_uptrend (regime='uptrend')
    # 3. low break (close < prev_low - buffer)
    #    close=108.0, prev_low=110.0, atr=2.0, buffer=0.3*2.0=0.6, trigger=109.4. 108.0 < 109.4
    # 4. BUT high coherence (cwc=0.40 >= 0.35) -> Should suppress exit
    row = {
        "close": 108.0,
        "low": 108.0,
        "cwvap": 105.0,
        "va_high": 102.0,
        "regime": "uptrend",
        "range_pos_10": 0.95,
        "atr_20": 2.0,
        "cwc": 0.40,
        "cwc_slope": -0.05,
        "cts": 0.5,
        "price_slope_z": 0.5
    }

    records = [
        {"close": 112.0, "low": 110.0, "range_pos_10": 0.95},  # prev bar with high close to trigger peak_pnl
        row                                    # current bar
    ]

    reason, next_state = apply_cwvap_guard(row, trade, None, st.to_int(), cfg, records, 1)

    assert reason is None
    next_st = SavgolCTSExitState.from_int(next_state)
    assert next_st.exit_suppressed is True


def test_parabolic_low_break_coherence_no_suppression():
    """Verify that parabolic low break exit triggers when coherence is low."""
    cfg = SavgolCTSExitConfig()
    cfg.cwvap_guard.expert_exits_enabled = True
    cfg.cwvap_guard.cwc_slope_early_release_enabled = False
    cfg.cwvap_guard.peak_pnl_trigger = 10.0
    cfg.cwvap_guard.parabolic_cwc_min = 0.35
    cfg.cwvap_guard.parabolic_cwc_slope_min = -0.02
    cfg.cwvap_guard.overextended_rp_threshold = 0.90
    cfg.cwvap_guard.uptrend_low_break_buffer_atr = 0.30

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0
    )

    st = SavgolCTSExitState()
    st.exit_suppressed = True

    # 1. overextended in the last 4 bars (RP10=0.95)
    # 2. is_uptrend (regime='uptrend')
    # 3. low break (close=108.0 < 109.4)
    # 4. AND low coherence (cwc=0.30 < 0.35, cwc_slope=-0.05 < -0.02) -> Should exit
    row = {
        "close": 108.0,
        "low": 108.0,
        "cwvap": 105.0,
        "va_high": 102.0,
        "regime": "uptrend",
        "range_pos_10": 0.95,
        "atr_20": 2.0,
        "cwc": 0.30,
        "cwc_slope": -0.05,
        "cts": 0.5,
        "price_slope_z": 0.5
    }

    records = [
        {"close": 112.0, "low": 110.0, "range_pos_10": 0.95},  # prev bar with high close to trigger peak_pnl
        row                                    # current bar
    ]

    reason, next_state = apply_cwvap_guard(row, trade, None, st.to_int(), cfg, records, 1)

    assert reason == ExitReason.EXPERT5_UPTREND_PARABOLIC_LOW_BREAK
    next_st = SavgolCTSExitState.from_int(next_state)
    assert next_st.exit_suppressed is False

