import pytest
import numpy as np
from src.trading.signals.enums import EntryTag, ExitReason
from src.trading.signals.base import Trade
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.savgol_cts.entries.anchor_shock_pullback import entry_anchor_shock_pullback
from src.trading.signals.savgol_cts.exits.anchor_shock_pullback import exit_anchor_shock_pullback
from src.trading.signals.savgol_cts.signal import SavgolCTSSignal


def get_base_records():
    """Returns mock records satisfying all new Anchor-Shock-Pullback conditions on the last bar."""
    records = []
    for i in range(10):
        records.append({
            "high": 99.8,
            "low": 98.0,
            "close": 99.0,             # strictly below sdvwap (100.0)
            "open": 98.5,
            "sdvwap": 100.0,           # (100.0 - 99.0) / 100.0 = 1.0% <= 2.0%
            "dv_shock": -1.0,          # <= -0.8
            "esr": 0.05,               # <= 0.10 (consolidation)
            "pdd_120": 3.0,            # >= 2.0 (bullish structure)
            "pdd_30": -1.5,            # >= -3.0 (no crash)
            "range_pos_252": 0.50,     # <= 0.70 (accumulation zone)
            "price_slope_z": -0.10,    # >= -0.25 (momentum check)
            "range_width_252": 30.0,   # >= 20.0% (volatility check)
            "date": f"2026-05-{10+i:02d}",
            "atr_20": 1.0,
            "cts": -0.5,
            "regime": "uptrend"
        })
    return records


def test_anchor_shock_pullback_success():
    """Verify successful entry triggers when all statistical thresholds are met."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    
    passed, score, meta = entry_anchor_shock_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is True
    assert score == 83
    assert meta["entry_tag"] == EntryTag.ANCHOR_SHOCK_PULLBACK.value
    assert "accepted" in meta["reason"].lower()


def test_anchor_shock_pullback_disabled():
    """Verify path rejects signals when path is disabled in config."""
    cfg = SavgolCTSEntryConfig()
    cfg.anchor_shock_pullback.enabled = False
    records = get_base_records()
    
    passed, score, meta = entry_anchor_shock_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "disabled" in meta["reason"]


def test_anchor_shock_pullback_rejection_pdd120():
    """Verify rejection when secular PDD is too low."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["pdd_120"] = 1.5  # Below 2.0 threshold
    
    passed, score, meta = entry_anchor_shock_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "pdd_120" in meta["reason"] and "secular" in meta["reason"]


def test_anchor_shock_pullback_rejection_pdd30():
    """Verify rejection when short-term PDD indicates falling knife."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["pdd_30"] = -3.5  # Below -3.0 threshold
    
    passed, score, meta = entry_anchor_shock_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "pdd_30" in meta["reason"] and "falling knife" in meta["reason"]


def test_anchor_shock_pullback_rejection_above_sdvwap():
    """Verify price must be strictly below S-DVWAP."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["close"] = 100.5  # Above sdvwap (100.0)
    
    passed, score, meta = entry_anchor_shock_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "above or at" in meta["reason"].lower()


def test_anchor_shock_pullback_rejection_proximity():
    """Verify rejection when price is too far below S-DVWAP."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["close"] = 97.5  # (100 - 97.5)/100 = 2.5% > 2% threshold
    
    passed, score, meta = entry_anchor_shock_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "too far below" in meta["reason"].lower()


def test_anchor_shock_pullback_rejection_shock():
    """Verify rejection when delivery volume shock is not dry enough."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["dv_shock"] = -0.5  # Above -0.8 dry-up limit
    
    passed, score, meta = entry_anchor_shock_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "dv_shock" in meta["reason"] and "above maximum shock" in meta["reason"]


def test_anchor_shock_pullback_rejection_esr():
    """Verify rejection when spread efficiency is too high (lack of consolidation)."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["esr"] = 0.15  # Above 0.12 efficiency ceiling
    
    passed, score, meta = entry_anchor_shock_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "esr" in meta["reason"] and "above spread efficiency ceiling" in meta["reason"]


def test_anchor_shock_pullback_rejection_rp252():
    """Verify rejection when price position in 1-year range is too high (late stage peaks)."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["range_pos_252"] = 0.75  # Above 0.70 ceiling
    
    passed, score, meta = entry_anchor_shock_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "range_pos_252" in meta["reason"] and "above high-altitude ceiling" in meta["reason"]


