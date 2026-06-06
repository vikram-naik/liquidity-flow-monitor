import pytest
import numpy as np
from src.trading.signals.base import Trade
from src.trading.signals.enums import EntryTag, ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
old_init = SavgolCTSEntryConfig.__init__
SavgolCTSEntryConfig.__init__ = lambda self, *args, **kwargs: (old_init(self, *args, **kwargs), setattr(self.universal_cross, "bayesian_mode", False), None)[2]
from src.trading.signals.savgol_cts.entries.universal_cross import entry_universal_cross
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross
from src.trading.signals.savgol_cts.state import SavgolCTSExitState


def get_base_records():
    """Returns a list of records that will pass all checks and trigger entry on CTS slope."""
    return [
        {
            "high": 100.0, "low": 98.0, "close": 99.0, "atr_20": 2.0,
            "cts_slope": -0.1, "cts_accel": 0.02, "cts_accel_threshold": 0.0,
            "psz_v": 1.0, "fas": -0.9, "fas_buy_threshold": -0.8,
            "range_pos_10": 0.2, "base_tightness": 0.8, "pdd_30": 0.0,
            "range_width_10": 10.0, "rdv": 1.0
        },
        {
            "high": 101.0, "low": 99.0, "close": 100.0, "atr_20": 2.0,
            "cts_slope": -0.05, "cts_accel": 0.04, "cts_accel_threshold": 0.0,
            "psz_v": 2.0, "fas": -0.85, "fas_buy_threshold": -0.8,
            "range_pos_10": 0.2, "base_tightness": 0.8, "pdd_30": 0.0,
            "range_width_10": 10.0, "rdv": 1.0
        },
        {
            "high": 102.0, "low": 100.0, "close": 101.0, "atr_20": 2.0,
            "cts_slope": 0.05, "cts_accel": 0.06, "cts_accel_threshold": 0.0,
            "psz_v": 3.0, "fas": -0.8, "fas_buy_threshold": -0.8,
            "range_pos_10": 0.2, "base_tightness": 0.8, "pdd_30": 0.0,
            "range_width_10": 10.0, "rdv": 1.0
        }
    ]


def test_universal_cross_entry_cts_slope_trigger():
    """Verify entry triggers on CTS slope crossing above 0 when all other conditions are met."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)

    assert passed is True
    assert score == 70
    assert meta["entry_tag"] == EntryTag.UNIVERSAL_CROSS.value
    assert meta["reason"] == "Universal Cross accepted"


def test_universal_cross_entry_fas_trigger():
    """Verify entry triggers on FAS crossing above fas_buy_threshold."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    # Remove CTS slope trigger, add FAS trigger
    records[1]["cts_slope"] = -0.1
    records[2]["cts_slope"] = -0.05  # No cross above 0

    records[1]["fas"] = -0.9
    records[2]["fas"] = -0.7  # Crosses above fas_buy_threshold = -0.8

    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)

    assert passed is True
    assert score == 70


def test_universal_cross_entry_rejection_no_inflection():
    """Verify entry is rejected when there is no structural inflection (no trigger)."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    records[1]["cts_slope"] = 0.05
    records[2]["cts_slope"] = 0.10  # No cross above 0

    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)

    assert passed is False
    assert "No structural inflection" in meta["reason"]


def test_universal_cross_entry_rejection_low_accel():
    """Verify entry is rejected when acceleration is below threshold."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    records[2]["cts_accel"] = -0.01  # below threshold 0.0
    records[2]["cts_accel_threshold"] = 0.0

    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)

    assert passed is False
    assert "cts_accel below threshold" in meta["reason"]


def test_universal_cross_entry_rejection_accel_not_rising():
    """Verify entry is rejected when acceleration is not rising."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    records[2]["cts_accel"] = 0.03  # dropped from 0.04 in prev record

    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)

    assert passed is False
    assert "cts_accel not rising" in meta["reason"]


def test_universal_cross_entry_rejection_psz_v_not_positive():
    """Verify entry is rejected when PSZ velocity is not positive."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    records[2]["psz_v"] = 0.0  # not positive

    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)

    assert passed is False
    assert "psz_v not positive" in meta["reason"]


def test_universal_cross_entry_rejection_psz_v_not_rising():
    """Verify entry is rejected when PSZ velocity is not rising."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    records[2]["psz_v"] = 1.5  # dropped from 2.0 in prev record

    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)

    assert passed is False
    assert "psz_v not rising" in meta["reason"]


def test_universal_cross_entry_rejection_fas_not_rising():
    """Verify entry is rejected when FAS is not rising."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    # Trigger on cts_slope
    records[0]["fas"] = -0.7
    records[1]["fas"] = -0.7
    records[2]["fas"] = -0.75  # dropped, and above fas_buy_threshold (-0.8)

    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)

    assert passed is False
    assert "fas not rising" in meta["reason"]


def test_universal_cross_entry_rejection_fas_above_high_threshold():
    """Verify entry is rejected when FAS is above the high threshold (0.1)."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    records[2]["fas"] = 0.15  # above 0.1

    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)

    assert passed is False
    assert "fas above high threshold" in meta["reason"]


def test_universal_cross_entry_rejection_weekly_range():
    """Verify entry is rejected when price is in the upper half of the weekly range (unless double trigger)."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    records[2]["range_pos_10"] = 0.6  # in upper half (>0.5)

    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)

    assert passed is False
    assert "price not in lower half of weekly range" in meta["reason"]


