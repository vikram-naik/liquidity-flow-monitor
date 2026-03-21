from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from typing import Tuple

from src.trading.signals.base import BaseEntryConfig, BaseExitConfig, SignalInterface, Trade

@dataclass
class PriceDivergenceEntryConfig(BaseEntryConfig):
    """Stub configuration for Price Divergence Entry."""
    noise_threshold: float = 0.0002


@dataclass
class PriceDivergenceExitConfig(BaseExitConfig):
    """Stub configuration for Price Divergence Exit."""
    stop_loss_pct: float = 2.5
    stop_atr_multiple: float = 2
    noise_threshold: float = 0.0002


class PriceDivergenceSignal(SignalInterface):
    """
    Stub implementation for PriceDivergenceSignal.
    This signal will focus on pure price-based divergence metrics.
    """

    def check_entry(
        self,
        row: dict,
        prev_row: dict,
        cfg: BaseEntryConfig,
        records: list[dict] | None = None,
        idx: int = 0
    ) -> tuple[bool, int, dict]:
        """
        Stub entry check for price divergence.
        """
        if not isinstance(cfg, PriceDivergenceEntryConfig):
            raise TypeError("cfg must be PriceDivergenceEntryConfig")

        prev_prev_row = None
        if records is not None and idx >= 2:
            prev_prev_row = records[idx - 2]

        enter, intensity, reason = can_enter(row, prev_row, cfg.noise_threshold, prev_prev_row)
        return enter, int(intensity), {"reason": reason}

    def check_exit(
        self,
        row: dict,
        prev_row: dict,
        trade: Trade,
        peak_close: float,
        bars_held: int,
        delivery_bad_count: int,
        cwvap_values: list[float],
        cfg: BaseExitConfig,
        records: list[dict] | None = None,
        idx: int = 0,
    ) -> tuple[str | None, int]:
        """
        Stub exit check for price divergence.
        TODO: need to revisit the signature of this method.
        """
        if not isinstance(cfg, PriceDivergenceExitConfig):
            raise TypeError("cfg must be PriceDivergenceExitConfig")
        
        close = row["close"]
        entry = trade.entry_price
        atr_pct = trade.atr_at_entry / entry if entry > 0 else 0.02

        pnl_pct = (close / entry - 1) * 100
        peak_pnl_pct = (peak_close / entry - 1) * 100

        # 1. Hard stop-loss
        if pnl_pct < -(cfg.stop_atr_multiple * atr_pct * 100):
            return "hard_stop", 0

        qualifies, intensity, reason = can_exit(trade, row, prev_row, cfg, cwvap_values)
        if qualifies:
            return reason, int(intensity)
        
        return None, delivery_bad_count


#-------------------------------------------------------------



def can_enter(row: dict, prev_row: dict, noise_threshold: float = 0.01, prev_prev_row: dict | None = None) -> Tuple[bool, float, str]:
    """
    Evaluates if a LONG position should be initiated.
    Returns: (Should_Enter: bool, Intensity: float 0-100, Reason: str)
    """
    cts = row.get('cts', np.nan)
    slope = row.get('cts_slope', np.nan)
    accel = row.get('cts_accel', np.nan)
    prev_slope = prev_row.get('cts_slope', np.nan)
    prev_cts = prev_row.get('cts', np.nan)
    prev_prev_cts = prev_prev_row.get('cts', np.nan) if prev_prev_row else np.nan
    
    cmp = row.get('close', np.nan)
    open_px = row.get('open', np.nan)
    prev_cmp = prev_row.get('close', np.nan)
    psz = row.get('price_slope_z', np.nan)
    prev_psz = prev_row.get('price_slope_z', np.nan)

    enter = False
    intensity = 0.0
    reason = "Neutral/No Entry"
    #TODO: check the distance between prev_cts and cts should be greater than 0.05
    #need to emperically find out what should the threshold.

    # 1. The Confirmed Hook (Slope crosses zero upwards)
    if prev_slope <= 0 and slope > noise_threshold and cts < -0.15:
        enter = True
        reason = "Confirmed Upward Hook"
        # Intensity: Base 50 + bonus for high acceleration and deep value
        accel_bonus = max(0, accel * 500) # Assuming accel is small, scale it up
        value_bonus = max(0, (0 - cts) * 20) # Bonus if cts is negative
        intensity = 50 + accel_bonus + value_bonus

    # 2. The Early Bend (Still pointing down, but accelerating up violently)
    elif (
        -0.02 < slope < 0  # Slope has flattened out significantly
        and accel > (noise_threshold * 10)  # Strong acceleration up
        and cts < -0.3  # Still in deep value
        and cts > prev_cts  # Ticking up today
        and prev_cts > prev_prev_cts # Confirmation: ticked up yesterday as well
    ):
        enter = True
        reason = "Deep Bottom Reversal (Bending Up)"
        # Intensity: Driven purely by how hard the acceleration is pushing
        intensity = 40 + (accel * 600)

    # 3. Strong Trend Continuation (Everything points up)
    elif (
        slope > noise_threshold 
        and accel > noise_threshold 
        and cts > 0.02
        and cmp > open_px  # Green day (Close > Open)
        and cmp > prev_cmp # Higher close than yesterday
        and psz > prev_psz # Momentum is ticking up
    ):
        enter = True
        reason = "Trend Continuation (Accelerating Up)"
        # Intensity is lower here because the move is already underway (higher risk)
        intensity = 30 + (accel * 300)

    # 4. CTS above 0.2, trend at peak dont enter.
    if enter and cts > 0.2:
        enter = False
        intensity = 0.0
        reason = "Trend at peak"

    # Cap intensity at 100
    intensity = min(100.0, max(0.0, float(np.nan_to_num(intensity))))

    if enter and cts < 0.2:
        rsz = row.get('rdv_slope_z', np.nan)
        prev_rsz = prev_row.get('rdv_slope_z', np.nan)
        crossed = False
        thresholds = (-0.25, -0.20, -0.15, -0.10, -0.05, 0.01)
        
        # PSZ crossing check
        if not np.isnan(psz) and not np.isnan(prev_psz):
            for t in thresholds:
                if prev_psz < t <= psz:
                    crossed = True
                    break
            if not crossed and cts > 0:
                crossed = (prev_psz > 0 and psz > 0 and psz > prev_psz)

        # RSZ crossing check (if PSZ didn't cross)
        # if not crossed and not np.isnan(rsz) and not np.isnan(prev_rsz):
        #     for t in thresholds[:-1]: # Excludes 0.0 for RSZ
        #         if prev_rsz < t <= rsz:
        #             crossed = True
        #             break

        if not crossed:
            enter = False
            intensity = 0.0
            reason = "PSZ or RSZ not crossed"
            
    # Do not enter if intensity is too weak despite technical conditions
    if enter and intensity < 20:
        return False, float(intensity), "Signal too weak"

    return enter, float(intensity), reason


