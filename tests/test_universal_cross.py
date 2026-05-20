import pytest
import numpy as np
from src.trading.signals.base import Trade
from src.trading.signals.enums import EntryTag, ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
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
    row = {"close": 105.0, "prt": 0.49, "prt_sell_threshold": 0.50}
    prev_row = {"close": 104.0, "prt": 0.51, "prt_sell_threshold": 0.50}

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
    row = {"close": 105.0, "prt": 0.49, "prt_sell_threshold": 0.50, "high": 99.0, "rdv": 2.5}
    prev_row = {"close": 104.0, "prt": 0.51, "prt_sell_threshold": 0.50, "low": 100.0}

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


def test_universal_cross_exit_atr_chandelier():
    """Verify ATR Chandelier trailing stop exit logic."""
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True
    cfg.chandelier_stop_enabled = True
    cfg.chandelier_stop_k = 3.5
    cfg.chandelier_stop_activation_pct = 5.0

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0,
        entry_tag=EntryTag.UNIVERSAL_CROSS.value
    )

    # Peak close is 106.0 (+6% peak pnl). ATR is 1.0.
    # Trailing stop price = 106.0 - 3.5 * 1.0 = 102.5.
    prev_row = {"close": 104.0, "atr_20": 1.0}

    # Case 1: Close is 103.0 (> 102.5 trailing stop price) -> Should NOT exit
    row = {"close": 103.0, "atr_20": 1.0}
    reason, _ = exit_universal_cross(row, prev_row, trade, 106.0, 5, 0, cfg)
    assert reason is None

    # Case 2: Close is 102.0 (<= 102.5 trailing stop price) -> Should EXIT
    row_hit = {"close": 102.0, "atr_20": 1.0}
    reason_hit, _ = exit_universal_cross(row_hit, prev_row, trade, 106.0, 5, 0, cfg)
    assert reason_hit == ExitReason.ATR_CHANDELIER

    # Case 3: Peak close is 104.0 (+4% peak pnl, under 5.0% activation threshold)
    # Even if close drops to 99.0, since stop was never active, should NOT exit under Chandelier stop.
    row_inactive = {"close": 99.0, "atr_20": 1.0}
    reason_inactive, _ = exit_universal_cross(row_inactive, prev_row, trade, 104.0, 5, 0, cfg)
    assert reason_inactive is None


def test_universal_cross_exit_atr_chandelier_panic_suppression():
    """Verify ATR Chandelier trailing stop exit is suppressed under panic gap down conditions."""
    cfg = SavgolCTSExitConfig().universal_cross
    cfg.enabled = True
    cfg.chandelier_stop_enabled = True
    cfg.chandelier_stop_k = 3.5
    cfg.chandelier_stop_activation_pct = 5.0
    cfg.panic_exit_suppression_enabled = True
    cfg.panic_exit_rdv_threshold = 2.0

    trade = Trade(
        symbol="TEST", entry_date="2024-01-01", entry_price=100.0,
        entry_idx=0, atr_at_entry=2.0,
        entry_tag=EntryTag.UNIVERSAL_CROSS.value
    )

    # Peak close is 106.0 (+6% peak pnl). ATR is 1.0.
    # Trailing stop price = 106.0 - 3.5 * 1.0 = 102.5.
    # Close drops to 102.0 (<= 102.5 stop price) but it is a panic day (gap down and high rdv)
    prev_row = {"close": 104.0, "atr_20": 1.0, "low": 103.0}
    row = {"close": 102.0, "atr_20": 1.0, "high": 102.2, "rdv": 2.5}

    reason, _ = exit_universal_cross(row, prev_row, trade, 106.0, 5, 0, cfg)
    assert reason is None  # Suppressed!


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
    
    # Put a gap-down at index 4 (11 bars before index 15)
    # i.e., index 4 high < index 3 low
    records[3]["low"] = 110.0
    records[4]["high"] = 102.0
    records[4]["atr_20"] = 2.0  # gap size = 8.0 > 0.3 * 2.0 (0.6)
    
    # Test with default gap_down_lookback = 10
    cfg.universal_cross.gap_down_lookback = 10
    passed, score, meta = entry_universal_cross(records[15], records[14], cfg, records, 15)
    assert passed is True  # Should not be rejected because gap is 11 bars ago, outside lookback 10
    
    # Test with gap_down_lookback = 15
    cfg.universal_cross.gap_down_lookback = 15
    passed, score, meta = entry_universal_cross(records[15], records[14], cfg, records, 15)
    assert passed is False  # Rejected because lookback 15 covers index 4 (11 bars ago)
    assert "recent gap down detected" in meta["reason"]


