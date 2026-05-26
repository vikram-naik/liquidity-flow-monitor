#!/usr/bin/env python3
"""
Study script to scan a watchlist's history for a specific bullish trigger:
1. RSZ Velocity (rsz_v) turns positive (crosses from <= 0 to > 0)
2. PSZ Velocity (psz_v) is positive (> 0)
3. PSZ Velocity (psz_v) is rising over two bars (psz_v[T] > psz_v[T-1])

Exit is simulated using the exact default, max-optimized trailing exit mechanics of:
`src/trading/signals/savgol_cts/exits/universal_cross.py` (Universal Cross Exit Path).

Saves outputs to:
- output/{watchlist}_trigger_study.csv
- output/{watchlist}_trigger_study.md
- output/{watchlist}_trigger_optimization.md (if --optimize is run)
"""

import sys
import os
import argparse
import pandas as pd
import numpy as np
import sqlite3
import multiprocessing
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig, UniversalCrossExitConfig
from src.trading.signals.base import Trade
from src.trading.signals.enums import EntryTag, ExitReason
from src.database import get_db_connection, DB_PATH

# Worker function to load ledgers in parallel processes
def load_symbol_ledger(symbol: str) -> tuple[str, list[dict] | None]:
    try:
        # NEVER pass start_date or end_date during init per GEMINI.md
        engine = DivergenceEngine(symbol)
        res = engine.run()
        df = res.ledger
        if df is not None and not df.empty:
            # Drop any columns we absolutely don't need to reduce pickle size, but keep all standard cols
            # Format date to string to prevent serialization issues
            df['date_str'] = df['date'].astype(str).str[:10]
            records = df.to_dict('records')
            return symbol, records
    except Exception as e:
        print(f"Warning: Failed loading ledger for {symbol}: {e}")
    return symbol, None

def evaluate_trigger(
    row: dict,
    prev_row: dict,
    prev_row2: dict | None,
    rsz_v_cross_thresh: float = 0.0,
    psz_v_min: float = 0.0,
    psz_v_rising_bars: int = 1,
    cwc_slope_min: float | None = 0.0,
    coherence_min: float | None = None,
    only_bullish_regime: bool = False
) -> bool:
    """Evaluate whether the entry trigger is active on the signal day (row)."""
    # 1. rsz_v turns positive (crosses from <= thresh to > thresh)
    rsz_v = row.get("rsz_v", np.nan)
    rsz_v_prev = prev_row.get("rsz_v", np.nan)
    if np.isnan(rsz_v) or np.isnan(rsz_v_prev):
        return False
    
    if not (rsz_v_prev <= rsz_v_cross_thresh and rsz_v > rsz_v_cross_thresh):
        return False

    # 2. psz_v is positive
    psz_v = row.get("psz_v", np.nan)
    psz_v_prev = prev_row.get("psz_v", np.nan)
    if np.isnan(psz_v) or np.isnan(psz_v_prev):
        return False
    
    if psz_v <= psz_v_min:
        return False
        
    # 3. psz_v is rising
    if psz_v_rising_bars == 1:
        if psz_v <= psz_v_prev:
            return False
    elif psz_v_rising_bars == 2:
        if prev_row2 is None:
            return False
        psz_v_prev2 = prev_row2.get("psz_v", np.nan)
        if np.isnan(psz_v_prev2):
            return False
        if not (psz_v > psz_v_prev > psz_v_prev2):
            return False

    # 4. Optional cwc_slope filter
    if cwc_slope_min is not None:
        cwc_slope = row.get("cwc_slope", np.nan)
        if np.isnan(cwc_slope) or cwc_slope <= cwc_slope_min:
            return False

    # 5. Optional coherence filter
    if coherence_min is not None:
        coherence = row.get("coherence", np.nan)
        if np.isnan(coherence) or coherence < coherence_min:
            return False

    # 6. Optional regime filter
    if only_bullish_regime:
        regime = row.get("regime", "")
        if regime != "bullish":
            return False

    return True

