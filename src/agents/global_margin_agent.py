
import os
import sys
import time
import sqlite3
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from datetime import datetime, date

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

import yfinance as yf
from src.utils.trading_calendar import is_cme_trading_day

MULTIPLIERS = {
    'GOLD': 100,
    'SILVER': 5000,
    'COPPER': 25000,
    'ES': 50,
    'NQ': 20
}

YAHOO_SYMBOLS = {
    'GOLD': 'GC=F',
    'SILVER': 'SI=F',
    'COPPER': 'HG=F',
    'ES': 'ES=F',
    'NQ': 'NQ=F'
}

DB_PATH = "liquidity_monitor.db"


def get_cme_margins():
    """Fetch CME margins - NO MOCKING"""
    
    # Check if today is a trading day
    # today = date.today()
    # if not is_cme_trading_day(today):
    #     print(f"⚠️ WARNING: {today} is not a CME trading day. Skipping.")
    #     return
    pass

    # Targets
    targets = [
        {'symbol': 'SILVER', 'code': 'SI', 'exchange': 'CMX', 'sector': 'METALS'},
        {'symbol': 'GOLD', 'code': 'GC', 'exchange': 'CMX', 'sector': 'METALS'},
        {'symbol': 'COPPER', 'code': 'HG', 'exchange': 'CMX', 'sector': 'METALS'},
        {'symbol': 'ES', 'code': 'ES', 'exchange': 'CME', 'sector': 'EQUITY INDEX'},
        {'symbol': 'NQ', 'code': 'NQ', 'exchange': 'CME', 'sector': 'EQUITY INDEX'}
    ]

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)
    
    try:
        found_any = False
        for t in targets:
            url = f"https://www.cmegroup.com/CmeWS/mvc/Margins/OUTRIGHT?1=1&sortField=exchange&sortAsc=true&clearingCode={t['code']}&sector={t['sector']}&exchange={t['exchange']}&pageSize=12&pageNumber=1&isProtected"
            print(f"Fetching {t['symbol']} from CME API via Selenium...")
            
            try:
                driver.get(url)
                time.sleep(3)
                
                content = driver.find_element(By.TAG_NAME, "body").text
                data = json.loads(content)
                
                if 'marginRates' in data and data['marginRates']:
                    rate_obj = data['marginRates'][0]
                    # Equities often use 'maintenanceLong', Futures 'maintenanceRate'
                    margin_raw = rate_obj.get('maintenanceRate') or rate_obj.get('maintenanceLong')
                    
                    if margin_raw:
                        if isinstance(margin_raw, str):
                           margin_val = float(margin_raw.replace(',', '').replace(' USD', '').strip())
                        else:
                           margin_val = float(margin_raw)

                        print(f"  > Found {t['symbol']} Margin: ${margin_val}")
                        
                        # Fetch Price and Calculate %
                        try:
                            yf_sym = YAHOO_SYMBOLS.get(t['symbol'])
                            price = 0.0
                            margin_pct = 0.0
                            if yf_sym:
                                ticker = yf.Ticker(yf_sym)
                                # Get fast price
                                hist = ticker.history(period="1d")
                                if not hist.empty:
                                    price = hist['Close'].iloc[-1]
                                    multiplier = MULTIPLIERS.get(t['symbol'], 1)
                                    contract_val = price * multiplier
                                    if contract_val > 0:
                                        margin_pct = (margin_val / contract_val) * 100
                                        print(f"  > Price: {price}, Value: ${contract_val:,.2f} -> Margin: {margin_pct:.2f}%")
                            
                            store_margin('CME', t['symbol'], margin_pct, t['sector'], price=price)
                            found_any = True
                        except Exception as e:
                            print(f"  ⚠️ Error calculating % for {t['symbol']}: {e}")
                            # Fallback: Store 0% but keep margin info? 
                            # Or strict fail? Let's strict fail printing but maybe store 0?
                            pass

                    else:
                        print(f"  ⚠️ WARNING: Margin value missing in JSON for {t['symbol']}")
                else:
                    print(f"  ⚠️ WARNING: No margin data in JSON response for {t['symbol']}")
                    
            except Exception as e:
                print(f"  ⚠️ WARNING: Failed to fetch/parse {t['symbol']}: {e}")
                
        if not found_any:
            print("⚠️ WARNING: No CME margins were successfully fetched today.")
                
    except Exception as e:
        print(f"CRITICAL: CME scraping failed: {e}")
    finally:
        driver.quit()

def store_margin(exch_name, symbol, margin_pct, sector, price=0.0):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get/Find Exchange ID
        cursor.execute("SELECT id FROM exchanges WHERE name = ?", (exch_name,))
        res = cursor.fetchone()
        if not res:
             cursor.execute("INSERT INTO exchanges (name, country) VALUES (?, ?)", (exch_name, 'USA'))
             exch_id = cursor.lastrowid
        else:
            exch_id = res[0]
        
        # Determine asset class
        m_map = {
            'METALS': 'Commodity',
            'ENERGY': 'Commodity',
            'EQUITY INDEX': 'Equity Index'
        }
        asset_class = m_map.get(sector, 'Commodity')
        
        # Get/Find Instrument ID
        cursor.execute("SELECT id FROM instruments WHERE exchange_id = ? AND symbol = ?", (exch_id, symbol))
        res = cursor.fetchone()
        if not res:
            cursor.execute("INSERT INTO instruments (exchange_id, symbol, asset_class) VALUES (?, ?, ?)", 
                           (exch_id, symbol, asset_class))
            instr_id = cursor.lastrowid
        else:
            instr_id = res[0]
        
        # Store with today's date (CME API only provides latest)
        ts = date.today().strftime("%Y-%m-%d 16:00:00")
        cursor.execute("""
            INSERT OR REPLACE INTO margin_logs (timestamp, instrument_id, margin_percent, contract_price, open_interest)
            VALUES (?, ?, ?, ?, ?)
        """, (ts, instr_id, margin_pct, price, 0))
        
        conn.commit()
        conn.close()
        print(f"Stored {symbol} margin for {exch_name}.")
        
    except Exception as e:
        print(f"DB Error: {e}")

if __name__ == "__main__":
    get_cme_margins()
