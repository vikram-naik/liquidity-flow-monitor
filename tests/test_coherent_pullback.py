import pytest
import numpy as np
from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.entries.coherent_pullback import entry_coherent_pullback
from src.trading.signals.savgol_cts.signal import SavgolCTSSignal


def get_base_records():
    """Returns mock records satisfying all Coherent Pullback conditions on the last bar."""
    records = []
    for i in range(10):
        records.append({
            "high": 100.0,
            "low": 98.0,
            "close": 99.0,
            "open": 99.5,
            "cwc": 0.55,               # >= 0.50
            "pdd_30": -3.0,            # between -5.00 and -2.50
            "range_pos_10": 0.25,      # <= 0.40
            "pdd_120": 2.0,            # >= -4.00
            "base_tightness": 0.30,    # <= 0.45
            "date": f"2026-05-{10+i:02d}",
            "atr_20": 1.0,
            "cts": -0.5 + (0.01 * i),  # rising (cts_change > 0)
            "fas": -0.5,
            "prt_slope": 0.1,
            "regime": "uptrend"
        })
    return records


def test_coherent_pullback_success():
    """Verify successful entry triggers when all statistical thresholds are met."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    
    passed, score, meta = entry_coherent_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is True
    assert score == 82
    assert meta["entry_tag"] == EntryTag.COHERENT_PULLBACK.value
    assert "accepted" in meta["reason"].lower()


def test_coherent_pullback_disabled():
    """Verify path rejects signals when path is disabled in config."""
    cfg = SavgolCTSEntryConfig()
    cfg.coherent_pullback.enabled = False
    records = get_base_records()
    
    passed, score, meta = entry_coherent_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "disabled" in meta["reason"]


def test_coherent_pullback_rejection_cwc():
    """Verify rejection when CWC is below threshold."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["cwc"] = 0.40  # Below 0.50 threshold
    
    passed, score, meta = entry_coherent_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "cwc" in meta["reason"] and "below threshold" in meta["reason"]


def test_coherent_pullback_rejection_pdd_30():
    """Verify rejection when short-term pullback is insufficient."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["pdd_30"] = -2.0  # Not oversold enough (> -2.50)
    
    passed, score, meta = entry_coherent_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "pdd_30" in meta["reason"] and "above maximum threshold" in meta["reason"]


def test_coherent_pullback_rejection_range_pos_10():
    """Verify rejection when price has run up and is in the upper half of the weekly range."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["range_pos_10"] = 0.45  # Overextended weekly (> 0.40)
    
    passed, score, meta = entry_coherent_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "range_pos_10" in meta["reason"] and "above weekly" in meta["reason"]


def test_coherent_pullback_rejection_falling_knife():
    """Verify rejection by the price Spearman falling knife guard."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    
    # Force a rapid downward price crash to trigger falling knife guard
    for i in range(10):
        records[i]["high"] = 110.0 - i * 2.0
        records[i]["low"] = 108.0 - i * 2.0
        records[i]["close"] = 109.0 - i * 2.0
        
    passed, score, meta = entry_coherent_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "falling knife" in meta["reason"].lower()


def test_signal_integration():
    """Verify integration with the main SavgolCTSSignal orchestrator."""
    signal = SavgolCTSSignal()
    cfg = SavgolCTSEntryConfig()
    
    # Isolate Coherent Pullback path
    cfg.universal_cross.enabled = False
    cfg.trend_pullback_enabled = False
    cfg.flow_momentum.enabled = False
    cfg.coherent_pullback.enabled = True
    
    records = get_base_records()
    
    passed, score, meta = signal.check_entry(records[9], records[8], cfg, records, 9)
    
    assert passed is True
    assert score == 82
    assert meta["entry_tag"] == EntryTag.COHERENT_PULLBACK.value
