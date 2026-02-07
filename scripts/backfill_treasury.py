#!/usr/bin/env python3
"""
US Treasury Data Backfill Script
Backfills auction, liquidity (TGA), and risk data.

Usage:
    python3 scripts/backfill_treasury.py --days=180
"""

import argparse
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from src.agents.treasury_agent import TreasuryAgent
from src.database import get_db_connection

def backfill_treasury(days: int):
    print(f"=== US Treasury Backfill (last {days} days) ===")
    agent = TreasuryAgent()
    
    # 1. Backfill Auctions
    print("Fetching historical auctions...")
    auctions = agent.poll_auctions(days=days)
    if auctions:
        agent.save_auctions(auctions)
        print(f"  ✓ {len(auctions)} auction records saved.")
    else:
        print("  ⚠️ No auction data found.")
        
    # 2. Backfill Liquidity (TGA)
    print("Fetching historical liquidity (TGA)...")
    liquidity = agent.poll_liquidity(days=days)
    if liquidity:
        # Try to pull RRP from existing yield_logs to supplement the backfill
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get historical RRP from yield_logs
        rrp_data = {}
        try:
            cursor.execute("SELECT DATE(timestamp), rate FROM yield_logs WHERE tenor = 'RRP'")
            for row in cursor.fetchall():
                rrp_data[row[0]] = row[1]
        except:
            print("  ⚠️ Could not fetch RRP data from yield_logs.")
            
        # Enrich treasury_liquidity with RRP data
        for record in liquidity:
            if record['record_date'] in rrp_data:
                record['rrp_balance'] = rrp_data[record['record_date']]
        
        agent.save_liquidity(liquidity)
        print(f"  ✓ {len(liquidity)} liquidity records saved.")
        conn.close()
    else:
        print("  ⚠️ No liquidity data found.")

    # 3. Backfill Buybacks
    print("Fetching historical buybacks...")
    buybacks = agent.poll_buybacks(days=days)
    if buybacks:
        agent.save_buybacks(buybacks)
        print(f"  ✓ {len(buybacks)} buyback records saved.")
    else:
        print("  ⚠️ No buyback data found.")

    # 4. Backfill Debt Profile
    print("Fetching historical debt profile...")
    debt = agent.poll_debt_profile(days=days)
    if debt:
        agent.save_debt_profile(debt)
        print(f"  ✓ {len(debt)} debt profile records saved.")
    else:
        print("  ⚠️ No debt profile data found.")

    # 5. Backfill Daily Debt Flows (DTS Table II)
    print("Fetching historical daily debt flows...")
    flows = agent.poll_daily_debt_flows(days=days)
    if flows:
        agent.save_daily_debt_flows(flows)
        print(f"  ✓ {len(flows)} daily flow records saved.")
    else:
        print("  ⚠️ No daily flow data found.")

    print("\n✓ Treasury backfill complete.")

def main():
    parser = argparse.ArgumentParser(description='US Treasury Data Backfill Script')
    parser.add_argument('--days', type=int, default=180,
                       help='Number of days to backfill (default: 180)')
    
    args = parser.parse_args()
    backfill_treasury(args.days)

if __name__ == "__main__":
    main()
