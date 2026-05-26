import pytest
import numpy as np
from src.trading.signals.base import Trade
from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.entries.flow_momentum import entry_flow_momentum
from src.trading.signals.savgol_cts.signal import SavgolCTSSignal


def get_base_records_path2():
    """Returns a list of 10 mock records that satisfy all Flow Momentum Path 2 conditions on the last bar."""
    records = []
    for i in range(10):
        records.append({
            "high": 100.0 + i,
            "low": 98.0 + i,
            "close": 99.0 + i,
            "open": 98.5 + i,
            "cts": 0.5 if i == 9 else 0.3,
            "cts_buy_threshold": 0.4,
            "cts_accel": 0.05 if i == 9 else 0.03,
            "cts_accel_threshold": 0.02,
            "price_slope_z": -0.05 if i == 9 else (-0.10 if i == 8 else (-0.15 if i == 7 else -0.20)),
            "rsz_v": 0.16 if i == 9 else -0.05,
            "psz_v": 0.08 if i == 9 else (0.06 if i == 8 else 0.04),
            "cwc_slope": 0.03,
            "coherence": 0.75,
            "cwc": 0.60,
            "mcs_composite": 0.10,
            "range_pos_63": 0.50,
            "date": f"2026-05-{10+i:02d}",
            "regime": "uptrend",
            "atr_20": 1.0
        })
    return records


def get_base_records_path1():
    """Returns a list of 10 mock records that satisfy all Flow Momentum Path 1 conditions on the last bar."""
    records = []
    for i in range(10):
        records.append({
            "high": 100.0,
            "low": 98.0,
            "close": 99.0,
            "open": 99.5,
            "cwc_slope": -0.01,
            "rsz_v": -0.05 if i == 9 else 0.02, # Turns negative
            "psz_v": -0.002 if i == 9 else 0.01, # Turns negative & < -0.001
            "prt_slope": -0.02 if i == 9 else 0.01, # Turns negative
            "cts": 0.15, # < 0.2
            "range_pos_252": 0.06, # > 0.05
            "range_pos_22": 0.05, # > 0.04
            "date": f"2026-05-{10+i:02d}",
            "atr_20": 1.0
        })
    return records


def test_flow_momentum_entry_success_path2():
    """Verify that a successful Path 2 momentum breakout triggers entry."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records_path2()
    
    passed, score, meta = entry_flow_momentum(records[9], records[8], cfg, records, 9)
    
    assert passed is True
    assert score == 81
    assert meta["entry_tag"] == EntryTag.FLOW_MOMENTUM.value
    assert "accepted" in meta["reason"].lower()


def test_flow_momentum_entry_success_path1():
    """Verify that a successful Path 1 deep reversion triggers entry."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records_path1()
    
    passed, score, meta = entry_flow_momentum(records[9], records[8], cfg, records, 9)
    
    assert passed is True
    assert score == 80
    assert meta["entry_tag"] == EntryTag.FLOW_MOMENTUM.value
    assert "accepted" in meta["reason"].lower()


def test_flow_momentum_rejection_disabled():
    """Verify rejection when the Flow Momentum entry path is disabled."""
    cfg = SavgolCTSEntryConfig()
    cfg.flow_momentum.enabled = False
    records = get_base_records_path2()
    
    passed, score, meta = entry_flow_momentum(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "disabled" in meta["reason"]


def test_flow_momentum_rejection_cts_not_crossing_path2():
    """Verify rejection when cts does not cross cts_buy_threshold."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records_path2()
    records[8]["cts"] = 0.5 # Already crossed on previous bar
    
    passed, score, meta = entry_flow_momentum(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "cts does not cross" in meta["reason"]


def test_flow_momentum_rejection_psz_v_below_min_path2():
    """Verify rejection when psz_v is below the required minimum threshold."""
    cfg = SavgolCTSEntryConfig()
    cfg.flow_momentum.psz_v_min = 0.10
    records = get_base_records_path2()
    records[8]["psz_v"] = 0.04
    records[9]["psz_v"] = 0.05 # Rising (0.05 > 0.04) but below 0.10 threshold
    
    passed, score, meta = entry_flow_momentum(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "psz_v" in meta["reason"] and "below required minimum" in meta["reason"]


def test_flow_momentum_rejection_mcs_below_min_path2():
    """Verify rejection when mcs_composite is below the required minimum threshold."""
    cfg = SavgolCTSEntryConfig()
    cfg.flow_momentum.mcs_composite_min = 0.15
    records = get_base_records_path2()
    records[9]["mcs_composite"] = 0.10 # Below 0.15 threshold
    
    passed, score, meta = entry_flow_momentum(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "mcs_composite" in meta["reason"] and "below required minimum" in meta["reason"]


def test_flow_momentum_rejection_regime_path2():
    """Verify optional bullish regime check rejects non-bullish markets when enabled."""
    cfg = SavgolCTSEntryConfig()
    cfg.flow_momentum.only_bullish_regime = True
    records = get_base_records_path2()
    records[9]["regime"] = "bearish"
    
    passed, score, meta = entry_flow_momentum(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "regime is not bullish" in meta["reason"]


def test_signal_integration_success():
    """Verify that SavgolCTSSignal.check_entry successfully triggers via Path 2."""
    signal = SavgolCTSSignal()
    cfg = SavgolCTSEntryConfig()
    
    # Disable Path 0 and Path 1 to isolate Path 2
    cfg.universal_cross.enabled = False
    cfg.trend_pullback_enabled = False
    cfg.flow_momentum.enabled = True
    
    records = get_base_records_path2()
    
    # Check entry at index 9
    passed, score, meta = signal.check_entry(records[9], records[8], cfg, records, 9)
    
    assert passed is True
    assert score == 81
    assert meta["entry_tag"] == EntryTag.FLOW_MOMENTUM.value