def test_universal_cross_entry_double_trigger_bypasses_range():
    """Verify that a double trigger (both CTS slope & FAS) bypasses the weekly range position limit."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    records[2]["range_pos_10"] = 0.6  # in upper half

    # Set both triggers
    records[1]["cts_slope"] = -0.1
    records[2]["cts_slope"] = 0.05  # CTS slope triggers

    records[1]["fas"] = -0.9
    records[2]["fas"] = -0.7  # FAS triggers

    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)

    assert passed is True
    assert score == 70


def test_universal_cross_entry_rejection_gap_down():
    """Verify entry is rejected when there is a recent gap down."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    # Add a gap down: prev_low > current_high AND gap > 0.3 * ATR
    records[1]["low"] = 100.0
    records[2]["high"] = 99.0

    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)

    assert passed is False
    assert "recent gap down detected" in meta["reason"]


def test_universal_cross_entry_rejection_flat_slope():
    """Verify entry is rejected when the CTS slope has been flat recently (Spearman correlation evaluation)."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    # We need idx >= 4 to trigger the check_slope_flatness check
    records = [
        # Flat slope records: all slope values are identical
        {"high": 100.0, "low": 98.0, "close": 99.0, "atr_20": 2.0, "cts_slope": 0.01, "cts_accel": 0.01, "cts_accel_threshold": 0.0, "psz_v": 1.0, "fas": -0.9, "fas_buy_threshold": -0.8, "range_pos_10": 0.2, "base_tightness": 0.8, "pdd_30": 0.0, "range_width_10": 10.0},
        {"high": 100.0, "low": 98.0, "close": 99.0, "atr_20": 2.0, "cts_slope": 0.01, "cts_accel": 0.02, "cts_accel_threshold": 0.0, "psz_v": 1.5, "fas": -0.9, "fas_buy_threshold": -0.8, "range_pos_10": 0.2, "base_tightness": 0.8, "pdd_30": 0.0, "range_width_10": 10.0},
        {"high": 100.0, "low": 98.0, "close": 99.0, "atr_20": 2.0, "cts_slope": 0.01, "cts_accel": 0.03, "cts_accel_threshold": 0.0, "psz_v": 2.0, "fas": -0.9, "fas_buy_threshold": -0.8, "range_pos_10": 0.2, "base_tightness": 0.8, "pdd_30": 0.0, "range_width_10": 10.0},
        {"high": 101.0, "low": 99.0, "close": 100.0, "atr_20": 2.0, "cts_slope": -0.05, "cts_accel": 0.04, "cts_accel_threshold": 0.0, "psz_v": 2.5, "fas": -0.85, "fas_buy_threshold": -0.8, "range_pos_10": 0.2, "base_tightness": 0.8, "pdd_30": 0.0, "range_width_10": 10.0},
        {"high": 102.0, "low": 100.0, "close": 101.0, "atr_20": 2.0, "cts_slope": 0.05, "cts_accel": 0.05, "cts_accel_threshold": 0.0, "psz_v": 3.0, "fas": -0.8, "fas_buy_threshold": -0.8, "range_pos_10": 0.2, "base_tightness": 0.8, "pdd_30": 0.0, "range_width_10": 10.0}
    ]

    passed, score, meta = entry_universal_cross(records[4], records[3], cfg, records, 4)

    assert passed is False
    assert "slope has been flat recently" in meta["reason"]


def test_universal_cross_entry_rejection_basing():
    """Verify entry is rejected when the price is in a basing pattern."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = [
        # Basing records: narrow, tight and flat typical prices
        {"high": 100.0, "low": 99.8, "close": 100.0, "atr_20": 2.0, "cts_slope": -0.1, "cts_accel": 0.01, "cts_accel_threshold": 0.0, "psz_v": 1.0, "fas": -0.9, "fas_buy_threshold": -0.8, "range_pos_10": 0.2, "base_tightness": 0.1, "pdd_30": 0.0, "range_width_10": 0.2},
        {"high": 100.0, "low": 99.8, "close": 100.0, "atr_20": 2.0, "cts_slope": -0.1, "cts_accel": 0.02, "cts_accel_threshold": 0.0, "psz_v": 1.5, "fas": -0.9, "fas_buy_threshold": -0.8, "range_pos_10": 0.2, "base_tightness": 0.1, "pdd_30": 0.0, "range_width_10": 0.2},
        {"high": 100.0, "low": 99.8, "close": 100.0, "atr_20": 2.0, "cts_slope": -0.1, "cts_accel": 0.03, "cts_accel_threshold": 0.0, "psz_v": 2.0, "fas": -0.9, "fas_buy_threshold": -0.8, "range_pos_10": 0.2, "base_tightness": 0.1, "pdd_30": 0.0, "range_width_10": 0.2},
        {"high": 100.0, "low": 99.8, "close": 100.0, "atr_20": 2.0, "cts_slope": -0.05, "cts_accel": 0.04, "cts_accel_threshold": 0.0, "psz_v": 2.5, "fas": -0.85, "fas_buy_threshold": -0.8, "range_pos_10": 0.2, "base_tightness": 0.1, "pdd_30": 0.0, "range_width_10": 0.2},
        {"high": 100.2, "low": 100.0, "close": 100.1, "atr_20": 2.0, "cts_slope": 0.05, "cts_accel": 0.05, "cts_accel_threshold": 0.0, "psz_v": 3.0, "fas": -0.8, "fas_buy_threshold": -0.8, "range_pos_10": 0.2, "base_tightness": 0.1, "pdd_30": 0.0, "range_width_10": 0.2}
    ]

    passed, score, meta = entry_universal_cross(records[4], records[3], cfg, records, 4)

    assert passed is False
    assert "price has been basing recently" in meta["reason"]


