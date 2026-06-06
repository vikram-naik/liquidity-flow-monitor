#!/usr/bin/env python3
"""
Data Integrity Validation Script.

Identifies large price whip-saws (unexplained drops/rises) that might indicate
missed corporate actions. Automatically triggers CA sync and reports persistent issues.

Usage:
    python scripts/validate_data_integrity.py --watchlist "NIFTY 50"
    python scripts/validate_data_integrity.py --symbol RELIANCE
"""

import sys
import os
import argparse
import logging
import subprocess
from datetime import datetime
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf
import json

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.divergence_engine.engine import DivergenceEngine
from src.database import get_db_connection
from src.cache.factory import get_cache

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Default threshold for a "whip-saw" (e.g., 10% change in one day)
DEFAULT_THRESHOLD = 12.0
# Tolerance for reconciliation with external source (percentage)
RECONCILE_TOLERANCE = 1.5

def get_symbols(watchlist_name=None):
    """Fetch symbols from watchlist or all symbols from delivery log."""
    conn = get_db_connection()
    try:
        if watchlist_name:
            query = """
                SELECT symbol FROM watchlist_items 
                JOIN watchlists ON watchlists.id = watchlist_items.watchlist_id
                WHERE watchlists.name = ?
                ORDER BY display_order
            """
            symbols = [row[0] for row in conn.execute(query, (watchlist_name,)).fetchall()]
        else:
            query = "SELECT DISTINCT symbol FROM nse_delivery_log"
            symbols = [row[0] for row in conn.execute(query).fetchall()]
        return symbols
    finally:
        conn.close()

def detect_whip_saws(symbol, threshold):
    """Detect price jumps/drops exceeding threshold %."""
    try:
        engine = DivergenceEngine(symbol)
        result = engine.run()
        df = result.ledger

        if df.empty or len(df) < 2:
            return []

        # Calculate daily returns
        df['prev_close'] = df['close'].shift(1)
        df['daily_ret'] = (df['close'] / df['prev_close'] - 1) * 100.0
        
        # Filter for whip-saws
        whip_saws = df[abs(df['daily_ret']) >= threshold].copy()
        
        issues = []
        for _, row in whip_saws.iterrows():
            issues.append({
                'date': str(row['date'])[:10],
                'price': round(row['close'], 2),
                'prev_price': round(row['prev_close'], 2),
                'change_pct': round(row['daily_ret'], 2),
                'local_ohlc': f"O:{round(row['open'], 2)} H:{round(row['high'], 2)} L:{round(row['low'], 2)} C:{round(row['close'], 2)}"
            })
        return issues
    except Exception as e:
        logger.error(f"Error processing {symbol}: {e}")
        return []

