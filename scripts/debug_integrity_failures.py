import yfinance as yf
from datetime import datetime, timedelta

symbols = ["ABB.NS", "HAL.NS", "MAZDOCK.NS", "SIEMENS.NS", "IOC.NS", "MUTHOOTFIN.NS", "CGPOWER.NS"]
target_dates = ["2025-02-03", "2020-03-23", "2019-02-13", "2019-02-14", "2020-02-13"]

for sym in symbols:
    print(f"\n--- {sym} ---")
    ticker = yf.Ticker(sym)
    
    # Check dividends
    divs = ticker.dividends
    for d in target_dates:
        dt = datetime.strptime(d, "%Y-%m-%d")
        # Check window
        mask_near = (divs.index >= (dt - timedelta(days=5)).strftime("%Y-%m-%d")) & \
                    (divs.index <= (dt + timedelta(days=5)).strftime("%Y-%m-%d"))
        if mask_near.any():
                print(f"Dividend near {d}:\n{divs[mask_near]}")

    # Check for any corporate actions around those dates
    actions = ticker.actions
    for d in target_dates:
        dt = datetime.strptime(d, "%Y-%m-%d")
        mask = (actions.index >= (dt - timedelta(days=5)).strftime("%Y-%m-%d")) & \
               (actions.index <= (dt + timedelta(days=5)).strftime("%Y-%m-%d"))
        if mask.any():
            print(f"Actions near {d}:\n{actions[mask]}")
