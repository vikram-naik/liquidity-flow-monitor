import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def run_test():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    # Enable console log capture
    chrome_options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    
    driver = webdriver.Chrome(options=chrome_options)
    
    try:
        print("Navigating to screener...")
        driver.get("http://127.0.0.1:8000/de/screener")
        
        # Wait for grid to render
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".ag-row"))
        )
        print("Grid loaded. Rows found.")
        
        # Get row count before filtering
        rows_before = len(driver.find_elements(By.CSS_SELECTOR, ".ag-row[row-index]"))
        print(f"Rows before filter (visible): {rows_before}")
        
        # Inject JS to trigger filter
        script = """
        if (window.gridApi) {
            window.gridApi.setFilterModel({
                'tech_signals': { values: ['C-UP', 'MIN-CTS'] }
            });
            window.gridApi.onFilterChanged();
            return true;
        }
        return false;
        """
        success = driver.execute_script(script)
        print(f"Filter script execution success: {success}")
        
        time.sleep(3)  # Wait for grid to update
        
        rows_after = len(driver.find_elements(By.CSS_SELECTOR, ".ag-row[row-index]"))
        print(f"Rows after filter (visible): {rows_after}")
        
        # Check browser logs
        print("--- Browser Logs ---")
        for entry in driver.get_log('browser'):
            print(entry['message'])
            
    finally:
        driver.quit()

if __name__ == "__main__":
    run_test()
