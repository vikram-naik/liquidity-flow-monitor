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

    @classmethod
    def from_int(cls, val: int) -> SavgolCTSExitState:
        return cls(
            cts_rose=bool(val & 1),
            psz_was_above=bool((val >> 1) & 1),
            cts_above_bt=bool((val >> 2) & 1),
            exit_suppressed=bool((val >> 3) & 1),
            suppressed_this_bar=bool((val >> 4) & 1),
        )

    def to_int(self) -> int:
        return (
            (int(self.suppressed_this_bar) << 4)
            | (int(self.exit_suppressed) << 3)
            | (int(self.cts_above_bt) << 2)
            | (int(self.psz_was_above) << 1)
            | int(self.cts_rose)
        )