def can_exit(trade: Trade, row: dict, prev_row: dict, cfg: PriceDivergenceExitConfig, cwvap_values: list[float]) -> Tuple[bool, float, str]:
    """
    Evaluates if an existing LONG position should be closed.
    Returns: (Should_Exit: bool, Urgency/Intensity: float 0-100, Reason: str)
    """

    # Extract current and previous states    
    cts = row.get('cts', np.nan)
    cwvap = row.get('cwvap', np.nan)
    va_low = row.get('va_low', np.nan)
    va_high = row.get('va_high', np.nan)
    cmp = row.get('close', np.nan)
    slope = row.get('cts_slope', np.nan)
    accel = row.get('cts_accel', np.nan)
    prev_slope = prev_row.get('cts_slope', np.nan)
    date = row.get('date')

    cwvap_at_entry = cwvap_values[trade.entry_idx]
    entry_price = trade.entry_price
    
    exit_trade = False
    intensity = 0.0
    reason = "Hold"

    # 1. Strong Trend Bypass Logic (Prioritized)
    # If we are in the "Strong Zone" (Above VA High & CWVAP), only CTS momentum can trigger exit.
    if cmp > va_high and cmp > cwvap:
        if cts < 0.20 and slope < 0:
            return True, 40.0, "Strong Trend: CTS Momentum Fade (<0.20 and falling)"
        else:
            return False, 0.0, "Trailing Strong Trend (Above VA High)"

    # 2. Standard Exit Rules (Apply when NOT in Strong Zone bypass)
    
    # A. Top Exhaustion (Slope still up, but acceleration dies in overbought territory)
    if cts > 0.4 and slope > 0 and accel < -cfg.noise_threshold:
        exit_trade = True
        reason = "Top Exhaustion (Momentum dying)"
        intensity = 50 + (abs(accel) * 500) + (cts * 20)

    # B. Confirmed Downward Hook (Slope crosses zero downwards)
    elif prev_slope >= 0 and slope < -cfg.noise_threshold and cmp < cwvap:
        exit_trade = True
        reason = "Confirmed Downward Hook"
        intensity = 60 + (abs(accel) * 400)

    # C. Standard VWAP Breaches
    elif cmp < cwvap:
        if cwvap_at_entry > entry_price and cmp < va_low:
            exit_trade = True
            reason = "Entry below VWAP & Price below VWAP & VA Low"
            intensity = 20 + (abs(accel) * 500)
        elif cwvap_at_entry < entry_price and (cwvap - cmp) / cwvap > 0.025:
            exit_trade = True
            reason = "Entry above VWAP & Price below VWAP by 2.5%"
        else:
            # General breach
            exit_trade = True
            reason = "Price crossed below CWVAP"
            intensity = 30

    # Cap intensity at 100
    intensity = min(100.0, max(0.0, float(np.nan_to_num(intensity))))

    return exit_trade, float(intensity), reason

