from enum import Enum

class EntryTag(str, Enum):
    """Strongly typed entry tags with presentation-friendly strings."""
    SLOPE_BOTTOM = "SavgolCTS Slope-Bottom"
    CTS_BT_FLOOR = "SavgolCTS CTS-BT-Floor"  # Legacy

    # Deprecated — retained for historical trade log compatibility.
    PSZ = "SavgolCTS PSZ Bend"               # Entry path removed (2026-03-28)
    PSZV_FLAT = "SavgolCTS PSZv-Flat"         # Entry path removed (2026-03-28)
    
    # Generic
    UNKNOWN = "Unknown Entry"


class ExitReason(str, Enum):
    """Strongly typed exit reasons with presentation-friendly strings."""
    # SavgolCTS specific
    CEILING_HIT = "CTS ceiling hit"
    FLOOR_HIT = "CTS hit floor"
    BT_HIT = "CTS hit BT"
    PSZ_GLIDE = "PSZ glide exit"
    PSZ_STALL = "PSZ stall early exit"
    CWVAP_EXHAUSTION = "CWVAP momentum exhaustion"
    SUPPRESSED_EXIT = "Suppressed exit triggered (price barrier)"
    BAR3_STOP = "Bar-3 PnL stop"
    BAR5_STOP = "Bar-5 PnL stop"
    LH_LL_BREAK = "LH+LL trend break"
    ST_CROSS = "CTS crossed ST down"
    CWVAP_LOST = "Close below CWVAP"
    SLOPE_CYCLE = "CTS slope cycle complete"
    PNL_CAP = "PnL cap hit"
    
    # Universal / Generic
    END_OF_DATA = "End of Data (Open Trade)"
    HARD_STOP = "Hard Stop Hit"
    TRAIL_STOP = "Trailing Stop Hit"
    TIME_DECAY = "Time Decay Max Hold Reached"
