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
    slope_crossed_zero: bool = False  # Track if structural momentum crossed above zero
    price_above_cwvap: bool = False    # Track if price has reclaimed CWVAP post-entry
    exit_suppressed_ext: bool = False  # Track if a path-specific exit was suppressed
    fas_crossed_zero: bool = False     # Track if FAS crossed above zero
    extreme_bottom_extension: bool = False # Track if trade hit absolute floor (grant 2x timeout)
    cwf_count: int = 0            # Counter for High > CWVAP and Close < CWVAP (4 bits: 0-15)
    climax_hit_above_va: bool = False # Track if structural climax hit while price > va_high
    cts_near_miss: bool = False    # Track if CTS barely touched ST (Bare-Touch persistence)
    psz_second_cycle: bool = False # Track if PSZ went positive again during Phase 2

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
            exit_suppressed_ext=bool((val >> 7) & 1),
            fas_crossed_zero=bool((val >> 8) & 1),
            extreme_bottom_extension=bool((val >> 9) & 1),
            cwf_count=int((val >> 13) & 0xF),
            climax_hit_above_va=bool((val >> 17) & 1),
            cts_near_miss=bool((val >> 18) & 1),
            psz_second_cycle=bool((val >> 19) & 1),
        )

    def to_int(self) -> int:
        return (
            (int(self.psz_second_cycle) << 19)
            | (int(self.cts_near_miss) << 18)
            | (int(self.climax_hit_above_va) << 17)
            | ((self.cwf_count & 0xF) << 13)
            | (int(self.extreme_bottom_extension) << 9)
            | (int(self.fas_crossed_zero) << 8)
            | (int(self.exit_suppressed_ext) << 7) 
            | (int(self.price_above_cwvap) << 6)
            | (int(self.slope_crossed_zero) << 5)
            | (int(self.suppressed_this_bar) << 4)
            | (int(self.exit_suppressed) << 3)
            | (int(self.cts_above_bt) << 2)
            | (int(self.psz_was_above) << 1)
            | int(self.cts_rose)
        )
