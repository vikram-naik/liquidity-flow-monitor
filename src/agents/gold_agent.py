import yfinance as yf
import requests
import pandas as pd
from datetime import datetime
import pytz

# Headers to mimic a browser
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

def fetch_nse_price(symbol="GOLDBEES"):
    """
    Fetches the live price from NSE (via Yahoo Finance or fallback).
    Returns dict: {'price': float, 'timestamp': datetime}
    """
    # Retry mechanism for robustness
    max_retries = 3
    for attempt in range(max_retries):
        try:
            ticker = yf.Ticker(f"{symbol}.NS")
            price = ticker.fast_info.get('lastPrice')
            
            # 1. Validation: Ignore 0.0 or None
            if price is None or price <= 0:
                print(f"[gold_agent] Invalid price {price} on attempt {attempt+1}. Retrying...")
                # Fallback to history only if fast_info fails
                hist = ticker.history(period="1d")
                if not hist.empty:
                    last_close = hist['Close'].iloc[-1]
                    if last_close > 0:
                        price = last_close
            
            # 2. Final Check
            if price and price > 0:
                return {
                    'price': round(float(price), 2),
                    'timestamp': datetime.now()
                }
        except Exception as e:
            print(f"[gold_agent] NSE Fetch Error (Attempt {attempt+1}): {e}")
            
    # If all retries fail
    return None

def fetch_nippon_inav(scheme_name="Nippon India ETF Gold BeES"):
    """
    Fetches the Indicative NAV (iNAV) from Nippon India's API.
    Returns dict: {'inav': float, 'updated_at': str}
    """
    url = "https://etf.nipponindiaim.com/RealtimeNAV/Nav/DetailsFill"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://etf.nipponindiaim.com/RealtimeNAV/nav/index",
        "Origin": "https://etf.nipponindiaim.com"
    }
    
    try:
        response = requests.post(url, headers=headers, json={}, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # The API returns a list in RVDetailsList
            for fund in data.get("RVDetailsList", []):
                if fund.get('SchName') == scheme_name:
                    inav_val = fund.get('CNav')
                    time_str = fund.get('Realdt')
                    
                    if inav_val:
                        return {
                            'inav': float(inav_val),
                            'updated_at': time_str
                        }
    except Exception as e:
         print(f"[gold_agent] Nippon iNAV fetch error: {e}")
         
    return None

def fetch_global_parity_metrics() -> dict | None:
    """
    Fetches Gold Futures (GC=F) and USDINR (INR=X) from Yahoo Finance.
    Returns dict with spot prices and calculated parity.
    
    Formula:
    1 Oz = 31.1034768 grams.
    GoldBees Unit = approx 0.01 grams.
    
    Parity (INR/Unit) = ((Gold_USD_Oz * USDINR) / 31.1035) * 0.01
    """
    try:
        # GC=F is Gold Futures (Comex)
        gold = yf.Ticker("GC=F")
        usdinr = yf.Ticker("INR=X")
        
        gold_price = gold.fast_info.get('lastPrice') or gold.history(period='1d')['Close'].iloc[-1]
        inr_rate = usdinr.fast_info.get('lastPrice') or usdinr.history(period='1d')['Close'].iloc[-1]
        
        if gold_price and inr_rate:
            OZ_TO_GRAM = 31.1034768
            # Price per gram in INR
            gram_price_inr = (gold_price * inr_rate) / OZ_TO_GRAM
            # Price per GoldBees Unit (0.01g)
            unit_parity = gram_price_inr * 0.01
            
            return {
                'spot_usd': round(float(gold_price), 2),
                'usdinr': round(float(inr_rate), 4),
                'parity_inr': round(unit_parity, 4)
            }
            
    except Exception as e:
        print(f"[gold_agent] Global parity fetch error: {e}")
    
    return None

# --- CLI Test ---
if __name__ == "__main__":
    print("Testing Gold Agent...")
    print("1. NSE Price:", fetch_nse_price())
    print("2. Nippon iNAV:", fetch_nippon_inav())
    print("3. Global Parity:", fetch_global_parity_metrics())
