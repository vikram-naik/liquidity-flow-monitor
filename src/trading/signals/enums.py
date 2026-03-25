from enum import Enum

class EntryTag(str, Enum):
    """Strongly typed entry tags with presentation-friendly strings."""
    CTS_FLOOR_LEAVE = "SavgolCTS CTS-Floor-Leave"
    PSZ = "SavgolCTS PSZ Bend"
    BT_CROSS = "SavgolCTS BT-Cross"
    PSZV_FLAT = "SavgolCTS PSZv-Flat"
    CTS_FLOOR_TOUCH = "SavgolCTS Floor-Touch"
    CTS_BT_FLOOR = "SavgolCTS CTS-BT-Floor" # Legacy
    
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
    ST_CROSS = "CTS crossed ST down"
    
    # Universal / Generic
    END_OF_DATA = "End of Data (Open Trade)"
    HARD_STOP = "Hard Stop Hit"
    TRAIL_STOP = "Trailing Stop Hit"
    TIME_DECAY = "Time Decay Max Hold Reached"
