from enum import Enum

class EntryTag(str, Enum):
    """Strongly typed entry tags with presentation-friendly strings."""
    # Active Paths
    CDVL_CTS = "SavgolCTS CDVL-CTS"
    UNIVERSAL_CROSS = "SavgolCTS Universal-Cross"
    TREND_PULLBACK = "SavgolCTS Trend-Pullback"
    FLOW_MOMENTUM = "SavgolCTS Flow-Momentum"
    COHERENT_PULLBACK = "SavgolCTS Coherent-Pullback"
    ANCHOR_SHOCK_PULLBACK = "SavgolCTS Anchor-Shock-Pullback"
    SPRINGBOARD = "SavgolCTS SpringBoard"
    
    # Generic / Fallback
    UNKNOWN = "Unknown Entry"

    # Deprecated — retained for historical trade log compatibility.
    PRT_ZERO_CROSS = "SavgolCTS PRT-Zero-Cross"        # Renamed to UNIVERSAL_CROSS (2026-05-10)
    CTS_FLOOR_REVERSION = "SavgolCTS CTS-Floor-Reversion" # Removed (2026-05-10)
    INSTITUTIONAL_FLOOR = "SavgolCTS Institutional-Floor" # Removed (2026-05-10)
    ACCEL = "SavgolCTS Accel-Cross"                      # Removed (2026-05-10)
    RANGE_REVERSION = "SavgolCTS Range-Reversion"        # Removed (2026-05-10)
    FAS_ZERO_CROSS = "SavgolCTS FAS-Zero-Cross"          # Removed (2026-05-10)
    FAS_FLOOR_REVERSION = "SavgolCTS FAS-Floor-Reversion" # Removed (2026-05-10)
    FAS_BUY_CROSS = "SavgolCTS FAS-Buy-Cross"            # Removed (2026-05-10)
    CTS_ACCEL_CROSS = "SavgolCTS CTS-Accel-Cross"        # Removed (2026-05-10)
    CTS_BT_FLOOR = "SavgolCTS CTS-BT-Floor"              # Removed (2026-05-10)
    PSZ = "SavgolCTS PSZ Bend"                          # Removed (2026-03-28)
    PSZV_FLAT = "SavgolCTS PSZv-Flat"                    # Removed (2026-03-28)


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
    GAP_DOWN_LOSS = "Gap down while in loss"
    NEGATIVE_PNL_TIMEOUT = "Negative PnL timeout exit"
    PRT_ST_CROSS = "PRT crossed PRT_ST down"
    PRT_SLOPE_NEGATIVE = "PRT slope turned negative"
    CWC_SLOPE_EARLY_RELEASE = "CWC slope early release"
    CTS_NEAR_MISS_ROLLOVER = "CTS near-miss rollover"
    
    # Universal / Generic
    END_OF_DATA = "End of Data (Open Trade)"
    HARD_STOP = "Hard Stop Hit"
    INITIAL_STOP = "Initial Stop Hit"
    TRAIL_STOP = "Trailing Stop Hit"
    TIME_DECAY = "Time Decay Max Hold Reached"
    TIME_FAIL = "Time Failure"