def test_universal_cross_entry_distribution_trap_rejection():
    """Verify that the entry is rejected under Distribution Trap conditions (pdd_30 <= -5.5 and 0.40 <= base_tightness <= 0.46)."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    records[2]["pdd_30"] = -6.0
    records[2]["base_tightness"] = 0.43

    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)

    assert passed is False
    assert "Distribution Trap" in meta["reason"]


# ---------------------------------------------------------------------------
# Exit Test Cases
# ---------------------------------------------------------------------------

def test_universal_cross_exit_prt_cross():
    """Verify exit on PRT crossing down through the PRT sell threshold."""
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0,
        entry_tag=EntryTag.UNIVERSAL_CROSS.value
    )

    # Crossover: prev_prt >= prev_prt_st, prt < prt_st
    row = {"close": 105.0, "prt": 0.49, "prt_sell_threshold": 0.50, "prt_buy_threshold": 0.0}
    prev_row = {"close": 104.0, "prt": 0.51, "prt_sell_threshold": 0.50, "prt_buy_threshold": 0.0}

    reason, _ = exit_universal_cross(row, prev_row, trade, 105.0, 5, 0, cfg)

    assert reason == ExitReason.PRT_ST_CROSS


def test_universal_cross_exit_panic_suppression():
    """Verify that a PRT crossover exit is suppressed under panic gap down conditions."""
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True
    cfg.panic_exit_suppression_enabled = True
    cfg.panic_exit_rdv_threshold = 2.0

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0,
        entry_tag=EntryTag.UNIVERSAL_CROSS.value
    )

    # Crossover condition is met, but physical gap down (high < prev_low) and high volume (rdv >= 2.0)
    row = {"close": 105.0, "prt": 0.49, "prt_sell_threshold": 0.50, "prt_buy_threshold": 0.0, "high": 99.0, "rdv": 2.5}
    prev_row = {"close": 104.0, "prt": 0.51, "prt_sell_threshold": 0.50, "prt_buy_threshold": 0.0, "low": 100.0}

    reason, _ = exit_universal_cross(row, prev_row, trade, 105.0, 5, 0, cfg)

    assert reason is None  # Suppressed!


def test_universal_cross_exit_hard_stop():
    """Verify hard stop exit."""
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True
    cfg.hard_stop_enabled = True
    cfg.hard_stop_pct = 8.0

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0,
        entry_tag=EntryTag.UNIVERSAL_CROSS.value
    )

    # Price dropped to 91 (-9%)
    row = {"close": 91.0}
    prev_row = {"close": 95.0}

    reason, _ = exit_universal_cross(row, prev_row, trade, 100.0, 1, 0, cfg)

    assert reason == ExitReason.HARD_STOP


def test_universal_cross_exit_negative_pnl_timeout():
    """Verify exit on negative PnL after timeout days."""
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True
    cfg.negative_pnl_timeout_enabled = True
    cfg.negative_pnl_timeout_days = 15

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0,
        entry_tag=EntryTag.UNIVERSAL_CROSS.value
    )

    # 1. Held for 14 days, PnL -1% -> Should NOT exit
    row_14 = {"close": 99.0}
    prev_row_14 = {"close": 99.0}
    reason_14, _ = exit_universal_cross(row_14, prev_row_14, trade, 100.0, 14, 0, cfg)
    assert reason_14 is None

    # 2. Held for 15 days, PnL -1% -> Should EXIT
    row_15 = {"close": 99.0}
    prev_row_15 = {"close": 99.0}
    reason_15, _ = exit_universal_cross(row_15, prev_row_15, trade, 100.0, 15, 0, cfg)
    assert reason_15 == ExitReason.NEGATIVE_PNL_TIMEOUT

def test_universal_cross_exit_pnl_cap():
    """Verify that exit triggers when PnL cap is hit."""
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True
    cfg.pnl_cap_enabled = True
    cfg.pnl_cap_threshold = 8.0

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0,
        entry_tag=EntryTag.UNIVERSAL_CROSS.value
    )

    # Peak close is 109.0 (> 8% PnL)
    row = {"close": 109.0}
    prev_row = {"close": 105.0}

    reason, _ = exit_universal_cross(row, prev_row, trade, 109.0, 5, 0, cfg)
    assert reason == ExitReason.PNL_CAP


def test_universal_cross_exit_gap_down_loss():
    """Verify that exit triggers on gap down while in loss."""
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True
    cfg.gap_down_enabled = True
    cfg.gap_down_atr_mult = 0.3

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0,
        entry_tag=EntryTag.UNIVERSAL_CROSS.value
    )

    # In loss: close is 95.0 (< entry=100.0)
    # Gap down: current high is 96.0 < prev low is 98.0 (gap of 2.0). ATR is 2.0.
    # gap of 2.0 > 0.3 * 2.0 (0.6)
    row = {"close": 95.0, "high": 96.0, "atr_20": 2.0}
    prev_row = {"close": 97.0, "low": 98.0}

    reason, _ = exit_universal_cross(row, prev_row, trade, 100.0, 5, 0, cfg)
    assert reason == ExitReason.GAP_DOWN_LOSS


def test_universal_cross_entry_rejection_fas_bt_out_of_range():
    """Verify entry is rejected when fas_buy_threshold is out of expected range (> -0.3)."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    records[2]["fas_buy_threshold"] = -0.2  # above -0.3

    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)

    assert passed is False
    assert "fas_bt not in expected range" in meta["reason"]


def test_signal_default_configs():
    """Verify that get_default_entry_config and get_default_exit_config return the expected config classes."""
    from src.trading.signals.savgol_cts.signal import SavgolCTSSignal
    from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
    
    sig = SavgolCTSSignal()
    entry_cfg = sig.get_default_entry_config()
    exit_cfg = sig.get_default_exit_config()
    
    assert isinstance(entry_cfg, SavgolCTSEntryConfig)
    assert isinstance(exit_cfg, SavgolCTSExitConfig)