def reconcile_with_internet(symbol, date, local_close, local_ret):
    """
    Reconciles local adjusted data with yfinance adjusted data.
    Compares the daily return to verify the move. 
    Returns (match_status, external_ohlc_dict, ext_ret, dividend_impact)
    """
    yf_symbol = f"{symbol.upper()}.NS"
    logger.info(f"Reconciling {symbol} on {date} with yfinance...")
    try:
        dt = pd.to_datetime(date)
        # Fetch a small window around the date
        start_str = (dt - pd.Timedelta(days=15)).strftime('%Y-%m-%d')
        end_str = (dt + pd.Timedelta(days=5)).strftime('%Y-%m-%d')
        
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(start=start_str, end=end_str, auto_adjust=True)
        
        # Remove yfinance rows with 0 volume to avoid stale price/return calculations
        if 'Volume' in df.columns:
            df = df[df['Volume'] > 0].copy()
        if df.empty:
            return "No External Data", None, None, 0.0
            
        # Get dividends specifically to explain drift
        divs = ticker.dividends
        date_iso = dt.strftime('%Y-%m-%d')
        dividend = 0.0
        if not divs.empty:
            day_divs = divs[divs.index.strftime('%Y-%m-%d') == date_iso]
            if not day_divs.empty:
                dividend = float(day_divs.iloc[0])
                logger.info(f"Found dividend of {dividend} for {symbol} on {date}")

        # Calculate daily returns in yfinance
        df['prev_close'] = df['Close'].shift(1)
        df['daily_ret'] = (df['Close'] / df['prev_close'] - 1) * 100.0
        
        yf_dates = list(df.index.strftime('%Y-%m-%d'))
        
        if date_iso in yf_dates:
            ext_row = df.loc[df.index.strftime('%Y-%m-%d') == date_iso].iloc[0]
            
            def _get_val(col):
                val = ext_row[col]
                return float(val.iloc[0]) if isinstance(val, pd.Series) else float(val)

            ext_close = _get_val('Close')
            ext_ret = _get_val('daily_ret')
            
            ext_ohlc = {
                'o': round(_get_val('Open'), 2),
                'h': round(_get_val('High'), 2),
                'l': round(_get_val('Low'), 2),
                'c': round(ext_close, 2)
            }
            
            if pd.isna(ext_ret):
                return "External Return N/A", ext_ohlc, ext_ret, 0.0

            # Date alignment to handle holidays/special sessions mismatch
            try:
                idx = yf_dates.index(date_iso)
                if idx > 0:
                    prev_date_iso = yf_dates[idx - 1]
                    engine = DivergenceEngine(symbol)
                    ledger = engine.run().ledger
                    ledger['date_str'] = pd.to_datetime(ledger['date']).dt.strftime('%Y-%m-%d')
                    local_row_prev = ledger[ledger['date_str'] == prev_date_iso]
                    if not local_row_prev.empty:
                        local_close_prev = float(local_row_prev.iloc[0]['close'])
                        local_ret_aligned = (local_close / local_close_prev - 1) * 100.0
                        if abs(local_ret - local_ret_aligned) > 0.01:
                            logger.info(f"Aligned previous date for {symbol} on {date}: external_prev={prev_date_iso}, local_close_prev={local_close_prev:.2f}. Adjusted local return from {local_ret:.2f}% to {local_ret_aligned:.2f}%")
                            local_ret = local_ret_aligned
            except Exception as e:
                logger.warning(f"Failed to align previous dates for {symbol} on {date}: {e}")

            
            # Dividend Impact on return: (P - div)/P_prev vs P/P_prev
            # yfinance adjusted return already includes the dividend.
            # Our local return does NOT.
            # Approximate impact: dividend / prev_price
            div_impact_ret = 0.0
            if dividend > 0:
                # We need the local previous close to estimate the impact
                conn = get_db_connection()
                row = conn.execute("SELECT price_close FROM nse_delivery_log WHERE symbol=? AND record_date < ? ORDER BY record_date DESC LIMIT 1", (symbol, date)).fetchone()
                conn.close()
                if row and row[0] > 0:
                    div_impact_ret = (dividend / row[0]) * 100.0
                    logger.info(f"Estimated dividend impact on return: {div_impact_ret:.2f}%")

            delta_ret = abs(local_ret - ext_ret)
            drift_pct = abs(local_close - ext_close) / ext_close * 100.0
            
            # 1. Primary Check: Absolute Price Drift
            # If the price itself matches within a tight tolerance, it's a match.
            if drift_pct <= RECONCILE_TOLERANCE:
                return "Match (Price Verified)", ext_ohlc, ext_ret, 0.0

            # 2. Secondary Check: Dividend-Adjusted Return
            # If we account for the dividend, does the return match?
            if dividend > 0 and abs(delta_ret - div_impact_ret) <= RECONCILE_TOLERANCE:
                return "Match (with Dividend)", ext_ohlc, ext_ret, div_impact_ret
            
            # 3. Tertiary Check: Standard Return Match
            if delta_ret <= RECONCILE_TOLERANCE:
                return "Match", ext_ohlc, ext_ret, 0.0
            else:
                return f"Mismatch (Ret diff: {delta_ret:.2f}%, Drift: {drift_pct:.2f}%)", ext_ohlc, ext_ret, div_impact_ret
        else:
            # Attempt next-date recovery for missing dates in yfinance (data gaps)
            logger.info(f"Date {date_iso} not found in yfinance for {symbol}. Attempting next-date recovery...")
            next_dates = [d for d in yf_dates if d > date_iso]
            if not next_dates:
                return "Date Not Found Externally", None, None, 0.0
            
            next_date_iso = next_dates[0]
            logger.info(f"Next available date in yfinance is {next_date_iso}. Reconciling...")
            
            engine = DivergenceEngine(symbol)
            ledger = engine.run().ledger
            ledger['date_str'] = pd.to_datetime(ledger['date']).dt.strftime('%Y-%m-%d')
            local_row_next = ledger[ledger['date_str'] == next_date_iso]
            
            if local_row_next.empty:
                return "Date Not Found Externally", None, None, 0.0
                
            local_close_next = float(local_row_next.iloc[0]['close'])
            ext_row_next = df.loc[df.index.strftime('%Y-%m-%d') == next_date_iso].iloc[0]
            
            def _get_val_next(col):
                val = ext_row_next[col]
                return float(val.iloc[0]) if isinstance(val, pd.Series) else float(val)
                
            ext_close_next = _get_val_next('Close')
            drift_pct = abs(local_close_next - ext_close_next) / ext_close_next * 100.0
            
            ext_ohlc = {
                'o': round(_get_val_next('Open'), 2),
                'h': round(_get_val_next('High'), 2),
                'l': round(_get_val_next('Low'), 2),
                'c': round(ext_close_next, 2)
            }
            
            if drift_pct <= RECONCILE_TOLERANCE:
                return "Match (Next Date Verified)", ext_ohlc, 0.0, 0.0
            else:
                return f"Mismatch on Next Date {next_date_iso} (Drift: {drift_pct:.2f}%)", ext_ohlc, 0.0, 0.0
            
    except Exception as e:
        logger.error(f"Reconciliation error for {symbol} on {date}: {e}")
        return f"Recon Error", None, None, 0.0

