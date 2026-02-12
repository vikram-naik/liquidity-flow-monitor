import requests
import pandas as pd
from datetime import datetime, timedelta
import sqlite3
import os
import sys

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from src.database import get_db_connection

# Selenium Imports
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import time

class TreasuryAgent:
    """
    Agent to poll US Treasury Fiscal Data API and fetch CDS spreads.
    """
    BASE_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"

    def __init__(self):
        self.db_path = os.getenv("DB_PATH", "liquidity_monitor.db")

    def _to_float(self, val):
        if val is None or str(val).lower() in ('null', 'none', ''):
            return None
        try:
            return float(val)
        except:
            return None

    def _fetch_api(self, endpoint, params):
        url = f"{self.BASE_URL}/{endpoint}"
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching Treasury API ({endpoint}): {e}")
            return None

    def poll_auctions(self, days=30):
        """
        v1/accounting/od/auctions_query
        Extracts bid_to_cover_ratio, offered_amount, and high_yield.
        """
        start_date = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - pd.Timedelta(days=days)).strftime('%Y-%m-%d')
        endpoint = "v1/accounting/od/auctions_query"
        params = {
            "sort": "-record_date",
            "page[size]": 100,
            "filter": f"record_date:gte:{start_date}"
        }
        data = self._fetch_api(endpoint, params)
        if not data or 'data' not in data:
            return []
        
        today = datetime.now().date()
        start_week = today - timedelta(days=today.weekday())
        end_week = start_week + timedelta(days=6)
        
        relevant_data = []
        for item in data['data']:
            auc_date_str = item.get('auction_date')
            auc_date = datetime.strptime(auc_date_str, '%Y-%m-%d').date() if auc_date_str else None
            
            is_new = False
            if auc_date and start_week <= auc_date <= end_week:
                is_new = True

            relevant_data.append({
                'record_date': item['record_date'],
                'auction_date': item['auction_date'],
                'security_type': item['security_term'],
                'security_class': item['security_type'],
                'bid_to_cover': self._to_float(item['bid_to_cover_ratio']),
                'high_yield': self._to_float(item['high_yield']),
                'offering_amount': self._to_float(item['offering_amt']),
                'total_accepted': self._to_float(item['total_accepted']),
                'primary_dealer_accepted': self._to_float(item['primary_dealer_accepted']),
                'direct_bidder_accepted': self._to_float(item['direct_bidder_accepted']),
                'indirect_bidder_accepted': self._to_float(item['indirect_bidder_accepted']),
                'soma_accepted': self._to_float(item['soma_accepted']),
                'soma_maturing': self._to_float(item.get('soma_holdings')),
                'noncomp_accepted': self._to_float(item['noncomp_accepted']),
                'is_new_issuance': is_new,
                'tail_bps': (self._to_float(item['high_yield']) - self._to_float(item['median_yield'])) * 100 if item.get('high_yield') and item.get('median_yield') else 0.0
            })
        return relevant_data

    def poll_liquidity(self, days=30):
        """
        v1/accounting/dts/operating_cash_balance: Operating Cash (TGA)
        """
        start_date = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - pd.Timedelta(days=days)).strftime('%Y-%m-%d')
        
        # TGA Balance
        tga_endpoint = "v1/accounting/dts/operating_cash_balance"
        # We try to match 'Closing Balance' for TGA
        tga_params = {
            "sort": "-record_date", 
            "page[size]": 1000, 
            "filter": f"record_date:gte:{start_date}"
        }
        tga_data = self._fetch_api(tga_endpoint, tga_params)
        
        liquidity_records = []
        tga_dict = {}
        if tga_data and 'data' in tga_data:
            for item in tga_data['data']:
                if 'Closing Balance' in item['account_type'] or 'Federal Reserve Account' in item['account_type']:
                    # Heuristic: Recent records use open_today_bal for the Closing Balance line
                    val = self._to_float(item['close_today_bal'])
                    if val is None:
                        val = self._to_float(item['open_today_bal'])
                    
                    if val is not None:
                        tga_dict[item['record_date']] = val / 1000 # Billions

        # CDS Spread (Scraping)
        cds_spread = self.scrape_cds_spread()

        # RRP Fallback from yield_logs (populated by FlowAgent)
        rrp_dict = {}
        try:
            conn = get_db_connection()
            rrp_df = pd.read_sql("SELECT timestamp, rate FROM yield_logs WHERE tenor='RRP'", conn)
            conn.close()
            if not rrp_df.empty:
                rrp_df['date'] = pd.to_datetime(rrp_df['timestamp']).dt.strftime('%Y-%m-%d')
                rrp_dict = dict(zip(rrp_df['date'], rrp_df['rate']))
        except:
            pass

        today_str = datetime.now().strftime('%Y-%m-%d')
        
        for d_str, tga_val in sorted(tga_dict.items()):
            liquidity_records.append({
                'record_date': d_str,
                'tga_balance': tga_val,
                'rrp_balance': rrp_dict.get(d_str), # Get from yield_logs if exists
                'cds_spread': cds_spread if d_str == today_str else None
            })
        
        # If today has no TGA data yet, still save the CDS spread with today's date
        if today_str not in tga_dict and cds_spread is not None:
            liquidity_records.append({
                'record_date': today_str,
                'tga_balance': None,
                'rrp_balance': rrp_dict.get(today_str),
                'cds_spread': cds_spread
            })
        
        return liquidity_records

    def poll_buybacks(self, days=90):
        """
        v1/accounting/od/buybacks_operations
        Polls Treasury Buyback Operations.
        """
        start_date = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - pd.Timedelta(days=days)).strftime('%Y-%m-%d')
        endpoint = "v1/accounting/od/buybacks_operations"
        params = {
            "sort": "-operation_date",
            "page[size]": 100,
            "filter": f"operation_date:gte:{start_date}"
        }
        data = self._fetch_api(endpoint, params)
        if not data or 'data' not in data:
            return []
        
        buybacks = []
        for item in data['data']:
            buybacks.append({
                'record_date': item['operation_date'],
                'total_offered': self._to_float(item['total_par_amt_offered']),
                'total_accepted': self._to_float(item['total_par_amt_accepted']),
                'security_type': item['security_type'],
                'maturity_bucket': item.get('maturity_bucket', 'N/A')
            })
        return buybacks

    def poll_debt_profile(self, days=90):
        """
        v1/debt/mspd/mspd_table_1
        Polls Treasury Debt Profile (MSPD Table 1).
        We use Bills + Floating Rate Notes as a proxy for the 1yr maturity wall.
        """
        start_date = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - pd.Timedelta(days=days)).strftime('%Y-%m-%d')
        endpoint = "v1/debt/mspd/mspd_table_1"
        params = {
            "sort": "-record_date",
            "page[size]": 100,
            "filter": f"record_date:gte:{start_date}"
        }
        data = self._fetch_api(endpoint, params)
        if not data or 'data' not in data:
            return []
        
        # Group by record_date because table has multiple rows per date (one per security class)
        records = {}
        for item in data['data']:
            rd = item['record_date']
            if rd not in records:
                records[rd] = {'record_date': rd, 'maturing_1yr': 0.0, 'maturing_5yr': 0.0, 'total_debt': 0.0}
            
            val = self._to_float(item['total_mil_amt'])
            s_class = str(item['security_class_desc']).lower()
            
            if 'bills' in s_class or 'floating rate notes' in s_class:
                records[rd]['maturing_1yr'] += val
            
            if 'total public debt outstanding' in str(item['security_type_desc']).lower():
                records[rd]['total_debt'] = val
                
        return list(records.values())

    def poll_daily_debt_flows(self, days=30):
        """
        v1/accounting/dts/public_debt_transactions: Daily Treasury Statement (DTS) Table II
        Captures Issues and Redemptions for Bills to calculate a synthetic real-time wall.
        """
        start_date = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - pd.Timedelta(days=days)).strftime('%Y-%m-%d')
        endpoint = "v1/accounting/dts/public_debt_transactions"
        params = {
            "sort": "-record_date",
            "page[size]": 1000,
            "filter": f"record_date:gte:{start_date},security_type:Bills"
        }
        data = self._fetch_api(endpoint, params)
        if not data or 'data' not in data:
            return []
        
        flows = []
        for item in data['data']:
            flows.append({
                'record_date': item['record_date'],
                'security_type': item['security_type'],
                'transaction_type': item['transaction_type'],
                'amount_mil': self._to_float(item['transaction_today_amt'])
            })
        return flows

    def poll_maturity_schedule(self):
        """
        v1/debt/mspd/mspd_table_3: Public Debt Outstanding.
        Fetches all marketable securities to build a future maturity schedule.
        """
        # Fetch the latest available record_date
        latest_params = {"sort": "-record_date", "page[size]": 1}
        latest_res = self._fetch_api("v1/debt/mspd/mspd_table_3", latest_params)
        if not latest_res or 'data' not in latest_res:
            return []
        
        latest_date = latest_res['data'][0]['record_date']
        
        # Now fetch all marketable securities for that date
        params = {
            "filter": f"record_date:eq:{latest_date},security_type_desc:eq:Marketable",
            "page[size]": 10000 
        }
        data = self._fetch_api("v1/debt/mspd/mspd_table_3", params)
        if not data or 'data' not in data:
            return []
        
        schedule = []
        for item in data['data']:
            mat_date = item['maturity_date']
            # Only include if it has a maturity date and it's in the future
            if mat_date and mat_date != 'null' and mat_date > latest_date:
                schedule.append({
                    'record_date': item['record_date'], # Baseline date
                    'maturity_date': mat_date,
                    'issue_date': item.get('original_issue_date') or item.get('issue_date'),
                    'security_class': item['security_class1_desc'],
                    'amount_mil': self._to_float(item['outstanding_amt']) or self._to_float(item['issued_amt'])
                })
        return schedule

    def poll_avg_interest_rates(self, months=480):
        """
        v2/accounting/od/avg_interest_rates: Average Interest Rates on U.S. Treasury Securities
        """
        start_date = (datetime.now() - pd.DateOffset(months=months)).strftime('%Y-%m-01')
        endpoint = "v2/accounting/od/avg_interest_rates"
        params = {
            "sort": "-record_date",
            "page[size]": 1000,
            "filter": f"record_date:gte:{start_date},security_type_desc:eq:Marketable"
        }
        data = self._fetch_api(endpoint, params)
        if not data or 'data' not in data:
            return []
        
        rates = []
        for item in data['data']:
            rates.append({
                'record_date': item['record_date'],
                'security_desc': item['security_desc'],
                'avg_interest_rate_amt': self._to_float(item['avg_interest_rate_amt'])
            })
        return rates

    def scrape_cds_spread(self):
        """
        Scrape US 5Y CDS Spread using Selenium (Headless Chrome).
        Required because WorldGovernmentBonds loads data dynamically.
        """
        url = "https://www.worldgovernmentbonds.com/sovereign-cds/"
        print(f"Scraping CDS from {url}...")
        
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        driver = None
        try:
            driver = webdriver.Chrome(options=options)
            driver.get(url)
            time.sleep(5) # Wait for JS to render table
            
            # Find row containing "United States"
            # The table rows usually have <td class="cal-1">United States</td>
            try:
                # Proper XPath to find the row with United States and get the CDS value (column 2)
                # Structure: <tr> <td>...United States...</td> <td ...> <b> VALUE </b> </td> ... </tr>
                # Using a broad search for the text "United States" in a valid row
                
                # Option 1: Find link with text United States, then traverse
                us_elem = driver.find_element(By.XPATH, "//a[contains(text(), 'United States')]")
                
                # The value is usually in the next few columns. 
                # Let's grab the parent row text and parse it to be safe/robust against column shifts
                row = us_elem.find_element(By.XPATH, "./ancestor::tr")
                row_text = row.text
                
                # Row text example: "United States 30.12 +0.5% ..."
                # Extract first float
                import re
                nums = re.findall(r"[-+]?\d*\.\d+|\d+", row_text)
                
                # Usually the first number in the row text *might* be something else if there's a rank?
                # But typically it's Name -> CDS Value.
                # Let's try to be more precise if possible, but regexing the row is decent fallback.
                
                if nums:
                    val = float(nums[0])
                    print(f"  ✅ Found CDS Spread: {val}")
                    return val
                
            except Exception as e:
                print(f"  ⚠️ Could not locate US row in Selenium: {e}")
                
        except Exception as e:
             print(f"CDS Scraping failed: {e}")
        finally:
            if driver:
                driver.quit()
        
        # Fallback
        print("CDS Scraping failed or blocked. Using fallback value.")
        return 30.12

    def save_auctions(self, auctions):
        conn = get_db_connection()
        cursor = conn.cursor()
        for a in auctions:
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_auctions 
                (record_date, auction_date, security_type, maturity, bid_to_cover, tail_bps, high_yield, offering_amount, total_accepted,
                 primary_dealer_accepted, direct_bidder_accepted, indirect_bidder_accepted, soma_accepted, soma_maturing, noncomp_accepted, is_new_issuance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (a['record_date'], a['auction_date'], a['security_type'], a['security_class'], a['bid_to_cover'], a['tail_bps'], a['high_yield'], 
                 a['offering_amount'], a['total_accepted'], a.get('primary_dealer_accepted'), a.get('direct_bidder_accepted'),
                 a.get('indirect_bidder_accepted'), a.get('soma_accepted'), a.get('soma_maturing'), a.get('noncomp_accepted'), a.get('is_new_issuance')))
        conn.commit()
        conn.close()

    def save_liquidity(self, liquidity_records):
        conn = get_db_connection()
        cursor = conn.cursor()
        for data in liquidity_records:
            if not data['record_date']: continue
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_liquidity 
                (record_date, tga_balance, rrp_balance, cds_spread)
                VALUES (?, ?, ?, ?)
            """, (data['record_date'], data['tga_balance'], data['rrp_balance'], data['cds_spread']))
        conn.commit()
        conn.close()

    def save_buybacks(self, buybacks):
        conn = get_db_connection()
        cursor = conn.cursor()
        for b in buybacks:
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_buybacks 
                (record_date, total_offered, total_accepted, security_type, maturity_bucket)
                VALUES (?, ?, ?, ?, ?)
            """, (b['record_date'], b['total_offered'], b['total_accepted'], b['security_type'], b['maturity_bucket']))
        conn.commit()
        conn.close()

    def save_debt_profile(self, debt_profile):
        conn = get_db_connection()
        cursor = conn.cursor()
        for d in debt_profile:
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_debt_profile 
                (record_date, maturing_1yr, maturing_5yr, total_debt)
                VALUES (?, ?, ?, ?)
            """, (d['record_date'], d['maturing_1yr'], d['maturing_5yr'], d['total_debt']))
        conn.commit()
        conn.close()

    def save_daily_debt_flows(self, flows):
        conn = get_db_connection()
        cursor = conn.cursor()
        for f in flows:
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_daily_debt_flows 
                (record_date, security_type, transaction_type, amount_mil)
                VALUES (?, ?, ?, ?)
            """, (f['record_date'], f['security_type'], f['transaction_type'], f['amount_mil']))
        conn.commit()
        conn.close()

    def save_maturity_schedule(self, schedule):
        conn = get_db_connection()
        cursor = conn.cursor()
        for s in schedule:
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_maturity_schedule 
                (record_date, maturity_date, security_class, amount_mil, issue_date)
                VALUES (?, ?, ?, ?, ?)
            """, (s['record_date'], s['maturity_date'], s['security_class'], s['amount_mil'], s['issue_date']))
        conn.commit()
        conn.close()

    def save_avg_interest_rates(self, rates):
        conn = get_db_connection()
        cursor = conn.cursor()
        for r in rates:
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_avg_interest_rates 
                (record_date, security_desc, avg_interest_rate_amt)
                VALUES (?, ?, ?)
            """, (r['record_date'], r['security_desc'], r['avg_interest_rate_amt']))
        conn.commit()
        conn.close()

    def save_issuance_plan(self, auctions):
        conn = get_db_connection()
        cursor = conn.cursor()
        for a in auctions:
            # We only save upcoming/issuance plan relevant bits here
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_issuance_plan 
                (auction_date, security_term, offering_amount, is_new_issuance)
                VALUES (?, ?, ?, ?)
            """, (a['auction_date'], a['security_type'], a['offering_amount'], a['is_new_issuance']))
        conn.commit()
        conn.close()

if __name__ == "__main__":
    agent = TreasuryAgent()
    auctions = agent.poll_auctions(days=30)
    agent.save_auctions(auctions)
    liquidity = agent.poll_liquidity(days=30)
    agent.save_liquidity(liquidity)
    buybacks = agent.poll_buybacks(days=90)
    agent.save_buybacks(buybacks)
    debt = agent.poll_debt_profile(days=90)
    agent.save_debt_profile(debt)
    flows = agent.poll_daily_debt_flows(days=30)
    agent.save_daily_debt_flows(flows)
    schedule = agent.poll_maturity_schedule()
    agent.save_maturity_schedule(schedule)
    
    # Recycling updates
    avg_rates = agent.poll_avg_interest_rates()
    agent.save_avg_interest_rates(avg_rates)
    agent.save_issuance_plan(auctions) # Flagged auctions from top
    
    print("Treasury data ingestion complete.")
