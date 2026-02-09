"""
Silver ETF Data Agent
Fetches live price and iNAV data for Silver ETFs (SILVERBEES) from NSE and Nippon India.
"""

import requests
from datetime import datetime


def get_browser_headers():
    """Returns browser-like headers to avoid blocks."""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': '*/*'
    }


def fetch_nse_price(symbol: str = "SILVERBEES") -> dict | None:
    """
    Fetches the Last Traded Price from NSE India.
    
    Args:
        symbol: NSE symbol (default: SILVERBEES)
    
    Returns:
        dict with 'price', 'timestamp', 'change', 'pchange' or None on failure
    """
    base_url = "https://www.nseindia.com/"
    api_url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
    session = requests.Session()
    session.headers.update(get_browser_headers())
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # First request to get cookies
            session.get(base_url, timeout=5)
            
            # Actual API call
            r = session.get(api_url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                price_info = data.get('priceInfo', {})
                price = float(price_info.get('lastPrice', 0))
                
                # Validate: Ignore 0.0 price
                if price > 0:
                    return {
                        'price': price,
                        'change': float(price_info.get('change', 0)),
                        'pchange': float(price_info.get('pChange', 0)),
                        'timestamp': datetime.now().isoformat()
                    }
                else:
                    print(f"[silver_agent] Invalid price {price} on attempt {attempt+1}. Retrying...")
        except Exception as e:
            print(f"[silver_agent] NSE fetch error (Attempt {attempt+1}): {e}")
    
    return None


def fetch_nippon_inav(scheme_name: str = "Nippon India Silver ETF") -> dict | None:
    """
    Fetches the indicative NAV (CNav) from Nippon India's internal API.
    
    API Endpoint: https://etf.nipponindiaim.com/RealtimeNAV/Nav/DetailsFill
    
    Response Fields:
        - CNav: Current/Indicative NAV (live value)
        - PNav: Previous Close NAV
        - NCvalue: Net Change (CNav - PNav)
        - PChange: Percentage Change
    
    Args:
        scheme_name: Scheme name to search for (default: Nippon India Silver ETF)
    
    Returns:
        dict with 'inav', 'prev_nav', 'change', 'pchange', 'updated_at' or None on failure
    """
    url = "https://etf.nipponindiaim.com/RealtimeNAV/Nav/DetailsFill"
    headers = get_browser_headers()
    headers['Content-Type'] = 'application/json'
    headers['X-Requested-With'] = 'XMLHttpRequest'
    headers['Referer'] = 'https://etf.nipponindiaim.com/RealtimeNAV/nav/index'
    headers['Origin'] = 'https://etf.nipponindiaim.com'
    
    try:
        r = requests.post(url, headers=headers, json={}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            for item in data.get("RVDetailsList", []):
                if scheme_name.lower() in item.get("SchName", "").lower():
                    return {
                        'inav': float(item.get("CNav", 0)),
                        'prev_nav': float(item.get("PNav", 0)),
                        'change': float(item.get("NCvalue", 0)),
                        'pchange': item.get("PChange", "0%"),
                        'updated_at': item.get("Realdt", ""),
                        'scheme_name': item.get("SchName", "")
                    }
    except Exception as e:
        print(f"[silver_agent] Nippon fetch error: {e}")
    
    return None


def calculate_tracking_error(price: float, inav: float) -> dict:
    """
    Calculates tracking error between traded price and iNAV.
    
    Args:
        price: Last traded price (from NSE)
        inav: Indicative NAV (from Nippon)
    
    Returns:
        dict with 'spread', 'spread_pct', 'status' (premium/discount/fair)
    """
    spread = price - inav
    spread_pct = (spread / inav) * 100 if inav else 0
    
    if spread > 0.1:
        status = "premium"
    elif spread < -0.1:
        status = "discount"
    else:
        status = "fair"
    
    return {
        'spread': round(spread, 4),
        'spread_pct': round(spread_pct, 4),
        'status': status
    }


# --- CLI Test ---
if __name__ == "__main__":
    print("Fetching SILVERBEES data...")
    
    nse_data = fetch_nse_price("SILVERBEES")
    nippon_data = fetch_nippon_inav("Nippon India Silver ETF")
    
    print(f"\nNSE Price: {nse_data}")
    print(f"Nippon iNAV: {nippon_data}")
    
    if nse_data and nippon_data:
        error = calculate_tracking_error(nse_data['price'], nippon_data['inav'])
        print(f"\nTracking Error: {error}")


def fetch_global_parity_metrics() -> dict | None:
    """
    Fetches Global Silver Spot (USD/Oz) and USD/INR exchange rate from Yahoo Finance.
    Calculates the parity price in INR/gm.
    
    Conversion: 1 Troy Ounce = 31.1034768 grams
    Formula: Parity (INR/gm) = (Silver USD/Oz * USDINR) / 31.1035
    
    Returns:
        dict with 'spot_usd', 'usdinr', 'parity_inr' or None on failure
    """
    import yfinance as yf
    
    try:
        # Fetch Silver Futures (SI=F) and USD/INR (INR=X)
        silver = yf.Ticker("SI=F")
        usdinr = yf.Ticker("INR=X")
        
        # Get latest close price
        silver_price = silver.fast_info.get('lastPrice') or silver.history(period='1d')['Close'].iloc[-1]
        inr_rate = usdinr.fast_info.get('lastPrice') or usdinr.history(period='1d')['Close'].iloc[-1]
        
        if silver_price and inr_rate:
            # Conversion: 1 Troy Oz = 31.1034768 grams
            OZ_TO_GRAM = 31.1034768
            parity_inr = (silver_price * inr_rate) / OZ_TO_GRAM
            
            return {
                'spot_usd': round(float(silver_price), 2),
                'usdinr': round(float(inr_rate), 4),
                'parity_inr': round(parity_inr, 4)
            }
    except Exception as e:
        print(f"[silver_agent] Global parity fetch error: {e}")
    
    return None
