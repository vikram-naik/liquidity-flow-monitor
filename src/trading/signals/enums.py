from enum import Enum

class EntryTag(str, Enum):
    """Strongly typed entry tags with presentation-friendly strings."""
    CTS_FLOOR_REVERSION = "SavgolCTS CTS-Floor-Reversion"
    INSTITUTIONAL_FLOOR = "SavgolCTS Institutional-Floor"
    ACCEL = "SavgolCTS Accel-Cross"
    RANGE_REVERSION = "SavgolCTS Range-Reversion"
    FAS_ZERO_CROSS = "SavgolCTS FAS-Zero-Cross"
    FAS_FLOOR_REVERSION = "SavgolCTS FAS-Floor-Reversion"
    FAS_BUY_CROSS = "SavgolCTS FAS-Buy-Cross"
    CTS_ACCEL_CROSS = "SavgolCTS CTS-Accel-Cross"
    PRT_ZERO_CROSS = "SavgolCTS PRT-Zero-Cross"
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
    CTS_BARE_TOUCH = "CTS near-miss stall"
    CTS_ZERO_DOWN = "CTS crossed zero down"
    CWVAP_LOST = "Close below CWVAP"
    RECLAIM_TIMEOUT = "CWVAP reclaim timeout"
    SLOPE_CYCLE = "CTS slope cycle complete"
    PNL_CAP = "PnL cap hit"
    FAS_FLOOR = "FAS floor breach"
    DUAL_ENGINE_FAILURE = "Dual engine failure (CTS & FAS)"
    FAS_CLIMAX = "FAS climax overextension (> 1.0)"
    ALPHA_RELEASE_EXIT = "Alpha release (momentum decay post-profit)"
    OVEREXTENDED_ENGINE_FAILURE = "Overextended engine failure (trailing gain)"
    NEGATIVE_PNL_ENGINE_FAILURE = "Negative PnL engine failure (loss prevention)"
    CANDLE_REJECTION = "Candlestick structural rejection at CWVAP"
    INSIDE_BAR_REJECTION = "Inside bar on extreme volume at CWVAP"
    CWVAP_REJECTION = "CWVAP rejection limit reached"
    STRUCTURAL_CLIMAX = "Structural climax (Range exhaustion + Overextension)"
    
    # Universal / Generic
    END_OF_DATA = "End of Data (Open Trade)"
    HARD_STOP = "Hard Stop Hit"
    INITIAL_STOP = "Initial Stop Hit"
    TRAIL_STOP = "Trailing Stop Hit"
    TIME_DECAY = "Time Decay Max Hold Reached"
    TIME_FAIL = "Time Failure"