def run_backtest_for_symbol(
    records: list[dict],
    start_date_str: str,
    entry_params: dict,
    exit_cfg: UniversalCrossExitConfig,
    symbol: str
) -> list[dict]:
    """Runs a backtest simulation for a single symbol using EOD-Lag entry and realistic exit trailing."""
    trades = []
    in_trade = False
    pending_entry = False
    entry_row = None
    entry_idx = 0
    trade_obj = None
    peak_close = 0.0
    state_val = 0
    
    # Pre-unpack parameters
    rsz_v_cross_thresh = entry_params.get("rsz_v_cross_thresh", 0.0)
    psz_v_min = entry_params.get("psz_v_min", 0.0)
    psz_v_rising_bars = entry_params.get("psz_v_rising_bars", 1)
    cwc_slope_min = entry_params.get("cwc_slope_min", 0.0)
    coherence_min = entry_params.get("coherence_min", None)
    only_bullish_regime = entry_params.get("only_bullish_regime", False)

    for i in range(2, len(records)):
        row = records[i]
        prev = records[i-1]
        prev2 = records[i-2]
        curr_date = row['date_str']
        
        # Skip checking entries/exits before start date filter
        if curr_date < start_date_str:
            continue
        
        if in_trade:
            bars_held = i - entry_idx
            close_now = row.get("close", np.nan)
            if np.isnan(close_now):
                continue
                
            if close_now > peak_close:
                peak_close = close_now
                
            # Realistic Exit Check
            reason, state_val = exit_universal_cross(
                row=row,
                prev_row=prev,
                trade=trade_obj,
                peak_close=peak_close,
                bars_held=bars_held,
                state_val=state_val,
                cfg=exit_cfg,
                records=records,
                idx=i
            )
            
            if reason is not None:
                exit_price = row.get('open', close_now)
                if np.isnan(exit_price):
                    exit_price = close_now
                pnl = (exit_price / entry_row['close'] - 1) * 100.0
                
                # Extract Highs & Lows within the window [T+2 : T+1+h]
                window_highs = [float(records[idx]['high']) for idx in range(entry_idx + 1, i + 1)]
                window_lows = [float(records[idx]['low']) for idx in range(entry_idx + 1, i + 1)]
                if not window_highs:
                    window_highs = [float(entry_row['high'])]
                if not window_lows:
                    window_lows = [float(entry_row['low'])]
                mfe_pct = (max(window_highs) / entry_row['close'] - 1) * 100.0
                mae_pct = (min(window_lows) / entry_row['close'] - 1) * 100.0
                
                trades.append({
                    'symbol': symbol,
                    'signal_date': str(records[entry_idx-1]['date_str']),
                    'entry_date': str(entry_row['date_str']),
                    'exit_date': str(row['date_str']),
                    'entry_price': entry_row['close'],
                    'exit_price': exit_price,
                    'pnl': pnl,
                    'mfe': mfe_pct,
                    'mae': mae_pct,
                    'duration': bars_held,
                    'exit_reason': reason.value if hasattr(reason, 'value') else str(reason)
                })
                in_trade = False
                entry_row = None
                trade_obj = None
            continue

        if pending_entry:
            # EOD-Lag: Entry price is the close of bar T+1
            entry_row = row
            entry_idx = i
            in_trade = True
            pending_entry = False
            peak_close = row.get('close', 0.0)
            state_val = 0
            
            atr = row.get("atr_20", 0.0)
            psz = row.get("price_slope_z", 0.0)
            
            trade_obj = Trade(
                symbol=symbol,
                entry_date=str(row['date_str']),
                entry_price=row.get('close', 0.0),
                entry_idx=i,
                atr_at_entry=atr if not np.isnan(atr) else 0.0,
                conviction_score=70,
                regime_at_entry=row.get('regime', '-'),
                entry_tag=EntryTag.UNIVERSAL_CROSS.value,
                psz_at_entry=psz if not np.isnan(psz) else 0.0,
                psz_peak=psz if not np.isnan(psz) else 0.0
            )
            continue
            
        # Check Entry Trigger on the signal day (prev, which represents bar T)
        is_trig = evaluate_trigger(
            row=prev,
            prev_row=prev2,
            prev_row2=records[i-3] if i >= 3 else None,
            rsz_v_cross_thresh=rsz_v_cross_thresh,
            psz_v_min=psz_v_min,
            psz_v_rising_bars=psz_v_rising_bars,
            cwc_slope_min=cwc_slope_min,
            coherence_min=coherence_min,
            only_bullish_regime=only_bullish_regime
        )
        if is_trig:
            pending_entry = True

    # Force close open trade at the end of data
    if in_trade and entry_row:
        last = records[-1]
        pnl = (last['close'] / entry_row['close'] - 1) * 100.0
        window_highs = [float(records[idx]['high']) for idx in range(entry_idx + 1, len(records))]
        window_lows = [float(records[idx]['low']) for idx in range(entry_idx + 1, len(records))]
        if not window_highs:
            window_highs = [float(entry_row['high'])]
        if not window_lows:
            window_lows = [float(entry_row['low'])]
        mfe_pct = (max(window_highs) / entry_row['close'] - 1) * 100.0
        mae_pct = (min(window_lows) / entry_row['close'] - 1) * 100.0
        
        trades.append({
            'symbol': symbol,
            'signal_date': str(records[entry_idx-1]['date_str']),
            'entry_date': str(entry_row['date_str']),
            'exit_date': str(last['date_str']),
            'entry_price': entry_row['close'],
            'exit_price': last['close'],
            'pnl': pnl,
            'mfe': mfe_pct,
            'mae': mae_pct,
            'duration': len(records) - 1 - entry_idx,
            'exit_reason': ExitReason.END_OF_DATA.value
        })
        
    return trades

