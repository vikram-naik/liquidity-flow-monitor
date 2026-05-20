---
name: ui-automation-testing
description: Workflow for end-to-end (E2E) UI testing of the LFM dashboard using Selenium and Python. Use when asked to verify UI components, test custom Ag-Grid filters, or confirm frontend rendering after backend changes.
---

# UI Automation Testing

Standardized procedure for performing E2E verification of the Liquidity Flow Monitor (LFM) web interface.

## Workflow

### 1. Environment Setup
The LFM UI requires a running backend server and a Selenium-compatible browser environment (Chrome/Chromium).

1.  **Start the Backend**: Launch the FastAPI server in the background.
    ```bash
    ./venv/bin/uvicorn src.api.main:app --host 127.0.0.1 --port 8000 &
    ```
2.  **Verify Redis**: Ensure Redis is running, as engine calculations depend on it.

### 2. Create the Test Script
Create a Python script (e.g., `tests/test_ui_feature.py`) using `selenium`.

**Mandatory Config Pattern:**
```python
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
    # Capture console logs for debugging
    chrome_options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    
    driver = webdriver.Chrome(options=chrome_options)
    try:
        driver.get("http://127.0.0.1:8000/de/screener") # Adjust route
        # Wait for Ag-Grid to render
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".ag-row"))
        )
        # Test logic here...
    finally:
        driver.quit()
```

### 3. Interacting with Ag-Grid
Since LFM uses Ag-Grid Community, interaction often requires injecting JavaScript to access the exposed `window.gridApi`.

**Pattern: Triggering a Filter**
```python
script = """
if (window.gridApi) {
    window.gridApi.setFilterModel({
        'signal_type': { value: 'entry' } # Standard value
        # OR multi-select pattern:
        # 'tech_signals': { values: ['C-UP', 'MAX-CTS'] }
    });
    window.gridApi.onFilterChanged();
    return true;
}
return false;
"""
success = driver.execute_script(script)
```

### 4. Verification & Logs
- **Visibility**: Count visible rows using `.ag-row[row-index]`.
- **Logs**: Capture and inspect browser console logs to detect `TypeError` or Ag-Grid warnings.
    ```python
    for entry in driver.get_log('browser'):
        print(entry['message'])
    ```

### 5. Cleanup
Always kill the background server after testing.
```bash
pkill -f "uvicorn src.api.main:app"
```

## Pitfalls & Troubleshooting
- **Blank Grid**: Selecting a filter value that matches 0 rows is common behavior in LFM, not necessarily a crash. Verify counts before concluding failure.
- **Missing Grid API**: Ensure `window.gridApi` is explicitly exposed in the target JS file (e.g., `src/web/js/screener.js`).
- **Timing**: Use `WebDriverWait` for `.ag-row` presence, not just `time.sleep()`.
- **Port Conflict**: Default port is 8000. Check if another process is using it if server fails to start.
