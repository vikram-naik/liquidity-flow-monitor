"""
LFM: Screener Backtester.
Runs the hybrid Quantitative Gate and Qualitative LLM Guard (llama.cpp)
retrospectively from a target start-date to analyze screening performance.
"""

from __future__ import annotations

import os
import sys
import argparse
import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
from tabulate import tabulate

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
from src.trading.signals.base import Trade
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross
from src.trading.signals.savgol_cts.config import UniversalCrossExitConfig
from src.trading.signals.enums import ExitReason

from dotenv import load_dotenv
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from src.database import DB_PATH
from src.divergence_engine.engine import DivergenceEngine
from src.agents.guard_orchestrator import GuardOrchestrator
from src.agents.llm import get_llm_provider, SimulationProvider

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def get_watchlist_symbols(watchlist_name: str) -> list[str]:
    """Fetch symbols belonging to a watchlist from the SQLite database."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        query = """
            SELECT symbol FROM watchlist_items 
            WHERE watchlist_id = (SELECT id FROM watchlists WHERE name = ?)
            ORDER BY display_order
        """
        rows = conn.execute(query, (watchlist_name,)).fetchall()
        if not rows:
            # Fallback check
            avail = [r[0] for r in conn.execute("SELECT name FROM watchlists").fetchall()]
            logger.warning(f"Watchlist '{watchlist_name}' not found. Available: {avail}")
            return []
        return [r[0] for r in rows]
    except Exception as e:
        logger.error(f"Failed to query watchlist symbols: {e}")
        return []
    finally:
        conn.close()


def precalculate_symbol(symbol: str) -> tuple[str, pd.DataFrame | None]:
    """Runs DivergenceEngine for a single symbol and returns its calculated ledger."""
    try:
        engine = DivergenceEngine(symbol)
        df = engine.run().ledger
        if df is not None and not df.empty:
            df['date_str'] = df['date'].astype(str).str[:10]
            return symbol, df
    except Exception as e:
        logger.warning(f"Failed pre-calculating {symbol}: {e}")
    return symbol, None


def simulate_universal_cross_trade(
    df: pd.DataFrame, 
    entry_signal_idx: int, 
    setup_tag: str
) -> tuple[float | None, int | None, str | None]:
    """
    Simulates a trade from entry bar (entry_signal_idx + 1) using the exact
    UniversalCross exit mechanics.
    
    Returns:
        pnl_pct: Percentage profit/loss of the trade.
        bars_held: Number of bars the trade was held.
        exit_reason: The exit reason string, or "Max Hold" if active at the end.
    """
    n = len(df)
    entry_idx = entry_signal_idx + 1
    if entry_idx >= n:
        return None, None, None

    entry_row = df.iloc[entry_idx]
    entry_price = float(entry_row.get("close", 0.0))
    if entry_price <= 0.0:
        return None, None, None

    trade = Trade(
        symbol=str(entry_row.get("symbol", "UNKNOWN")),
        entry_date=str(entry_row.get("date", ""))[:10],
        entry_price=entry_price,
        entry_idx=entry_idx,
        atr_at_entry=float(entry_row.get("atr_20", 0.0)),
        entry_tag=setup_tag
    )

    records = df.to_dict("records")
    peak_close = entry_price
    state_val = 0
    pending_exit_reason = None
    exit_idx = None
    exit_price = None

    # Instantiate the default Universal Cross exit config
    exit_cfg = UniversalCrossExitConfig()

    for i in range(entry_idx + 1, n):
        row = records[i]
        prev = records[i - 1]
        bars_held = i - entry_idx

        # If an exit was triggered on the previous bar, execute it at today's open (EOD-lag)
        if pending_exit_reason is not None:
            open_price = row.get("open", np.nan)
            exit_price = open_price if not np.isnan(open_price) else float(row.get("close", 0.0))
            exit_idx = i
            break

        # Track peak close
        close = float(row.get("close", 0.0))
        peak_close = max(peak_close, close)

        # Evaluate exit condition
        reason, state_val = exit_universal_cross(
            row=row,
            prev_row=prev,
            trade=trade,
            peak_close=peak_close,
            bars_held=bars_held,
            state_val=state_val,
            cfg=exit_cfg,
            records=records,
            idx=i
        )

        if reason is not None:
            pending_exit_reason = reason

    # If the backtest period ended before an exit was triggered, close at the final bar's close
    if exit_idx is None:
        last_row = records[-1]
        exit_price = float(last_row.get("close", 0.0))
        exit_idx = n - 1
        trade.exit_reason = pending_exit_reason or "Max Hold"
    else:
        trade.exit_reason = pending_exit_reason

    trade.exit_price = exit_price
    trade.exit_date = str(records[exit_idx].get("date", ""))[:10]
    pnl = round(((exit_price / entry_price) - 1.0) * 100.0, 2)
    bars_held = exit_idx - entry_idx

    # Handle enum conversions gracefully for return string
    reason_str = trade.exit_reason.value if hasattr(trade.exit_reason, "value") else str(trade.exit_reason)

    return pnl, bars_held, reason_str


async def run_backtest():
    parser = argparse.ArgumentParser(description="LFM Screener Backtester (Quantitative Gate + Qualitative LLM Guard)")
    parser.add_argument("--start-date", required=True, help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=datetime.now().strftime("%Y-%m-%d"), help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--watchlist", default=None, help="Name of the watchlist to test (e.g. 'NIFTY 50')")
    parser.add_argument("--symbols", default=None, help="Comma-separated ticker override list (e.g. INFY,TCS,RELIANCE)")
    parser.add_argument("--limit", type=int, default=5, help="Ceiling limit of LLM candidates audited per day")
    parser.add_argument("--provider", default=None, help="Override LLM provider name (e.g., llamacpp, simulation)")
    parser.add_argument("--threshold", type=float, default=80.0, help="Quantitative Gate s_total threshold (default: 80.0)")
    parser.add_argument("--system-prompt-override", default=None, help="Path to text file containing a custom system prompt override")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")
    args = parser.parse_args()

    # Configure logging dynamically
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)
        for handler in logging.getLogger().handlers:
            handler.setLevel(logging.DEBUG)
        logger.debug("Verbose debug logging enabled.")

    # 1. Resolve Symbols
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        logger.info(f"Using manual symbol overrides ({len(symbols)} tickers): {symbols}")
    elif args.watchlist:
        symbols = get_watchlist_symbols(args.watchlist)
        logger.info(f"Loaded {len(symbols)} symbols from watchlist '{args.watchlist}'")
    else:
        # Default to NIFTY 50
        symbols = get_watchlist_symbols("NIFTY 50")
        logger.info(f"Defaulting to NIFTY 50 watchlist ({len(symbols)} symbols)")

    if not symbols:
        logger.error("No target symbols resolved. Exiting.")
        return

    # 2. Pre-calculate all symbol ledgers in parallel to eliminate redundant sequential calculations!
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, as_completed

    logger.info(f"Pre-calculating ledger DataFrames for {len(symbols)} symbols in parallel...")
    ledgers = {}
    
    with ProcessPoolExecutor(max_workers=max(1, multiprocessing.cpu_count() - 1)) as executor:
        futures = {executor.submit(precalculate_symbol, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym, df = fut.result()
            if df is not None and not df.empty:
                ledgers[sym] = df

    # Resolve Trading Days using one of the precalculated ledgers
    trading_days = []
    for sym in symbols:
        if sym in ledgers:
            df = ledgers[sym]
            mask = (df['date_str'] >= args.start_date) & (df['date_str'] <= args.end_date)
            days = df.loc[mask, 'date_str'].tolist()
            if days:
                trading_days = sorted(list(set(days)))
                break

    if not trading_days:
        # Fallback
        start = datetime.strptime(args.start_date, "%Y-%m-%d")
        end = datetime.strptime(args.end_date, "%Y-%m-%d")
        delta = end - start
        trading_days = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(delta.days + 1)]

    logger.info(f"Resolved {len(trading_days)} target trading days between {args.start_date} and {args.end_date}")

    # Load custom system prompt if override specified
    custom_system_prompt = None
    if args.system_prompt_override:
        try:
            with open(args.system_prompt_override, "r", encoding="utf-8") as f:
                custom_system_prompt = f.read().strip()
            logger.info(f"Loaded custom system prompt override from: {args.system_prompt_override}")
        except Exception as e:
            logger.error(f"Failed to load custom system prompt override from {args.system_prompt_override}: {e}")
            return

    # 3. Instantiate LLM Provider and Orchestrator
    provider = get_llm_provider(args.provider, system_prompt=custom_system_prompt)
    orchestrator = GuardOrchestrator(llm_provider=provider)

    backtest_records = []

    # 4. Iterative Daily Backtest Loop
    for day_idx, target_date in enumerate(trading_days):
        logger.info(f"\n==================================================================")
        logger.info(f"PROCESSING HISTORICAL TRADING DAY: {target_date} ({day_idx + 1}/{len(trading_days)})")
        logger.info(f"==================================================================")
        
        gate_candidates = []
        
        # Run quantitative screening retrospectively for all symbols using cached DataFrames
        for symbol, df in ledgers.items():
            try:
                matches = df.index[df['date_str'] == target_date].tolist()
                if not matches:
                    continue
                    
                idx = matches[0]
                row_t = df.iloc[idx]
                
                # Check if it cleared the Gate
                s_total = float(row_t.get("s_total", 0.0))
                
                if s_total >= args.threshold:
                    gate_candidates.append({
                        "symbol": symbol,
                        "date": target_date,
                        "price": float(row_t.get("close", 0.0)),
                        "gate_score": s_total,
                        "setup_tag": str(row_t.get("gate_setup", "None")),
                        "_ledger_idx": idx,
                        "_ledger_df": df
                    })
            except Exception as e:
                logger.warning(f"Error screening {symbol} on {target_date}: {e}")
                continue
                
        if not gate_candidates:
            logger.info(f"No candidates cleared the Gate on {target_date}.")
            continue
            
        # Sort candidates by quantitative triage score (s_total) descending, and limit daily LLM runs
        gate_candidates = sorted(gate_candidates, key=lambda x: x["gate_score"], reverse=True)
        audited_candidates = gate_candidates[:args.limit]
        logger.info(f"Found {len(gate_candidates)} Gate candidates. Auditing top {len(audited_candidates)} (ceiling={args.limit}).")
        
        # Run qualitative forensic audits (respecting rate-limiting Sequential sleep of 15s if live calls are executed)
        for cand_idx, candidate in enumerate(audited_candidates):
            # Apply sequential API pacing if not in simulation mode
            if cand_idx > 0 and not isinstance(provider, SimulationProvider):
                logger.info("Pacing API Quotas: Sleeping 15 seconds between forensic audits...")
                await asyncio.sleep(15.0)
                
            symbol = candidate["symbol"]
            logger.info(f"Auditing candidate {symbol}...")
            
            # Execute audit
            audit_result = await orchestrator.execute_forensic_audit(candidate)
            
            if not audit_result:
                logger.warning(f"Forensic audit returned empty results for {symbol} on {target_date}.")
                continue
                
            # Simulate trade using Universal Cross exit mechanics (EOD-lag)
            ledger_df = candidate["_ledger_df"]
            ledger_idx = candidate["_ledger_idx"]
            setup_tag = candidate["setup_tag"]
            pnl_pct, hold_bars, exit_reason = simulate_universal_cross_trade(ledger_df, ledger_idx, setup_tag)
            
            # Save record
            record = {
                "date": target_date,
                "symbol": symbol,
                "price": candidate["price"],
                "gate_setup": candidate["setup_tag"],
                "gate_score": round(candidate["gate_score"], 2),
                "verdict": audit_result.get("verdict", "VETO"),
                "catalyst": audit_result.get("catalyst_type", "UNKNOWN"),
                "fund_grade": audit_result.get("fundamental_grade", "F"),
                "gov_risk": audit_result.get("governance_risk", "CRITICAL"),
                "qual_score": audit_result.get("qualitative_score", 0.0),
                "pnl_pct": pnl_pct if pnl_pct is not None else "N/A",
                "hold_bars": hold_bars if hold_bars is not None else "N/A",
                "exit_reason": exit_reason if exit_reason is not None else "N/A",
                "red_flags": ", ".join(audit_result.get("veto_reasons", [])),
                "citations": ", ".join(audit_result.get("evidence_citations", []))
            }
            backtest_records.append(record)
            logger.info(f"Result for {symbol}: Verdict={record['verdict']} | PnL: {record['pnl_pct']}% | Exit: {record['exit_reason']}")

    # 5. Output Summary Report
    logger.info(f"\n==================================================================")
    logger.info("BACKTEST EXECUTION COMPLETE")
    logger.info(f"==================================================================")
    
    if not backtest_records:
        logger.info("No candidates were audited during the backtest period.")
        return
        
    df_report = pd.DataFrame(backtest_records)
    
    # Print rich formatted table to console
    print("\n--- DETAILED HISTORICAL SIGNALS LEDGER ---")
    headers = ["Date", "Symbol", "Price", "Setup", "Gate Score", "Verdict", "Qual Score", "PnL %", "Hold Bars", "Exit Reason"]
    console_table = df_report[["date", "symbol", "price", "gate_setup", "gate_score", "verdict", "qual_score", "pnl_pct", "hold_bars", "exit_reason"]]
    print(tabulate(console_table, headers=headers, tablefmt="grid", showindex=False))

    # Calculate and print performance aggregates
    print("\n--- BACKTEST PERFORMANCE METRICS ---")
    total_audits = len(df_report)
    approved_df = df_report[df_report["verdict"] == "APPROVE"]
    vetoed_df = df_report[df_report["verdict"] == "VETO"]
    
    print(f"Total Audits Performed: {total_audits}")
    print(f"Approved Candidates:    {len(approved_df)} ({len(approved_df)/total_audits*100:.1f}%)")
    print(f"Vetoed Candidates:      {len(vetoed_df)} ({len(vetoed_df)/total_audits*100:.1f}%)")
    
    # Calculate returns for approved vs vetoed if numeric returns are available
    for name, subset in [("APPROVED", approved_df), ("VETOED", vetoed_df)]:
        numeric_pnl = pd.to_numeric(subset["pnl_pct"], errors="coerce").dropna()
        numeric_hold = pd.to_numeric(subset["hold_bars"], errors="coerce").dropna()
        if not numeric_pnl.empty:
            win_rate = (numeric_pnl > 0).mean() * 100.0
            print(f"Avg PnL for {name} candidates: {numeric_pnl.mean():.2f}% | Win Rate: {win_rate:.1f}%")
        if not numeric_hold.empty:
            print(f"Avg Hold Bars for {name} candidates: {numeric_hold.mean():.1f} bars")

    # 6. Save CSV Report to Output Directory
    output_dir = PROJECT_ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    csv_path = output_dir / f"backtest_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    df_report.to_csv(csv_path, index=False)
    logger.info(f"Saved complete backtest spreadsheet to: {csv_path}")


if __name__ == "__main__":
    asyncio.run(run_backtest())
