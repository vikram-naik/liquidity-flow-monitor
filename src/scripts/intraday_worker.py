import time
import sys
import os
from datetime import datetime
import pytz

# Robust imports
try:
    from src.database import get_db_connection
    from src.agents import silver_agent, gold_agent
    from src.utils.market_hours import is_nse_market_open
except ImportError:
    # Fallback for standalone script execution if needed
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
    from src.database import get_db_connection
    from src.agents import silver_agent, gold_agent
    from src.utils.market_hours import is_nse_market_open

def save_silver_tick(ts, price, inav, spot, usdinr):
    conn = get_db_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO silver_intraday_log 
            (timestamp, price_nse, inav_nippon, spot_usd, usdinr)
            VALUES (?, ?, ?, ?, ?)
        """, (ts.strftime('%Y-%m-%d %H:%M:%S'), price, inav, spot, usdinr))
        conn.commit()
    except Exception as e:
        print(f"[worker] Silver DB write error: {e}")
    finally:
        conn.close()

def save_gold_tick(ts, price, inav, spot, usdinr):
    conn = get_db_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO gold_intraday_log 
            (timestamp, price_nse, inav_nippon, spot_usd, usdinr)
            VALUES (?, ?, ?, ?, ?)
        """, (ts.strftime('%Y-%m-%d %H:%M:%S'), price, inav, spot, usdinr))
        conn.commit()
    except Exception as e:
        print(f"[worker] Gold DB write error: {e}")
    finally:
        conn.close()

def run_worker():
    print("🚀 Starting Intraday Data Worker...")
    ist = pytz.timezone('Asia/Kolkata')
    
    while True:
        is_open, status = is_nse_market_open()
        now_ist = datetime.now(ist)
        
        if is_open:
            print(f"[{now_ist.strftime('%H:%M:%S')}] Market Open. Fetching ticks...")
            
            # --- Silver ---
            try:
                s_price_data = silver_agent.fetch_nse_price("SILVERBEES")
                s_inav_data = silver_agent.fetch_nippon_inav("Nippon India Silver ETF")
                s_parity_data = silver_agent.fetch_global_parity_metrics()
                
                if s_price_data and s_inav_data:
                    spread = s_price_data['price'] - s_inav_data['inav']
                    spread_pct = (spread / s_inav_data['inav']) * 100
                    print(f"  🥈 [Silver] Price: {s_price_data['price']:.2f}, iNAV: {s_inav_data['inav']:.4f}, Spread: {spread:.4f} ({spread_pct:+.2f}%)")
                    save_silver_tick(
                        now_ist, 
                        s_price_data['price'], 
                        s_inav_data['inav'],
                        s_parity_data['spot_usd'] if s_parity_data else None,
                        s_parity_data['usdinr'] if s_parity_data else None
                    )
            except Exception as e:
                print(f"[worker] Silver fetch loop error: {e}")

            # --- Gold ---
            try:
                g_price_data = gold_agent.fetch_nse_price("GOLDBEES")
                g_inav_data = gold_agent.fetch_nippon_inav("Nippon India ETF Gold BeES")
                g_parity_data = gold_agent.fetch_global_parity_metrics()
                
                if g_price_data and g_inav_data:
                    spread = g_price_data['price'] - g_inav_data['inav']
                    spread_pct = (spread / g_inav_data['inav']) * 100
                    print(f"  🥇 [Gold]   Price: {g_price_data['price']:.2f}, iNAV: {g_inav_data['inav']:.4f}, Spread: {spread:.4f} ({spread_pct:+.2f}%)")
                    save_gold_tick(
                        now_ist, 
                        g_price_data['price'], 
                        g_inav_data['inav'],
                        g_parity_data['spot_usd'] if g_parity_data else None,
                        g_parity_data['usdinr'] if g_parity_data else None
                    )
            except Exception as e:
                print(f"[worker] Gold fetch loop error: {e}")
                
        else:
            # Optionally log every hour or just sleep
            if now_ist.minute == 0:
                print(f"[{now_ist.strftime('%H:%M:%S')}] Market Closed: {status}")
        
        # Interval: 1 minute
        time.sleep(60)

if __name__ == "__main__":
    run_worker()
