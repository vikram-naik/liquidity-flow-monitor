
import os
import sys
import json
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

def debug_cme():
    targets = [
        {'symbol': 'CRUDE_OIL', 'code': 'CL', 'exchange': 'NYM', 'sector': 'ENERGY'},
        {'symbol': 'ES', 'code': 'ES', 'exchange': 'CME', 'sector': 'EQUITY INDEX'},
        {'symbol': 'NQ', 'code': 'NQ', 'exchange': 'CME', 'sector': 'EQUITY INDEX'}
    ]

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(options=options)
    
    try:
        for t in targets:
            url = f"https://www.cmegroup.com/services/margins/OUTRIGHT?1=1&search={t['code']}&pageSize=10"
            print(f"\n--- Debugging {t['symbol']} ---")
            print(f"URL: {url}")
            
            driver.get(url)
            time.sleep(3)
            
            content = driver.find_element(By.TAG_NAME, "body").text
            print(f"Response Content Snippet: {content[:500]}...")
            
            try:
                data = json.loads(content)
                if 'marginRates' in data:
                    print(f"Found {len(data['marginRates'])} marginRates.")
                    if data['marginRates']:
                        print(f"First Rate Object: {json.dumps(data['marginRates'][0], indent=2)}")
                else:
                    print("Key 'marginRates' NOT found in JSON!")
            except Exception as e:
                print(f"JSON Parse Error: {e}")
                
    finally:
        driver.quit()

if __name__ == "__main__":
    debug_cme()
