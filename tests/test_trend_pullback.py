import pytest
import numpy as np
import pandas as pd
from src.trading.signals.base import Trade
from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.entries.trend_pullback import entry_trend_pullback
from src.trading.signals.savgol_cts.signal import SavgolCTSSignal


def get_base_records():
    """Returns a list of 10 mock records that satisfy all secular trend pullback conditions."""
    records = []
    for i in range(10):
        records.append({
            "high": 100.0,
            "low": 98.0,
            "close": 99.0,
            "cwc": 0.60,
            "pdd_30": -3.0,
            "pdd_120": -1.0,
            "base_tightness": 0.30,
            "price_slope_z": -0.20,
            "range_pos_10": 0.20,
            "range_pos_252": 0.20,
            "cts": 0.50 + 0.02 * i,  # Increments to guarantee positive cts_change
            "cts_slope": 0.0,
            "fas": 0.0,
            "prt_slope": 0.0,
            "date": f"2026-05-{10+i:02d}",
            "regime": "bullish"
        })
    return records


def test_trend_pullback_entry_success():
    """Verify that a highly coherent pullback in a secular uptrend successfully triggers entry."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    
    passed, score, meta = entry_trend_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is True
    assert score == 75
    assert meta["entry_tag"] == EntryTag.TREND_PULLBACK.value
    assert meta["reason"] == "Secular Trend Pullback accepted"


def test_trend_pullback_rejection_shallow_cwc():
    """Verify rejection when CWC is below the coherence threshold."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["cwc"] = 0.50  # Below 0.55 threshold
    
    passed, score, meta = entry_trend_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "cwc below optimized threshold" in meta["reason"]


def test_trend_pullback_rejection_pdd30_not_pullback():
    """Verify rejection when pdd_30 indicates the pullback is not deep enough."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["pdd_30"] = -2.0  # Above -2.50 threshold
    
    passed, score, meta = entry_trend_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "pdd_30 not in optimized pullback region" in meta["reason"]


def test_trend_pullback_rejection_loose_base():
    """Verify rejection when base is too loose (high base tightness)."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["base_tightness"] = 0.50  # Above 0.45 threshold
    
    passed, score, meta = entry_trend_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "base_tightness above optimized threshold" in meta["reason"]


def test_trend_pullback_rejection_slope_z_not_oversold():
    """Verify rejection when price slope Z is not oversold (too high)."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["price_slope_z"] = 0.0  # Above -0.10 threshold
    
    passed, score, meta = entry_trend_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "price_slope_z not in optimized oversold region" in meta["reason"]


def test_trend_pullback_rejection_secular_distribution():
    """Verify rejection when pdd_120 indicates long-term/secular distribution."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["pdd_120"] = -4.5  # Below -4.0 threshold
    
    passed, score, meta = entry_trend_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "Secular Distribution" in meta["reason"]


def test_trend_pullback_rejection_falling_knife():
    """Verify rejection when prices are in a steep, straight falling knife pattern (Spearman <= -0.90)."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    
    # Force typical prices to decrease perfectly monotonically over the last 10 bars
    for i in range(10):
        records[i]["high"] = 200.0 - i * 5.0
        records[i]["low"] = 198.0 - i * 5.0
        records[i]["close"] = 199.0 - i * 5.0
        
    passed, score, meta = entry_trend_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "Falling Knife Guard" in meta["reason"]


def test_trend_pullback_rejection_overextended():
    """Verify rejection when range_pos_10 indicates price is already in the upper half of the range."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["range_pos_10"] = 0.45  # Above 0.40 threshold
    
    passed, score, meta = entry_trend_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "Overextended" in meta["reason"]


def test_trend_pullback_config_disabled():
    """Verify that the trend pullback path does not trigger when disabled in the config."""
    cfg = SavgolCTSEntryConfig()
    cfg.trend_pullback_enabled = False
    records = get_base_records()
    
    passed, score, meta = entry_trend_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "disabled" in meta["reason"]


def test_composite_signal_integration_trend_pullback():
    """Verify SavgolCTSSignal successfully checks and falls back to Trend Pullback when Universal Cross is rejected."""
    signal = SavgolCTSSignal()
    cfg = SavgolCTSEntryConfig()
    
    # Disable universal cross to guarantee rejection/skipping
    cfg.universal_cross.enabled = False
    cfg.trend_pullback_enabled = True
    
    records = get_base_records()
    passed, intensity, meta = signal.check_entry(records[9], records[8], cfg, records, 9)
    
    assert passed is True
    assert intensity == 75
    assert meta["entry_tag"] == EntryTag.TREND_PULLBACK.value
    assert "Secular Trend Pullback accepted" in meta["reason"]


def test_composite_signal_both_disabled():
    """Verify that if both paths are disabled, check_entry returns False with correct reason."""
    signal = SavgolCTSSignal()
    cfg = SavgolCTSEntryConfig()
    
    cfg.universal_cross.enabled = False
    cfg.trend_pullback_enabled = False
    
    records = get_base_records()
    passed, intensity, meta = signal.check_entry(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "Universal path disabled" in meta["reason"]
    assert "TrendPullback path disabled" in meta["reason"]


def test_trend_pullback_rejection_deep_pdd30_crash():
    """Verify rejection when short-term drawdown is too deep (falling knife / short-term crash)."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["pdd_30"] = -5.50  # Below -5.0 crash threshold
    
    passed, score, meta = entry_trend_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "short-term crash" in meta["reason"]


def test_trend_pullback_rejection_secular_overextension():
    """Verify rejection when yearly range position indicates price is overextended on a long-term basis."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["range_pos_252"] = 0.60  # Above 0.55 yearly range position threshold
    
    passed, score, meta = entry_trend_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "Secular Overextension" in meta["reason"]


def test_trend_pullback_rejection_negative_cts_change():
    """Verify rejection when raw CTS daily change is heavily negative (active institutional selling)."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    # To test CTS negative change rejection, we override the default increment
    records[8]["cts"] = 0.40
    records[9]["cts"] = 0.30  # Raw change = 0.30 - 0.40 = -0.10 (Below -0.05 threshold)
    
    passed, score, meta = entry_trend_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "active institutional distribution" in meta["reason"]


def test_trend_pullback_rejection_no_trigger():
    """Verify rejection when no resumption trigger is present (flow change/slope flat or negative)."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    # Override CTS to be flat/non-increasing, and no other trigger active
    records[8]["cts"] = 0.50
    records[9]["cts"] = 0.50  # cts_change = 0.0 (no trigger)
    records[9]["fas"] = -0.80
    records[8]["fas"] = -0.80  # fas_change = 0.0
    records[9]["prt_slope"] = -0.10
    records[8]["prt_slope"] = -0.10  # prt_slope_change = 0.0

    passed, score, meta = entry_trend_pullback(records[9], records[8], cfg, records, 9)

    assert passed is False
    assert "Resumption Trigger" in meta["reason"]