def test_universal_cross_entry_rejection_gap_down_lookback_config():
    """Verify gap-down lookback configuration rejects or accepts based on the configured window."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    # Build 20 records with trending values to bypass flatness/basing gates
    base = get_base_records()[0]
    records = []
    for i in range(20):
        rec = base.copy()
        # Rising prices to prevent flat typical price basing
        rec["high"] = 100.0 + i
        rec["low"] = 98.0 + i
        rec["close"] = 99.0 + i
        # Trending slopes to prevent slope flatness gate
        rec["cts_slope"] = -1.0 + i * 0.05
        rec["cts_accel"] = 0.01 + i * 0.01
        rec["psz_v"] = 1.0 + i * 0.5
        rec["base_tightness"] = 0.8  # Not tight
        records.append(rec)
    
    # Trigger the CTS slope cross above 0 at index 15
    records[14]["cts_slope"] = -0.05
    records[15]["cts_slope"] = 0.02
    
    # Put a gap-down at index 3 (12 bars before index 15)
    # i.e., index 3 high < index 2 low
    records[2]["low"] = 110.0
    records[3]["high"] = 102.0
    records[3]["atr_20"] = 2.0  # gap size = 8.0 > 0.3 * 2.0 (0.6)
    
    # Test with default gap_down_lookback = 10
    cfg.universal_cross.gap_down_lookback = 10
    passed, score, meta = entry_universal_cross(records[15], records[14], cfg, records, 15)
    assert passed is True  # Should not be rejected because gap is 12 bars ago, outside lookback 10+1
    
    # Test with gap_down_lookback = 15
    cfg.universal_cross.gap_down_lookback = 15
    passed, score, meta = entry_universal_cross(records[15], records[14], cfg, records, 15)
    assert passed is False  # Rejected because lookback 15 covers index 3 (12 bars ago)
    assert "recent gap down detected" in meta["reason"]


def test_universal_cross_entry_state_based_bypass():
    """Verify that a V-bottom setup with positive/rising institutional flow (fas > fas_bt) and strong price velocity (psz_v > 0.02) bypasses the acceleration check, even if acceleration is below threshold and falling."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    # Trigger on FAS
    records[1]["cts_slope"] = -0.1
    records[2]["cts_slope"] = -0.05  # No CTS slope trigger
    records[1]["fas"] = -0.9
    records[2]["fas"] = -0.7  # FAS triggers above -0.8

    # Set acceleration to fail: below threshold and dropping (a2=0.04 -> a3=-0.01)
    records[1]["cts_accel"] = 0.04
    records[2]["cts_accel"] = -0.01  # below threshold 0.0, and not rising
    records[2]["cts_accel_threshold"] = 0.0

    # Case 1: psz_v is not strong (psz_v = 0.01 <= 0.02), should get REJECTED by acceleration
    records[1]["psz_v"] = 0.005
    records[2]["psz_v"] = 0.01
    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)
    assert passed is False
    assert "cts_accel below threshold" in meta["reason"]

    # Case 2: psz_v is strong (psz_v = 0.05 > 0.02), should BYPASS acceleration and get ACCEPTED
    records[1]["psz_v"] = 0.04
    records[2]["psz_v"] = 0.05  # rising and positive
    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)
    assert passed is True
    assert meta["reason"] == "Universal Cross accepted"


def test_universal_cross_entry_long_term_range_with_cwc_bypass():
    """Verify that setups in the upper portion of long-term ranges are rejected unless CWC trend coherence is strong (cwc > 0.50)."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    
    # Standard low range baseline works:
    records[2]["range_pos_22"] = 0.2
    records[2]["range_pos_63"] = 0.3
    records[2]["range_pos_252"] = 0.4
    records[2]["cwc"] = 0.3  # standard cwc
    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)
    assert passed is True

    # Case 1: In high range (range_pos_22 > 0.50) but weak coherence (cwc = 0.40 <= 0.50) -> REJECTED
    records[2]["range_pos_22"] = 0.6
    records[2]["range_pos_63"] = 0.3
    records[2]["range_pos_252"] = 0.4
    records[2]["cwc"] = 0.4
    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)
    assert passed is False
    assert "price in upper portion of long-term ranges without trend coherence" in meta["reason"]

    # Case 2: In high range but strong coherence (cwc = 0.60 > 0.50) -> BYPASSED & ACCEPTED
    records[2]["cwc"] = 0.6
    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)
    assert passed is True
    assert meta["reason"] == "Universal Cross accepted"

    # Case 3: Verify check for range_pos_63 (> 0.60) rejection
    records[2]["range_pos_22"] = 0.2
    records[2]["range_pos_63"] = 0.7
    records[2]["range_pos_252"] = 0.4
    records[2]["cwc"] = 0.4  # low coherence
    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)
    assert passed is False
    assert "price in upper portion of long-term ranges without trend coherence" in meta["reason"]

    # Case 4: Verify check for range_pos_252 (> 0.55) rejection
    records[2]["range_pos_22"] = 0.2
    records[2]["range_pos_63"] = 0.3
    records[2]["range_pos_252"] = 0.6
    records[2]["cwc"] = 0.4  # low coherence
    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)
    assert passed is False
    assert "price in upper portion of long-term ranges without trend coherence" in meta["reason"]


def test_universal_cross_exit_cts_cross():
    """Verify exit on CTS crossing down through the CTS sell threshold."""
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True
    cfg.cts_st_cross_enabled = True

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0,
        entry_tag=EntryTag.UNIVERSAL_CROSS.value
    )

    # Crossover: prev_cts >= prev_cts_st, cts < cts_st
    row = {"close": 105.0, "cts": 0.49, "cts_sell_threshold": 0.50}
    prev_row = {"close": 104.0, "cts": 0.51, "cts_sell_threshold": 0.50}

    reason, _ = exit_universal_cross(row, prev_row, trade, 105.0, 5, 0, cfg)

    assert reason == ExitReason.ST_CROSS


def test_universal_cross_entry_state_based_bypass_falling_fas():
    """Verify that a V-bottom setup with falling/decaying institutional flow (fas < prev_fas) fails the V-bottom bypass even if psz_v is strong, and gets rejected by the acceleration gate."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    # Trigger on CTS Slope (crosses above 0)
    records[1]["cts_slope"] = -0.1
    records[2]["cts_slope"] = 0.05
    
    # Set acceleration to fail: below threshold and dropping (a2=0.04 -> a3=-0.01)
    records[1]["cts_accel"] = 0.04
    records[2]["cts_accel"] = -0.01
    records[2]["cts_accel_threshold"] = 0.0

    # Set strong price velocity (psz_v = 0.05 > 0.02)
    records[1]["psz_v"] = 0.04
    records[2]["psz_v"] = 0.05

    # FAS is high (above fas_bt=-0.8) but falling (decaying institutional flow: prev=-0.6 -> current=-0.7)
    records[1]["fas"] = -0.6
    records[2]["fas"] = -0.7
    records[2]["fas_buy_threshold"] = -0.8
    
    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)
    assert passed is False
    assert "cts_accel below threshold" in meta["reason"]


