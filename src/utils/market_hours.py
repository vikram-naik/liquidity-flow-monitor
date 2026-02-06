from datetime import datetime
import pytz

def is_nse_market_open():
    """
    Checks if Indian market (NSE) is currently open.
    Market hours: 9:15 AM - 3:30 PM IST, Monday to Friday.
    Returns: (is_open: bool, status_message: str)
    """
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    
    # Check if weekend
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False, f"🌙 Market Closed (Weekend). Last updated: {now.strftime('%d %b %Y, %I:%M %p IST')}"
    
    # Market hours: 9:15 AM to 3:30 PM
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    if now < market_open:
        return False, f"🌅 Market opens at 9:15 AM IST. Current time: {now.strftime('%I:%M %p IST')}"
    elif now > market_close:
        return False, f"🌙 Market closed at 3:30 PM IST. Current time: {now.strftime('%I:%M %p IST')}"
    
    return True, "🟢 Market Open"
