"""PDD Divergence Exit.

Tailored exit strategy for shallow bases that protects the downside but allows
massive trailing run-ups (like BEL in early 2026).
"""

from __future__ import annotations

import numpy as np

from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig


def exit_pdd_divergence(
    row: dict, prev: dict, trade: Trade, peak_close: float,
    bars_held: int, exit_cfg: SavgolCTSExitConfig,
) -> tuple[ExitReason | str | None, int]:
    """Check exits for PDD Divergence entry.

    Uses CWVAP ride strategy:
    - -3.0% hard stop loss
    - Suppressed structural exits when close >= CWVAP * 0.99
    - Loss of CWVAP support exit (close < CWVAP * 0.99 after reclaim)
    - Deep MFE trailing stop (15% drop from peak once MFE > 15%)
    """
    cfg = exit_cfg.pdd_divergence
    
    close = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)
    cs = row.get("cts_slope", np.nan)
    pcs = prev.get("cts_slope", np.nan)
    
    if np.isnan(close) or np.isnan(cwvap) or np.isnan(cs) or np.isnan(pcs):
        return None, 0
        
    # Optional trailing cap check (we let the advanced trailing logic handle it)
    # Could be added to config if needed, but not included here by default
        
    # 1. Hard Stop
    if close < trade.entry_price * (1.0 + cfg.hard_stop_pct / 100.0):
        return "Stop Loss Hit", 0
        
    # 2. Structural Exit: Slope Cycle Complete
    structural_exit = None
    if pcs > 0 and cs < 0:
        structural_exit = "Slope Cycle Complete"
        
    # Suppress structural exit if above CWVAP + tolerance
    if structural_exit:
        if close >= cwvap * cfg.cwvap_suppress_tolerance:
            structural_exit = None # Suppressed
            
    if structural_exit:
        return structural_exit, 0
        
    # 3. Lost CWVAP Support
    # We consider 'reclaimed' if previous close was > previous CWVAP
    prev_close = prev.get("close", 0.0)
    prev_cwvap = prev.get("cwvap", 0.0)
    
    if bars_held > 3 and prev_close > prev_cwvap:
        if close < cwvap * cfg.cwvap_lost_tolerance:
            return "Lost CWVAP Support", 0
            
    # 4. Deep MFE Trailing Stop
    # Lock in really massive gains so we don't ride them all the way down
    if trade.mfe_pct > cfg.mfe_trail_activation_pct:
        peak = trade.entry_price * (1 + trade.mfe_pct / 100.0)
        if close < peak * cfg.mfe_trail_lock_ratio:
            return "Deep MFE Trailing Stop", 0
            
    return None, 0