def test_universal_cross_entry_cwc_basing_filter():
    """Verify that a setup with extremely low CWC (< 0.10) and degrading CWC slope (< -0.02) is correctly rejected as a choppy flat base, and passes when thresholds are not breached or the filter is disabled."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True
    cfg.universal_cross.cwc_basing_filter_enabled = True
    cfg.universal_cross.cwc_basing_cwc_threshold = 0.10
    cfg.universal_cross.cwc_basing_slope_threshold = -0.02

    records = get_base_records()
    # Trigger on CTS Slope
    records[1]["cts_slope"] = -0.1
    records[2]["cts_slope"] = 0.05
    
    # Ensure acceleration and velocity gates pass
    records[2]["cts_accel"] = 0.05
    records[2]["cts_accel_threshold"] = 0.0
    records[1]["cts_accel"] = 0.04  # rising
    records[2]["psz_v"] = 0.05      # positive
    records[1]["psz_v"] = 0.04      # rising
    records[2]["fas"] = -0.2
    records[1]["fas"] = -0.3        # rising

    # Case 1: Low CWC (0.08 < 0.10) and degrading CWC slope (-0.03 < -0.02) -> Should be REJECTED
    records[2]["cwc"] = 0.08
    records[2]["cwc_slope"] = -0.03
    
    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)
    assert passed is False
    assert "low and degrading trend coherence" in meta["reason"]

    # Case 2: Filter is disabled -> Should be ACCEPTED
    cfg.universal_cross.cwc_basing_filter_enabled = False
    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)
    assert passed is True

    # Case 3: Re-enabled but CWC is high (0.15 > 0.10) -> Should be ACCEPTED
    cfg.universal_cross.cwc_basing_filter_enabled = True
    records[2]["cwc"] = 0.15
    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)
    assert passed is True

    # Case 4: Re-enabled, CWC is low (0.08 < 0.10) but slope is positive/flat (-0.01 > -0.02) -> Should be ACCEPTED
    records[2]["cwc"] = 0.08
    records[2]["cwc_slope"] = -0.01
    passed, score, meta = entry_universal_cross(records[2], records[1], cfg, records, 2)
    assert passed is True


def test_universal_cross_exit_cts_near_miss_rollover():
    """Verify CTS near-miss detection and rollover exit logic in exit_universal_cross."""
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True
    cfg.cts_near_miss_exit_enabled = True
    cfg.cts_near_miss_gap = 0.10
    cfg.cts_near_miss_rollover_level = 0.50

    trade = Trade(
        symbol="TECHM",
        entry_date="2026-03-09",
        entry_price=100.0,
        entry_idx=0,
        atr_at_entry=2.0
    )

    # 1. Initially far from sell threshold, no near-miss
    # Gap: 1.0 - 0.20 = 0.80 > 0.10
    row = {"close": 100.0, "cts": 0.20, "cts_sell_threshold": 1.00}
    prev_row = {"close": 100.0, "cts": 0.15, "cts_sell_threshold": 1.00}
    reason, state_val = exit_universal_cross(row, prev_row, trade, 100.0, 1, 0, cfg)
    assert reason is None
    st = SavgolCTSExitState.from_int(state_val)
    assert st.cts_near_miss is False

    # Try dropping below rollover level (0.45 < 0.50) without near-miss active -> no exit
    row_drop = {"close": 98.0, "cts": 0.45, "cts_sell_threshold": 1.00}
    prev_drop = {"close": 100.0, "cts": 0.20, "cts_sell_threshold": 1.00}
    reason_drop, state_val_drop = exit_universal_cross(row_drop, prev_drop, trade, 100.0, 2, state_val, cfg)
    assert reason_drop is None
    assert SavgolCTSExitState.from_int(state_val_drop).cts_near_miss is False

    # 2. Enter near-miss range: gap = 1.00 - 0.92 = 0.08 <= 0.10
    row_near = {"close": 110.0, "cts": 0.92, "cts_sell_threshold": 1.00}
    prev_near = {"close": 108.0, "cts": 0.85, "cts_sell_threshold": 1.00}
    reason_near, state_val_near = exit_universal_cross(row_near, prev_near, trade, 110.0, 3, state_val, cfg)
    assert reason_near is None
    st_near = SavgolCTSExitState.from_int(state_val_near)
    assert st_near.cts_near_miss is True

    # 3. Drops a bit but not below rollover level (0.75 >= 0.50) -> near-miss stays True, no exit
    row_mid = {"close": 108.0, "cts": 0.75, "cts_sell_threshold": 1.00}
    prev_mid = {"close": 110.0, "cts": 0.92, "cts_sell_threshold": 1.00}
    reason_mid, state_val_mid = exit_universal_cross(row_mid, prev_mid, trade, 110.0, 4, state_val_near, cfg)
    assert reason_mid is None
    st_mid = SavgolCTSExitState.from_int(state_val_mid)
    assert st_mid.cts_near_miss is True

    # 4. Drops below rollover level (0.45 < 0.50) -> triggers CTS_NEAR_MISS_ROLLOVER
    row_roll = {"close": 102.0, "cts": 0.45, "cts_sell_threshold": 1.00}
    prev_roll = {"close": 108.0, "cts": 0.75, "cts_sell_threshold": 1.00}
    reason_roll, state_val_roll = exit_universal_cross(row_roll, prev_roll, trade, 110.0, 5, state_val_mid, cfg)
    assert reason_roll == ExitReason.CTS_NEAR_MISS_ROLLOVER

    # 5. Bypassed if disabled
    cfg.cts_near_miss_exit_enabled = False
    reason_dis, _ = exit_universal_cross(row_roll, prev_roll, trade, 110.0, 5, state_val_mid, cfg)
    assert reason_dis is None
    cfg.cts_near_miss_exit_enabled = True

    # 6. Bypassed on panic bar
    row_panic = {"close": 95.0, "high": 95.0, "low": 90.0, "cts": 0.45, "cts_sell_threshold": 1.00, "rdv": 3.0}
    prev_panic = {"close": 102.0, "high": 102.0, "low": 98.0, "cts": 0.75, "cts_sell_threshold": 1.00}
    reason_panic, _ = exit_universal_cross(row_panic, prev_panic, trade, 110.0, 5, state_val_mid, cfg)
    assert reason_panic is None


def test_universal_cross_entry_technical_momentum_bypass():
    """Verify that a stock in upper range_pos_10 is accepted if it meets the technical momentum bypass (cwc >= 0.40 and pdd_30 < -3.5)."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records()
    
    # Put in upper range (>0.5)
    records[2]["range_pos_10"] = 0.6
    
    # Scenario A: Bypassed because cwc >= 0.40 and pdd_30 < -3.5
    records[2]["cwc"] = 0.45
    records[2]["pdd_30"] = -4.0
    
    passed_a, score_a, meta_a = entry_universal_cross(records[2], records[1], cfg, records, 2)
    assert passed_a is True
    assert score_a == 70

    # Scenario B: Rejected because cwc is too low (< 0.40)
    records[2]["cwc"] = 0.35
    records[2]["pdd_30"] = -4.0
    
    passed_b, score_b, meta_b = entry_universal_cross(records[2], records[1], cfg, records, 2)
    assert passed_b is False
    assert "price not in lower half of weekly range" in meta_b["reason"]

    # Scenario C: Rejected because pdd_30 is too high (>= -3.5)
    records[2]["cwc"] = 0.45
    records[2]["pdd_30"] = -3.0
    
    passed_c, score_c, meta_c = entry_universal_cross(records[2], records[1], cfg, records, 2)
    assert passed_c is False
    assert "price not in lower half of weekly range" in meta_c["reason"]