def patch_override(symbol, date, local_ret, ext_ret):
    """
    Calculates the correction factor and updates ca_overrides table in DB.
    """
    # 1. Find if there's an existing CA on or before this date
    conn = get_db_connection()
    try:
        query = "SELECT ex_date, ratio_factor FROM corporate_actions WHERE symbol = ? AND ex_date <= ? ORDER BY ex_date DESC LIMIT 1"
        row = conn.execute(query, (symbol, date)).fetchone()
        
        current_factor = 1.0
        target_date = date
        
        if row:
            target_date = row[0]
            current_factor = row[1]
            logger.info(f"Found existing CA for {symbol} on {target_date} with factor {current_factor}")
        else:
            logger.info(f"No existing CA found for {symbol} on or before {date}. Creating new override.")

        # Calculate correction
        correction_delta = (1 + ext_ret / 100.0) / (1 + local_ret / 100.0)
        new_factor = round(current_factor * correction_delta, 4)
        
        # Only apply patch if drift is significant (>3%) to avoid corrupting ratios with dividend drift
        if abs(correction_delta - 1.0) < 0.03:
            logger.info(f"Correction delta too small ({correction_delta:.6f}), likely dividend drift. Skipping patch.")
            return False

        # Insert or Replace in DB
        conn.execute("""
            INSERT OR REPLACE INTO ca_overrides (symbol, ex_date, ratio_factor, notes)
            VALUES (?, ?, ?, ?)
        """, (symbol, target_date, new_factor, f"Auto-patched from internet recon on {datetime.now().strftime('%Y-%m-%d')}"))
        conn.commit()
            
        logger.info(f"Successfully patched {symbol} {target_date} in DB with factor {new_factor}")
        return True
    finally:
        conn.close()

def sync_corporate_actions(symbol):
    """Run sync_nse_ca.py for the symbol non-interactively."""
    logger.info(f"Syncing corporate actions for {symbol}...")
    try:
        cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / "sync_nse_ca.py"), symbol, "--yes"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"Sync failed for {symbol}: {result.stderr}")
            return False
        return True
    except Exception as e:
        logger.error(f"Error running sync script for {symbol}: {e}")
        return False

def flush_cache_for_symbol(symbol):
    """Flush Redis cache for the given symbol."""
    cache = get_cache()
    if cache:
        # de:adjusted:<symbol>:*
        # de:result:<symbol>:*
        pattern_adj = f"de:adjusted:{symbol}:*"
        pattern_res = f"de:result:{symbol}:*"
        cache.delete_pattern(pattern_adj)
        cache.delete_pattern(pattern_res)
        logger.info(f"Flushed cache for {symbol}")