def calculate_metrics(trades: list[dict]) -> dict:
    """Calculate summary statistics for a list of trade records."""
    if not trades:
        return {
            'count': 0, 'win_rate': 0.0, 'avg_pnl': 0.0, 'median_pnl': 0.0,
            'avg_mfe': 0.0, 'avg_mae': 0.0, 'profit_factor': 0.0, 'avg_duration': 0.0
        }
    
    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    win_rate = len(wins) / len(pnls) * 100.0
    avg_pnl = np.mean(pnls)
    median_pnl = np.median(pnls)
    avg_mfe = np.mean([t['mfe'] for t in trades])
    avg_mae = np.mean([t['mae'] for t in trades])
    avg_duration = np.mean([t['duration'] for t in trades])
    
    sum_wins = sum(wins)
    sum_losses = abs(sum(losses))
    profit_factor = sum_wins / sum_losses if sum_losses > 0 else float('inf')
    
    return {
        'count': len(trades),
        'win_rate': win_rate,
        'avg_pnl': avg_pnl,
        'median_pnl': median_pnl,
        'avg_mfe': avg_mfe,
        'avg_mae': avg_mae,
        'profit_factor': profit_factor,
        'avg_duration': avg_duration
    }

def get_watchlist_symbols(watchlist_name: str) -> list[str]:
    """Retrieve symbols belonging to a watchlist name from the SQLite database."""
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT id FROM watchlists WHERE name = ?", (watchlist_name,)).fetchone()
        if not row:
            print(f"Error: Watchlist '{watchlist_name}' not found in watchlists table.")
            return []
        
        symbols = [
            r[0] for r in conn.execute(
                "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY display_order",
                (row[0],)
            ).fetchall()
        ]
        return symbols
    except Exception as e:
        print(f"Error loading watchlist items: {e}")
        return []
    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser(description="Watchlist-Wide Bullish Trigger & Setup Optimization Study")
    parser.add_argument("--watchlist", default="NIFTY 50", type=str, help="Watchlist to analyze (default: NIFTY 50)")
    parser.add_argument("--symbol", default=None, type=str, help="NSE Symbol to analyze directly (overrides watchlist)")
    parser.add_argument("--start-date", default="2020-01-01", type=str, help="Start date filter in YYYY-MM-DD format (default: 2020-01-01)")
    parser.add_argument("--optimize", action="store_true", help="Run multi-parameter entry setup grid search optimization")
    parser.add_argument("--cores", default=multiprocessing.cpu_count(), type=int, help="Number of CPU cores for parallel loading")
    parser.add_argument("--min-trades", default=None, type=int, help="Minimum trade count required to include an optimized parameter set")
    args = parser.parse_args()

    start_date_str = args.start_date.strip()
    try:
        datetime.strptime(start_date_str, "%Y-%m-%d")
    except ValueError:
        print(f"Error: Invalid --start-date format '{start_date_str}'. Please use YYYY-MM-DD.")
        sys.exit(1)

    # 1. Determine tickers to analyze
    if args.symbol:
        symbols = [args.symbol.upper().strip()]
        watchlist_name = f"SYMBOL_{symbols[0]}"
        default_min_trades = 3
    else:
        watchlist_name = args.watchlist.upper().strip()
        symbols = get_watchlist_symbols(watchlist_name)
        default_min_trades = 10
        if not symbols:
            # Standard NSE fallback list per study scripts
            symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "BHARTIARTL", "SBIN", "LICI", "ITC", "HINDUNILVR"]
            watchlist_name = "NSE_TOP_FALLBACK"

    min_trades = args.min_trades if args.min_trades is not None else default_min_trades

    print(f"============================================================")
    print(f"Starting Trigger Watchlist PnL Study & Setup Optimization")
    print(f"Target Universe: {watchlist_name} ({len(symbols)} symbols)")
    print(f"Start Date Filter: {start_date_str}")
    print(f"Optimization Mode: {'ENABLED' if args.optimize else 'DISABLED'}")
    print(f"Minimum Trades Threshold: {min_trades}")
    print(f"============================================================")

    # 2. Parallel loading of symbol ledgers
    print(f"\n[1/3] Loading and warming DivergenceEngine for symbols in parallel (using {args.cores} workers)...", flush=True)
    symbol_ledgers = {}
    
    with ProcessPoolExecutor(max_workers=args.cores) as executor:
        futures = {executor.submit(load_symbol_ledger, sym): sym for sym in symbols}
        
        for i, future in enumerate(as_completed(futures)):
            sym, ledger_records = future.result()
            if i > 0 and i % 10 == 0:
                print(f"Progress: Loaded {i}/{len(symbols)} symbols...")
            if ledger_records:
                symbol_ledgers[sym] = ledger_records

    loaded_count = len(symbol_ledgers)
    print(f"Successfully loaded and warmed ledger data for {loaded_count}/{len(symbols)} symbols.\n")
    if not symbol_ledgers:
        print("Error: No ledgers loaded successfully. Exiting.")
        sys.exit(1)

    # 3. Setup realistic universal_cross exits using default, max-optimized config
    exit_cfg = SavgolCTSExitConfig().universal_cross

    # 4. Run baseline trade simulation
    baseline_params = {
        "rsz_v_cross_thresh": 0.0,
        "psz_v_min": 0.0,
        "psz_v_rising_bars": 1,
        "cwc_slope_min": 0.0,
        "coherence_min": None,
        "only_bullish_regime": False
    }

    print("[2/3] Simulating baseline setups across the watchlist...")
    all_baseline_trades = []
    for sym, records in symbol_ledgers.items():
        sym_trades = run_backtest_for_symbol(records, start_date_str, baseline_params, exit_cfg, sym)
        all_baseline_trades.extend(sym_trades)

    baseline_metrics = calculate_metrics(all_baseline_trades)
    
    print("\n" + "="*80)
    print(f"BASELINE SETUP PERFORMANCE SUMMARY - {watchlist_name}")
    print("="*80)
    print(f"Total Trades Taken  : {baseline_metrics['count']}")
    print(f"Win Rate            : {baseline_metrics['win_rate']:.1f}%")
    print(f"Average PnL         : {baseline_metrics['avg_pnl']:+.2f}%")
    print(f"Median PnL          : {baseline_metrics['median_pnl']:+.2f}%")
    print(f"Average MFE         : {baseline_metrics['avg_mfe']:.2f}%")
    print(f"Average MAE         : {baseline_metrics['avg_mae']:.2f}%")
    print(f"Profit Factor       : {baseline_metrics['profit_factor']:.2f}")
    print(f"Avg Bars Held       : {baseline_metrics['avg_duration']:.1f} bars")
    print("="*80 + "\n")

    # Output baseline trades and report
    output_dir = PROJECT_ROOT / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    watchlist_fn = watchlist_name.lower().replace(" ", "_")
    
    df_trades = pd.DataFrame(all_baseline_trades)
    csv_path = output_dir / f"{watchlist_fn}_trigger_study.csv"
    df_trades.to_csv(csv_path, index=False)
    print(f"Saved baseline trade log to CSV: {csv_path}")

    # Generate premium markdown report for baseline
    md_path = output_dir / f"{watchlist_fn}_trigger_study.md"
    with open(md_path, "w") as f:
        f.write(f"# Watchlist Bullish Trigger Study: {watchlist_name}\n\n")
        f.write(f"## Study Parameters & Metadata\n")
        f.write(f"- **Target Watchlist**: `{watchlist_name}`\n")
        f.write(f"- **Total Watchlist Constituents**: `{len(symbols)}`\n")
        f.write(f"- **Successfully Warmed/Processed**: `{loaded_count}`\n")
        f.write(f"- **Start Date Filter**: `{start_date_str}`\n")
        f.write(f"- **Baseline Trigger Conditions**:\n")
        f.write(f"  - `rsz_v` turns positive (`rsz_v[T] > 0` and `rsz_v[T-1] <= 0`)\n")
        f.write(f"  - `psz_v` is positive (`psz_v[T] > 0`)\n")
        f.write(f"  - `psz_v` is rising (`psz_v[T] > psz_v[T-1]`)\n")
        f.write(f"  - `cwc_slope` is positive (`cwc_slope[T] > 0.0`)\n")
        f.write(f"- **Execution Assumption**: EOD-Lag (Buy at CLOSE of bar T+1, exit via `exit_universal_cross` from T+2 onwards)\n")
        f.write(f"- **Exit Mechanics**: Standarized platform exits of `exit_universal_cross` (max-optimized, including trailing, near-miss, and panic exits).\n")
        f.write(f"- **Report Generated**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n\n")

        f.write(f"## Baseline Performance Summary\n\n")
        f.write(f"| Metric | Value |\n")
        f.write(f"| --- | --- |\n")
        f.write(f"| **Total Trades Taken** | {baseline_metrics['count']} |\n")
        f.write(f"| **Win Rate** | {baseline_metrics['win_rate']:.1f}% |\n")
        f.write(f"| **Average PnL** | {baseline_metrics['avg_pnl']:+.2f}% |\n")
        f.write(f"| **Median PnL** | {baseline_metrics['median_pnl']:+.2f}% |\n")
        f.write(f"| **Average MFE (Draw-up)** | {baseline_metrics['avg_mfe']:.2f}% |\n")
        f.write(f"| **Average MAE (Draw-down)** | {baseline_metrics['avg_mae']:.2f}% |\n")
        f.write(f"| **Profit Factor** | {baseline_metrics['profit_factor']:.2f} |\n")
        f.write(f"| **Avg Holding Duration** | {baseline_metrics['avg_duration']:.1f} bars |\n\n")

        # Exit reasons breakdown
        if all_baseline_trades:
            f.write(f"### Exit Reasons Breakdown\n\n")
            reasons = pd.Series([t['exit_reason'] for t in all_baseline_trades]).value_counts()
            f.write(f"| Exit Reason | Count | Percentage |\n")
            f.write(f"| --- | --- | --- |\n")
            for reason, count in reasons.items():
                pct = count / len(all_baseline_trades) * 100.0
                f.write(f"| {reason} | {count} | {pct:.1f}% |\n")
            f.write("\n")

        # Top 15 Trades
        if all_baseline_trades:
            f.write(f"### Top 15 Best Trades\n\n")
            top_15 = df_trades.sort_values(by="pnl", ascending=False).head(15)
            f.write(f"| Symbol | Signal Date | Entry Date | Exit Date | Entry Price | Exit Price | PnL % | Duration |\n")
            f.write(f"| --- | --- | --- | --- | --- | --- | --- | --- |\n")
            for _, r in top_15.iterrows():
                f.write(f"| **{r['symbol']}** | {r['signal_date']} | {r['entry_date']} | {r['exit_date']} | {r['entry_price']:.2f} | {r['exit_price']:.2f} | {r['pnl']:+.2f}% | {r['duration']} |\n")
            f.write("\n")

            f.write(f"### Bottom 15 Worst Trades\n\n")
            bottom_15 = df_trades.sort_values(by="pnl", ascending=True).head(15)
            f.write(f"| Symbol | Signal Date | Entry Date | Exit Date | Entry Price | Exit Price | PnL % | Duration |\n")
            f.write(f"| --- | --- | --- | --- | --- | --- | --- | --- |\n")
            for _, r in bottom_15.iterrows():
                f.write(f"| **{r['symbol']}** | {r['signal_date']} | {r['entry_date']} | {r['exit_date']} | {r['entry_price']:.2f} | {r['exit_price']:.2f} | {r['pnl']:+.2f}% | {r['duration']} |\n")
            f.write("\n")

    print(f"Saved premium baseline study report to: {md_path}")

    # 5. Optimization Mode
    if args.optimize:
        print("\n[3/3] Running multi-parameter setup grid search optimizer...")
        
        # Grid parameters to search (High Quality Setup Exploration)
        rsz_v_cross_grid = [0.0, 0.05, 0.1, 0.15, 0.2]
        psz_v_min_grid = [0.02, 0.05, 0.1, 0.15, 0.2]
        psz_v_rising_grid = [1, 2]
        cwc_slope_grid = [0.0, 0.02, None]
        coherence_grid = [0.3, 0.5, 0.7]
        only_bullish_grid = [False, True]
        
        results = []
        
        # Build combinations
        combinations = []
        for rsz in rsz_v_cross_grid:
            for psz in psz_v_min_grid:
                for rising in psz_v_rising_grid:
                    for slope in cwc_slope_grid:
                        for coh in coherence_grid:
                            for bullish in only_bullish_grid:
                                combinations.append({
                                    "rsz_v_cross_thresh": rsz,
                                    "psz_v_min": psz,
                                    "psz_v_rising_bars": rising,
                                    "cwc_slope_min": slope,
                                    "coherence_min": coh,
                                    "only_bullish_regime": bullish
                                })
        
        print(f"Evaluating {len(combinations)} total parameter combinations across {loaded_count} symbols...", flush=True)
        
        # Run combinations sequentially in-memory (in-memory execution is extremely fast)
        step_mod = len(combinations) // 10 if len(combinations) >= 10 else 1
        for idx, entry_params in enumerate(combinations):
            if idx > 0 and idx % step_mod == 0:
                print(f"Optimization Progress: {idx}/{len(combinations)} combinations evaluated...")
                
            trades = []
            for sym, records in symbol_ledgers.items():
                sym_trades = run_backtest_for_symbol(records, start_date_str, entry_params, exit_cfg, sym)
                trades.extend(sym_trades)
                
            # Filter by min_trades threshold
            if len(trades) >= min_trades:
                metrics = calculate_metrics(trades)
                results.append({
                    "params": entry_params,
                    "trade_count": metrics['count'],
                    "win_rate": metrics['win_rate'],
                    "avg_pnl": metrics['avg_pnl'],
                    "median_pnl": metrics['median_pnl'],
                    "avg_mfe": metrics['avg_mfe'],
                    "avg_mae": metrics['avg_mae'],
                    "profit_factor": metrics['profit_factor'],
                    "avg_duration": metrics['avg_duration']
                })
        
        # Create DataFrame of results
        df_res = pd.DataFrame(results)
        if df_res.empty:
            print("\nError: No optimized configurations satisfied the minimum trade threshold. Please try lowering --min-trades.")
            return

        # Sort by avg_pnl descending
        df_res = df_res.sort_values(by="avg_pnl", ascending=False).reset_index(drop=True)
        
        # Extract setups with avg_pnl > 5%
        setups_above_5 = df_res[df_res['avg_pnl'] > 5.0]
        
        print("\n" + "="*80)
        print(f"GRID SEARCH OPTIMIZATION RESULTS SUMMARY - {watchlist_name}")
        print("="*80)
        print(f"Total Configs Satisfying Min Trades ({min_trades}): {len(df_res)}")
        print(f"Configs Achieving Avg PnL > 5%                : {len(setups_above_5)}")
        print("="*80)
        
        if not setups_above_5.empty:
            best_setup = setups_above_5.iloc[0]
            print(f"\n★★★ BEST OPTIMIZED SETUP (Avg PnL: {best_setup['avg_pnl']:+.2f}%) ★★★")
            print(f"Trade Count   : {best_setup['trade_count']}")
            print(f"Win Rate      : {best_setup['win_rate']:.1f}%")
            print(f"Profit Factor : {best_setup['profit_factor']:.2f}")
            print(f"Avg Hold Time : {best_setup['avg_duration']:.1f} bars")
            print("\nOptimal Parameters:")
            for k, v in best_setup['params'].items():
                print(f"  - {k:<25}: {v}")
        else:
            print("\nWarning: No configurations achieved an average PnL > 5%. Listing the top 5 setups below instead.")
            best_setup = df_res.iloc[0]
            print(f"\n★★★ TOP PERFORMING SETUP (Avg PnL: {best_setup['avg_pnl']:+.2f}%) ★★★")
            print(f"Trade Count   : {best_setup['trade_count']}")
            print(f"Win Rate      : {best_setup['win_rate']:.1f}%")
            print(f"Profit Factor : {best_setup['profit_factor']:.2f}")
            print(f"Avg Hold Time : {best_setup['avg_duration']:.1f} bars")
            print("\nTop Parameters:")
            for k, v in best_setup['params'].items():
                print(f"  - {k:<25}: {v}")
        print("="*80 + "\n")

        # Save Optimization Markdown Report
        opt_md_path = output_dir / f"{watchlist_fn}_trigger_optimization.md"
        with open(opt_md_path, "w") as f:
            f.write(f"# Watchlist Setup Optimization Report: {watchlist_name}\n\n")
            f.write(f"## Optimization Settings & Context\n")
            f.write(f"- **Target Watchlist**: `{watchlist_name}`\n")
            f.write(f"- **Start Date Filter**: `{start_date_str}`\n")
            f.write(f"- **Min Trades Filter**: `{min_trades}`\n")
            f.write(f"- **Exit Mechanics**: Standard Platform Universal Cross Trailing Exit (Fixed & Max-Optimized)\n")
            f.write(f"- **Total Parameter Sets Evaluated**: `{len(combinations)}`\n")
            f.write(f"- **Configs Achieving Avg PnL > 5%**: `{len(setups_above_5)}`\n")
            f.write(f"- **Report Generated**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n\n")

            if not setups_above_5.empty:
                f.write(f"## ★★★ Optimal Parameter Configuration ★★★\n\n")
                f.write(f"This configuration achieves the highest average PnL over `{min_trades}+` trades:\n\n")
                f.write(f"| Parameter / Metric | Optimal Value |\n")
                f.write(f"| --- | --- |\n")
                f.write(f"| `rsz_v_cross_thresh` | `{best_setup['params']['rsz_v_cross_thresh']}` |\n")
                f.write(f"| `psz_v_min` | `{best_setup['params']['psz_v_min']}` |\n")
                f.write(f"| `psz_v_rising_bars` | `{best_setup['params']['psz_v_rising_bars']}` |\n")
                f.write(f"| `cwc_slope_min` | `{best_setup['params']['cwc_slope_min']}` |\n")
                f.write(f"| `coherence_min` | `{best_setup['params']['coherence_min']}` |\n")
                f.write(f"| `only_bullish_regime` | `{best_setup['params']['only_bullish_regime']}` |\n")
                f.write(f"| **Avg PnL %** | **{best_setup['avg_pnl']:+.2f}%** |\n")
                f.write(f"| **Win Rate %** | **{best_setup['win_rate']:.1f}%** |\n")
                f.write(f"| **Profit Factor** | **{best_setup['profit_factor']:.2f}** |\n")
                f.write(f"| **Trade Count** | `{best_setup['trade_count']}` |\n")
                f.write(f"| **Avg Holding Duration** | `{best_setup['avg_duration']:.1f} bars` |\n\n")
            else:
                f.write(f"## No Configurations Achieved Avg PnL > 5%\n\n")
                f.write(f"Listing metrics for the absolute top-performing setup below:\n\n")
                f.write(f"| Parameter / Metric | Top Value |\n")
                f.write(f"| --- | --- |\n")
                f.write(f"| `rsz_v_cross_thresh` | `{best_setup['params']['rsz_v_cross_thresh']}` |\n")
                f.write(f"| `psz_v_min` | `{best_setup['params']['psz_v_min']}` |\n")
                f.write(f"| `psz_v_rising_bars` | `{best_setup['params']['psz_v_rising_bars']}` |\n")
                f.write(f"| `cwc_slope_min` | `{best_setup['params']['cwc_slope_min']}` |\n")
                f.write(f"| `coherence_min` | `{best_setup['params']['coherence_min']}` |\n")
                f.write(f"| `only_bullish_regime` | `{best_setup['params']['only_bullish_regime']}` |\n")
                f.write(f"| **Avg PnL %** | **{best_setup['avg_pnl']:+.2f}%** |\n")
                f.write(f"| **Win Rate %** | **{best_setup['win_rate']:.1f}%** |\n")
                f.write(f"| **Profit Factor** | **{best_setup['profit_factor']:.2f}** |\n")
                f.write(f"| **Trade Count** | `{best_setup['trade_count']}` |\n")
                f.write(f"| **Avg Holding Duration** | `{best_setup['avg_duration']:.1f} bars` |\n\n")

            f.write(f"## Top 30 Parameter Configurations Sorted by Avg PnL\n\n")
            f.write(f"Below are the top 30 parameter combinations that met the min trade threshold:\n\n")
            f.write(f"| Rank | rsz_v_cross | psz_v_min | psz_v_rising | cwc_slope_min | coherence_min | bullish_only | Trades | Win Rate | Profit Factor | Avg PnL |\n")
            f.write(f"| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
            
            top_30 = df_res.head(30)
            for idx, r in top_30.iterrows():
                f.write(f"| {idx+1} | {r['params']['rsz_v_cross_thresh']} | {r['params']['psz_v_min']} | {r['params']['psz_v_rising_bars']} | {r['params']['cwc_slope_min']} | {r['params']['coherence_min']} | {r['params']['only_bullish_regime']} | {r['trade_count']} | {r['win_rate']:.1f}% | {r['profit_factor']:.2f} | **{r['avg_pnl']:+.2f}%** |\n")
            
        print(f"Saved premium optimization report to: {opt_md_path}")

if __name__ == "__main__":
    main()