def test_universal_cross_exit_cts_near_miss_crossover_reset():
    """Verify that cts_near_miss is cleared if cts >= st or if it crosses down from above st."""
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True
    cfg.cts_near_miss_exit_enabled = True
    cfg.cts_near_miss_gap = 0.10

    trade = Trade(
        symbol="TECHM",
        entry_date="2026-03-09",
        entry_price=100.0,
        entry_idx=0,
        atr_at_entry=2.0
    )

    # 1. Start with near-miss active
    # Gap: 1.0 - 0.92 = 0.08 <= 0.10
    row_near = {"close": 110.0, "cts": 0.92, "cts_sell_threshold": 1.00}
    prev_near = {"close": 108.0, "cts": 0.85, "cts_sell_threshold": 1.00}
    reason, state_val = exit_universal_cross(row_near, prev_near, trade, 110.0, 1, 0, cfg)
    st = SavgolCTSExitState.from_int(state_val)
    assert st.cts_near_miss is True

    # 2. CTS rises at or above sell threshold -> near_miss must reset to False
    row_high = {"close": 112.0, "cts": 1.00, "cts_sell_threshold": 1.00}
    reason_high, state_val_high = exit_universal_cross(row_high, row_near, trade, 112.0, 2, state_val, cfg)
    st_high = SavgolCTSExitState.from_int(state_val_high)
    assert st_high.cts_near_miss is False

    # 3. CTS crosses down from above sell threshold -> should NOT activate near-miss
    # (Because it is a cross-down from above, not a near-miss from below)
    row_cross = {"close": 111.0, "cts": 0.95, "cts_sell_threshold": 1.00}
    prev_cross = {"close": 112.0, "cts": 1.00, "cts_sell_threshold": 1.00}
    reason_cross, state_val_cross = exit_universal_cross(row_cross, prev_cross, trade, 112.0, 3, state_val_high, cfg)
    st_cross = SavgolCTSExitState.from_int(state_val_cross)
    assert st_cross.cts_near_miss is False


