"""Per-trade exit state — bitfield packed into the ``delivery_bad_count`` int.

The base ``SignalInterface.check_exit`` signature passes an int
``delivery_bad_count``.  SavgolCTS repurposes this as a 5-bit state
tracking per-trade exit progression.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SavgolCTSExitState:
    """Helper to manage the delivery_bad_count bitfield state."""
    cts_rose: bool = False
    psz_was_above: bool = False
    cts_above_bt: bool = False
    exit_suppressed: bool = False
    suppressed_this_bar: bool = False
    slope_crossed_zero: bool = False  # Track if slope has crossed above zero (Slope Bottom phase 1 complete)
    price_above_cwvap: bool = False    # Track if price has reclaimed CWVAP post-entry
    prt_exit_suppressed: bool = False  # Track if the prt exit was suppressed.
    fas_crossed_zero: bool = False   # Track if the prt crosses zero
    extreme_bottom_extension: bool = False # Track if trade hit absolute floor (grant 2x timeout)
    recovery_passed: bool = False  # Track if either indicator has crossed above zero
    cts_exhausted: bool = False    # Track if CTS has exhausted post-recovery
    fas_exhausted: bool = False    # Track if FAS has exhausted post-recovery
    cwf_count: int = 0            # Counter for High > CWVAP and Close < CWVAP (4 bits: 0-15)
    climax_hit_above_va: bool = False # Track if structural climax hit while price > va_high

    @classmethod
    def from_int(cls, val: int) -> SavgolCTSExitState:
        return cls(
            cts_rose=bool(val & 1),
            psz_was_above=bool((val >> 1) & 1),
            cts_above_bt=bool((val >> 2) & 1),
            exit_suppressed=bool((val >> 3) & 1),
            suppressed_this_bar=bool((val >> 4) & 1),
            slope_crossed_zero=bool((val >> 5) & 1),
            price_above_cwvap=bool((val >> 6) & 1),
            prt_exit_suppressed=bool((val >> 7) & 1),
            fas_crossed_zero=bool((val >> 8) & 1),
            extreme_bottom_extension=bool((val >> 9) & 1),
            recovery_passed=bool((val >> 10) & 1),
            cts_exhausted=bool((val >> 11) & 1),
            fas_exhausted=bool((val >> 12) & 1),
            cwf_count=int((val >> 13) & 0xF),
            climax_hit_above_va=bool((val >> 17) & 1),
        )

    def to_int(self) -> int:
        return (
            (int(self.climax_hit_above_va) << 17)
            | ((self.cwf_count & 0xF) << 13)
            | (int(self.fas_exhausted) << 12)
            | (int(self.cts_exhausted) << 11)
            | (int(self.recovery_passed) << 10)
            | (int(self.extreme_bottom_extension) << 9)
            | (int(self.fas_crossed_zero) << 8)
            | (int(self.prt_exit_suppressed) << 7) 
            | (int(self.price_above_cwvap) << 6)
            | (int(self.slope_crossed_zero) << 5)
            | (int(self.suppressed_this_bar) << 4)
            | (int(self.exit_suppressed) << 3)
            | (int(self.cts_above_bt) << 2)
            | (int(self.psz_was_above) << 1)
            | int(self.cts_rose)
        )