def test_anchor_shock_pullback_rejection_psz():
    """Verify rejection when price slope z-score is too low (excessive downward momentum)."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["price_slope_z"] = -0.30  # Below -0.25 floor
    
    passed, score, meta = entry_anchor_shock_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "price_slope_z" in meta["reason"] and "below floor" in meta["reason"]


def test_anchor_shock_pullback_rejection_rw252():
    """Verify rejection when annual volatility range is too narrow (low rebound energy)."""
    cfg = SavgolCTSEntryConfig()
    records = get_base_records()
    records[9]["range_width_252"] = 15.0  # Below 20.0% floor
    
    passed, score, meta = entry_anchor_shock_pullback(records[9], records[8], cfg, records, 9)
    
    assert passed is False
    assert "range_width_252" in meta["reason"] and "below minimum requirement" in meta["reason"]


def test_exit_anchor_shock_pullback_climax():
    """Verify shock climax exit triggers correctly."""
    cfg = SavgolCTSExitConfig()
    cfg.anchor_shock_pullback.shock_exit_enabled = True
    trade = Trade(
        symbol="BEL",
        entry_date="2026-05-10",
        entry_price=100.0,
        entry_idx=0,
        atr_at_entry=1.0,
        entry_tag=EntryTag.ANCHOR_SHOCK_PULLBACK.value,
        psz_at_entry=0.0
    )
    
    # 1. Climax shock exits
    row = {"close": 105.0, "dv_shock": 2.5}
    reason, state = exit_anchor_shock_pullback(row, {}, trade, 105.0, 5, 0, cfg.anchor_shock_pullback)
    assert reason == ExitReason.STRUCTURAL_CLIMAX
    
    # 2. Climax disabled/not met
    row = {"close": 105.0, "dv_shock": 1.5}
    reason, state = exit_anchor_shock_pullback(row, {}, trade, 105.0, 5, 0, cfg.anchor_shock_pullback)
    assert reason is None


def test_exit_anchor_shock_pullback_fallback():
    """Verify hard stop and time decay fallback exits trigger correctly."""
    cfg = SavgolCTSExitConfig()
    trade = Trade(
        symbol="BEL",
        entry_date="2026-05-10",
        entry_price=100.0,
        entry_idx=0,
        atr_at_entry=1.0,
        entry_tag=EntryTag.ANCHOR_SHOCK_PULLBACK.value,
        psz_at_entry=0.0
    )
    
    # 1. Hard stop exit
    row = {"close": 84.5, "dv_shock": 0.0}  # Loss = -15.5% >= -15.0%
    reason, state = exit_anchor_shock_pullback(row, {}, trade, 100.0, 5, 0, cfg.anchor_shock_pullback)
    assert reason == ExitReason.HARD_STOP
    
    # 2. Time decay exit
    row = {"close": 101.0, "dv_shock": 0.0}
    reason, state = exit_anchor_shock_pullback(row, {}, trade, 101.0, 51, 0, cfg.anchor_shock_pullback) # 51 bars >= 50 bar limit
    assert reason == ExitReason.TIME_DECAY


def test_signal_integration():
    """Verify integration of both entry and exit paths in orchestrator."""
    signal = SavgolCTSSignal()
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    
    # Isolate ASP entry path
    entry_cfg.universal_cross.enabled = False
    entry_cfg.trend_pullback_enabled = False
    entry_cfg.flow_momentum.enabled = False
    entry_cfg.coherent_pullback.enabled = False
    entry_cfg.anchor_shock_pullback.enabled = True
    
    records = get_base_records()
    
    # Verify entry integration
    passed, score, meta = signal.check_entry(records[9], records[8], entry_cfg, records, 9)
    assert passed is True
    assert score == 83
    assert meta["entry_tag"] == EntryTag.ANCHOR_SHOCK_PULLBACK.value
    
    # Verify exit integration - Crossover Exit
    trade = Trade(
        symbol="BEL",
        entry_date="2026-05-10",
        entry_price=100.0,
        entry_idx=0,
        atr_at_entry=1.0,
        entry_tag=EntryTag.ANCHOR_SHOCK_PULLBACK.value,
        psz_at_entry=0.0
    )
    
    row = {"close": 105.0, "cts": -0.8, "cts_sell_threshold": 0.0}
    prev_row = {"cts": 0.5, "cts_sell_threshold": 0.0}
    reason, state = signal.check_exit(row, prev_row, trade, 105.0, 5, 0, [], exit_cfg)
    assert reason == "ExitReason.ST_CROSS"

    # Verify exit integration - Hard Stop Exit
    row = {"close": 84.5, "cts": 0.5, "cts_sell_threshold": 0.0}
    prev_row = {"cts": 0.5, "cts_sell_threshold": 0.0}
    reason, state = signal.check_exit(row, prev_row, trade, 105.0, 5, 0, [], exit_cfg)
    assert reason == "ExitReason.HARD_STOP"

    # Verify exit integration - Time Decay Exit
    row = {"close": 101.0, "cts": 0.5, "cts_sell_threshold": 0.0}
    prev_row = {"cts": 0.5, "cts_sell_threshold": 0.0}
    reason, state = signal.check_exit(row, prev_row, trade, 105.0, 51, 0, [], exit_cfg)
    assert reason == "ExitReason.TIME_DECAY"