def test_universal_cross_exit_cts_near_miss_rising_regression():
    """Verify HINDUNILVR regression where CTS enters the near-miss zone from below while rising,
    and should NOT trigger a premature near-miss exit even if its value is below the rollover level.
    """
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True
    cfg.cts_near_miss_exit_enabled = True
    cfg.cts_near_miss_gap = 0.10
    cfg.cts_near_miss_rollover_level = 0.50

    trade = Trade(
        symbol="HINDUNILVR",
        entry_date="2025-12-19",
        entry_price=2280.0,
        entry_idx=0,
        atr_at_entry=30.0
    )

    # Bar 1: CTS is far below the threshold
    # CTS: -0.027, ST: 0.394
    row1 = {"close": 2293.30, "cts": -0.0270, "cts_sell_threshold": 0.3940}
    prev1 = {"close": 2285.40, "cts": -0.5915, "cts_sell_threshold": 0.3940}
    reason1, state1 = exit_universal_cross(row1, prev1, trade, 2293.30, 1, 0, cfg)
    assert reason1 is None
    st1 = SavgolCTSExitState.from_int(state1)
    assert st1.cts_near_miss is False

    # Bar 2: CTS climbs to 0.3195, which enters the near-miss gap zone (0.3940 - 0.3195 = 0.0745 <= 0.10).
    # Since CTS is rising (-0.027 -> 0.3195), it is NOT a rollover.
    # Therefore, no exit should trigger even though CTS (0.3195) is less than the rollover level (0.50).
    row2 = {"close": 2290.20, "cts": 0.3195, "cts_sell_threshold": 0.3940}
    reason2, state2 = exit_universal_cross(row2, row1, trade, 2293.30, 2, state1, cfg)
    assert reason2 is None
    st2 = SavgolCTSExitState.from_int(state2)
    assert st2.cts_near_miss is True


def test_universal_cross_exit_cts_near_miss_persistence_regression():
    """Verify that if cts reaches or exceeds st, near_miss is permanently locked out (stays False)
    even if the trade stays within the gap region for multiple subsequent bars after crossing down,
    and dropping below the rollover level later does not trigger CTS_NEAR_MISS_ROLLOVER.
    """
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True
    cfg.cts_near_miss_exit_enabled = True
    cfg.cts_near_miss_gap = 0.10
    cfg.cts_near_miss_rollover_level = 0.50
    cfg.cts_st_cross_enabled = False
    cfg.prt_st_cross_enabled = False

    trade = Trade(
        symbol="CIPLA",
        entry_date="2026-04-06",
        entry_price=100.0,
        entry_idx=0,
        atr_at_entry=2.0
    )

    # 1. Bar 1: CTS is close to ST but not yet crossed (gap <= 0.10)
    # CTS: 0.92, ST: 1.00 -> Near Miss active
    row1 = {"close": 100.0, "cts": 0.92, "cts_sell_threshold": 1.00}
    prev1 = {"close": 100.0, "cts": 0.80, "cts_sell_threshold": 1.00}
    reason1, state1 = exit_universal_cross(row1, prev1, trade, 100.0, 1, 0, cfg)
    assert reason1 is None
    assert SavgolCTSExitState.from_int(state1).cts_near_miss is True
    assert SavgolCTSExitState.from_int(state1).cts_reached_st is False

    # 2. Bar 2: CTS crosses above ST (CTS: 1.05, ST: 1.00)
    # Near Miss should clear, reached_st becomes True
    row2 = {"close": 102.0, "cts": 1.05, "cts_sell_threshold": 1.00}
    reason2, state2 = exit_universal_cross(row2, row1, trade, 102.0, 2, state1, cfg)
    assert reason2 is None
    assert SavgolCTSExitState.from_int(state2).cts_near_miss is False
    assert SavgolCTSExitState.from_int(state2).cts_reached_st is True

    # 3. Bar 3: CTS crosses down below ST, landing in the gap region (CTS: 0.95, ST: 1.00)
    # Near Miss must remain False because we reached ST earlier (not a near-miss setup!)
    row3 = {"close": 101.0, "cts": 0.95, "cts_sell_threshold": 1.00}
    reason3, state3 = exit_universal_cross(row3, row2, trade, 102.0, 3, state2, cfg)
    assert reason3 is None
    assert SavgolCTSExitState.from_int(state3).cts_near_miss is False
    assert SavgolCTSExitState.from_int(state3).cts_reached_st is True

    # 4. Bar 4: CTS remains below ST in the gap region for another bar (CTS: 0.96, ST: 1.00)
    # Near Miss must remain False (persistence regression check)
    row4 = {"close": 101.5, "cts": 0.96, "cts_sell_threshold": 1.00}
    reason4, state4 = exit_universal_cross(row4, row3, trade, 102.0, 4, state3, cfg)
    assert reason4 is None
    assert SavgolCTSExitState.from_int(state4).cts_near_miss is False
    assert SavgolCTSExitState.from_int(state4).cts_reached_st is True

    # 5. Bar 5: CTS drops below rollover level (CTS: 0.40 < 0.50)
    # Should NOT trigger CTS_NEAR_MISS_ROLLOVER exit because near_miss is False
    row5 = {"close": 98.0, "cts": 0.40, "cts_sell_threshold": 1.00}
    reason5, state5 = exit_universal_cross(row5, row4, trade, 102.0, 5, state4, cfg)
    assert reason5 is None
    assert SavgolCTSExitState.from_int(state5).cts_near_miss is False
    assert SavgolCTSExitState.from_int(state5).cts_reached_st is True


