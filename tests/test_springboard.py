import pytest
import numpy as np
from src.trading.signals.enums import EntryTag
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig
from src.trading.signals.savgol_cts.entries.springboard import entry_springboard
from src.trading.signals.savgol_cts.signal import SavgolCTSSignal


def get_base_records():
    """Returns mock records satisfying all SpringBoard conditions on the last bar (idx 11)."""
    records = []
    for i in range(12):
        tp = 99.0
        if i == 7: tp = 105.0
        elif i == 8: tp = 104.0
        elif i == 9: tp = 102.0
        elif i == 10: tp = 101.0
        elif i == 11: tp = 103.0
        
        if i == 5:
            cts = -0.90  # min_cts_10 = -0.90 (<= -0.85)
        elif i == 10:
            cts = -0.80
        elif i == 11:
            cts = -0.60  # surge = 0.20 (>= 0.18)
        else:
            cts = -0.40
            
        records.append({
            "high": tp,
            "low": tp,
            "close": tp,
            "open": tp,
            "range_pos_63": 0.25,      # falls in (0.2, 0.32]
            "cwc": 0.30,
            "cwc_slope": 0.03 if i == 11 else 0.01,  # > 0.02
            "psz_v": -0.12 if i == 11 else -0.02,    # <= -0.08
            "date": f"2026-05-{10+i:02d}",
            "atr_20": 1.0,
            "cts": cts,
            "fas": -0.5,
            "prt_slope": 0.1,
            "regime": "downtrend"
        })
    return records


def test_springboard_success():
    """Verify successful entry triggers when all SpringBoard conditions are met."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    
    passed, score, meta = entry_springboard(records[11], records[10], cfg, records, 11)
    
    assert passed is True
    assert score == 69
    assert meta["entry_tag"] == EntryTag.SPRINGBOARD.value
    assert "accepted" in meta["reason"].lower()


def test_springboard_disabled():
    """Verify path rejects signals when path is disabled in config."""
    cfg = SavgolCTSEntryConfig()
    cfg.springboard.enabled = False
    records = get_base_records()
    
    passed, score, meta = entry_springboard(records[11], records[10], cfg, records, 11)
    
    assert passed is False
    assert "disabled" in meta["reason"]


def test_springboard_rejection_range_pos():
    """Verify rejection when price is too high in the 63-bar range."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[11]["range_pos_63"] = 0.45  # Above 0.40 ceiling
    
    passed, score, meta = entry_springboard(records[11], records[10], cfg, records, 11)
    
    assert passed is False
    assert "rp_63" in meta["reason"] and "above ceiling" in meta["reason"]


def test_springboard_rejection_no_capitulation():
    """Verify rejection when there is no recent capitulation (CTS didn't reach <= -0.50)."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    # Remove the capitulation at index 5
    for r in records:
        r["cts"] = -0.30
        
    passed, score, meta = entry_springboard(records[11], records[10], cfg, records, 11)
    
    assert passed is False
    assert "no recent capitulation" in meta["reason"].lower()


def test_springboard_rejection_falling_knife():
    """Verify rejection by the price Spearman falling knife guard."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    
    # Force a rapid downward price crash to trigger falling knife guard
    for i in range(12):
        records[i]["high"] = 120.0 - i * 3.0
        records[i]["low"] = 118.0 - i * 3.0
        records[i]["close"] = 119.0 - i * 3.0
        
    passed, score, meta = entry_springboard(records[11], records[10], cfg, records, 11)
    
    assert passed is False
    assert "falling knife" in meta["reason"].lower()


def test_springboard_signal_integration():
    """Verify integration with the main SavgolCTSSignal orchestrator."""
    signal = SavgolCTSSignal()
    cfg = SavgolCTSEntryConfig()
    
    # Isolate SpringBoard path
    cfg.universal_cross.enabled = False
    cfg.trend_pullback_enabled = False
    cfg.flow_momentum.enabled = False
    cfg.coherent_pullback.enabled = False
    cfg.anchor_shock_pullback.enabled = False
    cfg.springboard.enabled = True
    
    records = get_base_records()
    
    passed, score, meta = signal.check_entry(records[11], records[10], cfg, records, 11)
    
    assert passed is True
    assert score == 69
    assert meta["entry_tag"] == EntryTag.SPRINGBOARD.value
