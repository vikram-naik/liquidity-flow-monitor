"""
Position sizing — equal-weight with ATR risk cap.
"""


def calculate_quantity(
    capital: float,
    max_positions: int,
    entry_price: float,
    atr: float,
    stop_atr_multiple: float = 2.0,
) -> int:
    """Calculate position size capped by allocation and per-trade risk.

    Args:
        capital: Total portfolio capital.
        max_positions: Maximum concurrent positions (for equal-weight split).
        entry_price: Expected entry price per share.
        atr: ATR at entry (absolute, not percentage).
        stop_atr_multiple: Stop distance in ATR multiples.

    Returns:
        Number of shares to buy (integer, >= 0).
    """
    if entry_price <= 0 or atr <= 0 or max_positions <= 0:
        return 0

    per_position = capital / max_positions
    risk_per_share = stop_atr_multiple * atr
    max_risk = capital * 0.02  # 2% portfolio risk cap per trade
    max_shares_by_risk = int(max_risk / risk_per_share)
    shares_by_allocation = int(per_position / entry_price)
    return min(shares_by_allocation, max_shares_by_risk)