def test_universal_cross_exit_cipla_regression():
    """Verify that CIPLA trade lifecycle is correctly handled, particularly that:
    1. Early near-miss is cleared once CTS >= ST.
    2. When CTS crosses down below ST, st.cts_near_miss remains False.
    3. PRT_ST_CROSS or ST_CROSS are allowed to trigger on the cross-down day,
       rather than triggering CTS_NEAR_MISS_ROLLOVER.
    """
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True
    cfg.cts_near_miss_exit_enabled = True
    cfg.cts_near_miss_gap = 0.10
    cfg.cts_near_miss_rollover_level = 0.50
    cfg.cts_st_cross_enabled = True
    cfg.prt_st_cross_enabled = True

    trade = Trade(
        symbol="CIPLA",
        entry_date="2026-04-06",
        entry_price=1000.0,
        entry_idx=0,
        atr_at_entry=20.0
    )

    # Day 1: CTS is negative (-1.0), not near-miss
    row1 = {"close": 1000.0, "cts": -1.0, "cts_sell_threshold": 0.6733}
    reason1, state1 = exit_universal_cross(row1, None, trade, 1000.0, 1, 0, cfg)
    assert reason1 is None
    assert SavgolCTSExitState.from_int(state1).cts_near_miss is False
    assert SavgolCTSExitState.from_int(state1).cts_reached_st is False

    # Day 2: CTS approaches ST (CTS: 0.6846, ST: 0.7133 -> gap = 0.0287)
    # Near Miss should activate
    row2 = {"close": 1010.0, "cts": 0.6846, "cts_sell_threshold": 0.7133}
    reason2, state2 = exit_universal_cross(row2, row1, trade, 1010.0, 2, state1, cfg)
    assert reason2 is None
    assert SavgolCTSExitState.from_int(state2).cts_near_miss is True
    assert SavgolCTSExitState.from_int(state2).cts_reached_st is False

    # Day 3: CTS rises above ST (CTS: 0.9903, ST: 0.9740)
    # Near Miss must clear, reached_st becomes True
    row3 = {"close": 1020.0, "cts": 0.9903, "cts_sell_threshold": 0.9740}
    reason3, state3 = exit_universal_cross(row3, row2, trade, 1020.0, 3, state2, cfg)
    assert reason3 is None
    assert SavgolCTSExitState.from_int(state3).cts_near_miss is False
    assert SavgolCTSExitState.from_int(state3).cts_reached_st is True

    # Day 4: CTS crosses down below ST (CTS: 0.9620, ST: 1.0000)
    # Near Miss must remain False. And since CTS crossed below ST and PRT crossed below ST,
    # it should trigger PRT_ST_CROSS (or ST_CROSS if prt was not crossing).
    row4 = {
        "close": 1015.0,
        "cts": 0.9620,
        "cts_sell_threshold": 1.0000,
        "prt": 0.4808,
        "prt_sell_threshold": 0.4824,
    }
    prev4 = {
        "close": 1020.0,
        "cts": 1.0000,
        "cts_sell_threshold": 1.0000,
        "prt": 0.5726,
        "prt_sell_threshold": 0.4648,
    }
    reason4, state4 = exit_universal_cross(row4, prev4, trade, 1020.0, 4, state3, cfg)
    assert reason4 == ExitReason.PRT_ST_CROSS
    assert SavgolCTSExitState.from_int(state4).cts_reached_st is True


def get_base_records_10(price_trend="flat"):
    """Returns a mocked list of 10 records for testing the 5th trigger."""
    records = []
    for i in range(10):
        # Default price is flat
        high = 100.0
        low = 98.0
        close = 99.0
        if price_trend == "knife":
            # Perfect fall
            high = 100.0 - i
            low = 98.0 - i
            close = 99.0 - i
            
        records.append({
            "high": high, "low": low, "close": close, "atr_20": 2.0,
            "cts_slope": -0.1, "cts_accel": 0.02, "cts_accel_threshold": 0.0,
            "psz_v": 1.0 + 0.1 * i, "fas": -0.9, "fas_buy_threshold": -0.8,
            "range_pos_10": 0.2, "base_tightness": 0.35, "pdd_30": 0.0,
            "pdd_120": -5.0, "range_width_10": 10.0, "rdv": 1.0,
            "cwc": 0.45, "price_slope_z": -0.20
        })
    return records


def test_universal_cross_5th_trigger_success():
    """Verify entry triggers on the 5th trigger (CWC Accumulation Exhaustion) when all sweet-spot metrics are met."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records_10(price_trend="flat")
    passed, score, meta = entry_universal_cross(records[9], records[8], cfg, records, 9)

    assert passed is True
    assert score == 70
    assert meta["entry_tag"] == EntryTag.UNIVERSAL_CROSS.value
    assert meta["reason"] == "Universal Cross accepted"


def test_universal_cross_5th_trigger_rejection_knife():
    """Verify that the 5th trigger is rejected when the price is in a perfect linear downtrend (falling knife)."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records_10(price_trend="knife")
    passed, score, meta = entry_universal_cross(records[9], records[8], cfg, records, 9)

    assert passed is False
    assert "No structural inflection" in meta["reason"]


def test_universal_cross_5th_trigger_rejection_shallow_pdd():
    """Verify that the 5th trigger is rejected when pdd_120 is too shallow."""
    cfg = SavgolCTSEntryConfig()
    cfg.universal_cross.enabled = True

    records = get_base_records_10(price_trend="flat")
    records[9]["pdd_120"] = -3.5  # too shallow (must be <= -4.0)
    passed, score, meta = entry_universal_cross(records[9], records[8], cfg, records, 9)

    assert passed is False
    assert "No structural inflection" in meta["reason"]