def main():
    parser = argparse.ArgumentParser(description="Validate data integrity and corporate actions")
    parser.add_argument("--watchlist", help="Watchlist name to check")
    parser.add_argument("--symbol", help="Specific symbol to check")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, 
                        help=f"Percentage change threshold for whip-saw (default: {DEFAULT_THRESHOLD}%)")
    parser.add_argument("--auto-fix", action="store_true", help="Automatically trigger CA sync on whip-saw")
    parser.add_argument("--auto-patch", action="store_true", help="Automatically update ca_overrides.json on mismatch")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only log errors")
    
    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.ERROR)
        # Also silence stdout if any prints are used
        sys.stdout = open(os.devnull, 'w')

    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = get_symbols(args.watchlist)

    if not symbols:
        logger.error("No symbols found.")
        sys.exit(1)

    logger.info(f"Starting validation for {len(symbols)} symbols with threshold {args.threshold}%")

    failures = []
    verified_moves = []
    
    for symbol in symbols:
        logger.info(f"Checking {symbol}...")
        issues = detect_whip_saws(symbol, args.threshold)
        
        if issues:
            logger.warning(f"Found {len(issues)} whip-saws for {symbol}")
            
            # 1. Try Auto-Fix (CA Sync)
            if args.auto_fix:
                if sync_corporate_actions(symbol):
                    flush_cache_for_symbol(symbol)
                    # Refresh issues
                    issues = detect_whip_saws(symbol, args.threshold)
                else:
                    logger.error(f"Sync failed for {symbol}")

            if not issues:
                logger.info(f"All whip-saws resolved for {symbol} after CA sync")
                continue

            # 2. Process remaining issues (Reconcile)
            patches_applied = False
            for issue in issues:
                issue['symbol'] = symbol
                status, ext_ohlc, ext_ret, div_impact = reconcile_with_internet(symbol, issue['date'], issue['price'], issue['change_pct'])
                
                if status.startswith("Match"):
                    issue['status'] = f'Verified (Real Market Event{ " + Dividend" if "Dividend" in status else ""})'
                    issue['ext_ohlc'] = f"O:{ext_ohlc['o']} H:{ext_ohlc['h']} L:{ext_ohlc['l']} C:{ext_ohlc['c']}"
                    verified_moves.append(issue)
                else:
                    issue['status'] = f'Failure ({status})'
                    if ext_ohlc:
                        issue['ext_ohlc'] = f"O:{ext_ohlc['o']} H:{ext_ohlc['h']} L:{ext_ohlc['l']} C:{ext_ohlc['c']}"
                        # 3. Try Auto-Patch
                        if args.auto_patch and ext_ret is not None:
                            if patch_override(symbol, issue['date'], issue['change_pct'], ext_ret):
                                issue['status'] += ' -> Patched'
                                patches_applied = True
                    else:
                        issue['ext_ohlc'] = "N/A"
                    failures.append(issue)

            # 4. If patches were applied, re-sync and re-verify in one pass
            if patches_applied:
                logger.info(f"Patches applied for {symbol}. Re-syncing and re-verifying...")
                if sync_corporate_actions(symbol):
                    flush_cache_for_symbol(symbol)
                    
                    # Clear previous failures for this symbol as we are re-evaluating
                    failures = [f for f in failures if f.get('symbol') != symbol]
                    verified_moves = [v for v in verified_moves if v.get('symbol') != symbol]
                    
                    final_issues = detect_whip_saws(symbol, args.threshold)
                    for issue in final_issues:
                        issue['symbol'] = symbol
                        status, ext_ohlc, ext_ret, div_impact = reconcile_with_internet(symbol, issue['date'], issue['price'], issue['change_pct'])
                        if status.startswith("Match"):
                            issue['status'] = f'Verified (Real Market Event{ " + Dividend" if "Dividend" in status else ""})'
                            issue['ext_ohlc'] = f"O:{ext_ohlc['o']} H:{ext_ohlc['h']} L:{ext_ohlc['l']} C:{ext_ohlc['c']}"
                            verified_moves.append(issue)
                        else:
                            issue['status'] = f'Failure ({status}) -> Persistent after patch'
                            if ext_ohlc:
                                issue['ext_ohlc'] = f"O:{ext_ohlc['o']} H:{ext_ohlc['h']} L:{ext_ohlc['l']} C:{ext_ohlc['c']}"
                            else:
                                issue['ext_ohlc'] = "N/A"
                            failures.append(issue)

    # Output report
    if failures or verified_moves:
        print("\n" + "="*140)
        print(f"DATA INTEGRITY REPORT - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("="*140)
        
        if failures:
            print(f"\n[!!!] INTEGRITY FAILURES ({len(failures)})")
            df_fail = pd.DataFrame(failures)
            cols = ['symbol', 'date', 'change_pct', 'status', 'local_ohlc', 'ext_ohlc']
            print(df_fail[cols].to_string(index=False))
            
        if verified_moves:
            print(f"\n[OK] VERIFIED MARKET EVENTS ({len(verified_moves)})")
            df_ver = pd.DataFrame(verified_moves)
            cols = ['symbol', 'date', 'change_pct', 'status', 'local_ohlc']
            print(df_ver[cols].to_string(index=False))

        print("\n" + "="*140)
        
        # Save report to output directory
        output_dir = PROJECT_ROOT / "output"
        output_dir.mkdir(exist_ok=True)
        report_file = output_dir / f"integrity_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(report_file, 'w') as f:
            f.write(f"DATA INTEGRITY REPORT - {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            if failures:
                f.write(f"\nINTEGRITY FAILURES\n" + df_fail[cols].to_string(index=False) + "\n")
            if verified_moves:
                f.write(f"\nVERIFIED MARKET EVENTS\n" + df_ver[cols].to_string(index=False) + "\n")
        logger.info(f"Report saved to {report_file}")
        
        if args.auto_patch and failures:
            print("\n[TIP] Auto-patches applied. Please re-run with --auto-fix to refresh data.")
    else:
        logger.info("No data integrity issues found.")

if __name__ == "__main__":
    main()
