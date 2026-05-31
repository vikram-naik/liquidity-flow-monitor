#!/usr/bin/env python3
"""
Study script: Shock and PRT Co-inflection.

Simulates entry signals where:
- dv_shock < -1.0 (deep institutional delivery volume dry-up)
- prt < prt_buy_threshold (adaptive 10th percentile oversold floor)

Exits are handled using the standard SavgolCTS universal_cross exit mechanics.
Backtest is run on the dynamic NIFTY 50 stock universe starting from 14-Nov-2025.
Saves a markdown report to output/shock_prt_study.md.
"""

import sys
import os
import argparse
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from tabulate import tabulate

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.walk_forward import get_watchlist_symbols, today_str
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig, UniversalCrossExitConfig
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross
from src.trading.signals.savgol_cts.state import SavgolCTSExitState
from src.trading.signals.enums import ExitReason

def check_gap_down(records: list[dict], idx: int, lookback: int = 10) -> bool:
    """
    Checks if a significant gap down occurred in the recent lookback window.
    Gap down: prev_low > current_high AND gap > 0.3 * ATR.
    """
    start = max(1, idx - lookback)
    for i in range(start, idx + 1):
        prev_low = records[i-1].get("low", 0.0)
        curr_high = records[i].get("high", 0.0)
        atr = records[i].get("atr_20", 0.0)
        if prev_low > curr_high:
            gap_size = prev_low - curr_high
            if atr > 0 and gap_size > (0.3 * atr):
                return True
    return False

def simulate_shock_prt_trades(
    ticker: str,
    df: pd.DataFrame,
    start_date: str,
    exit_cfg: UniversalCrossExitConfig,
    dv_shock_thresh: float = -1.0,
    dv_shock_op: str = "lt"
) -> list[Trade]:
    """
    Simulates trades bar-by-bar using the EOD-Lag execution model.
    - Signal fires on bar i if: dv_shock condition met AND prt < prt_buy_threshold AND no recent gap down
    - Trade enters on bar i+1 at the close
    - Exit checks start on bar i+2
    """
    records = df.to_dict("records")
    n = len(records)
    trades = []
    in_trade = False
    trade = None
    peak_close = 0.0
    pending_signal = False
    pending_sig_data = {}
    pending_exit_reason = None
    state_val = 0

    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue

        # 1. Execute pending exit at today's open (EOD-lag)
        if pending_exit_reason is not None:
            open_price = row.get("open", np.nan)
            exit_price = open_price if not np.isnan(open_price) else close
            
            trade.exit_date = str(row.get("date", ""))[:10]
            trade.exit_price = round(exit_price, 2)
            trade.exit_reason = pending_exit_reason
            trade.pnl_pct = round((exit_price / trade.entry_price - 1) * 100, 2)
            trade.duration = i - trade.entry_idx
            trade.mfe_pct = round(trade.mfe_pct, 2)
            trade.mae_pct = round(trade.mae_pct, 2)
            
            trades.append(trade)
            
            in_trade = False
            trade = None
            pending_exit_reason = None
            state_val = 0
            continue

        # 2. If already in trade, evaluate exits
        if in_trade:
            if close > peak_close:
                peak_close = close

            bars_held = i - trade.entry_idx
            mfe = max(trade.mfe_pct, (close / trade.entry_price - 1) * 100)
            mae_val = (close / trade.entry_price - 1) * 100
            mae = min(-trade.mae_pct, mae_val)
            trade.mfe_pct = mfe
            if -mae > trade.mae_pct:
                trade.mae_pct = -mae

            # exit_universal_cross exit check
            st = SavgolCTSExitState.from_int(state_val)
            cwvap = row.get("cwvap", np.nan)
            if not np.isnan(close) and not np.isnan(cwvap) and close > cwvap:
                st.price_above_cwvap = True

            exit_reason, new_state_val = exit_universal_cross(
                row, prev, trade, peak_close, bars_held, st.to_int(),
                exit_cfg, records, i
            )
            state_val = new_state_val

            if exit_reason:
                pending_exit_reason = exit_reason

        # 3. Enter trade if entry signal fired on the previous bar
        elif pending_signal:
            pending_signal = False
            atr = row.get("atr_20", 0.0)
            if atr <= 0 or np.isnan(atr):
                continue
            psz_now = row.get("price_slope_z", np.nan)
            
            trade = Trade(
                symbol=ticker,
                entry_date=str(row.get("date", ""))[:10],
                entry_price=close,
                entry_idx=i,
                atr_at_entry=atr,
                conviction_score=70,
                regime_at_entry=row.get("regime", "-"),
                entry_tag="Shock-PRT-Coincidence",
                psz_at_entry=psz_now if not np.isnan(psz_now) else 0.0,
                psz_peak=psz_now if not np.isnan(psz_now) else 0.0,
            )
            
            # Attach custom signal telemetry
            trade.signal_date = pending_sig_data.get("signal_date", "")
            trade.dv_shock_at_signal = pending_sig_data.get("dv_shock", 0.0)
            trade.prt_at_signal = pending_sig_data.get("prt", 0.0)
            trade.prt_bt_at_signal = pending_sig_data.get("prt_bt", 0.0)
            
            peak_close = close
            in_trade = True
            state_val = 0
            pending_sig_data = {}

        # 4. Check for entry signals
        else:
            dv_shock = row.get("dv_shock", np.nan)
            prt = row.get("prt", np.nan)
            prt_bt = row.get("prt_buy_threshold", np.nan)
            date_str = str(row.get("date", ""))[:10]

            if not np.isnan(dv_shock) and not np.isnan(prt) and not np.isnan(prt_bt):
                # Only check entry on or after start_date
                if date_str >= start_date:
                    # Apply operator
                    dv_ok = (dv_shock > dv_shock_thresh) if dv_shock_op == "gt" else (dv_shock < dv_shock_thresh)
                    if dv_ok and prt < prt_bt:
                        # Avoid signal if there was a gap down in the last 10 bars
                        if not check_gap_down(records, i, lookback=10):
                            pending_signal = True
                            pending_sig_data = {
                                "signal_date": date_str,
                                "dv_shock": dv_shock,
                                "prt": prt,
                                "prt_bt": prt_bt
                            }

    # 5. Handle open trade at end of history
    if in_trade and trade:
        last = records[-1]
        trade.exit_date = str(last.get("date", ""))[:10]
        trade.exit_price = last.get("close", trade.entry_price)
        trade.exit_reason = ExitReason.END_OF_DATA
        trade.pnl_pct = round((trade.exit_price / trade.entry_price - 1) * 100, 2)
        trade.duration = n - 1 - trade.entry_idx
        trade.mfe_pct = round(trade.mfe_pct, 2)
        trade.mae_pct = round(trade.mae_pct, 2)
        trades.append(trade)

    return trades

def compute_profit_factor(trades: list[Trade]) -> float:
    gross_win = sum(t.pnl_pct for t in trades if t.pnl_pct > 0)
    gross_loss = abs(sum(t.pnl_pct for t in trades if t.pnl_pct <= 0))
    if gross_loss == 0:
        return float("inf")
    return gross_win / gross_loss

def compute_expectancy(trades: list[Trade]) -> float:
    if not trades:
        return 0.0
    winners = [t.pnl_pct for t in trades if t.pnl_pct > 0]
    losers = [t.pnl_pct for t in trades if t.pnl_pct <= 0]
    n = len(trades)
    avg_win = np.mean(winners) if winners else 0.0
    avg_loss = abs(np.mean(losers)) if losers else 0.0
    wr = len(winners) / n
    lr = len(losers) / n
    return avg_win * wr - avg_loss * lr

def main():
    parser = argparse.ArgumentParser(description="Study Shock & PRT Co-inflection Entry Model")
    parser.add_argument("--watchlist", default="NIFTY 50", help="Watchlist name (default: NIFTY 50)")
    parser.add_argument("--start", default="2025-11-14", help="Backtest entry start date (default: 2025-11-14)")
    parser.add_argument("--dv-shock-thresh", type=float, default=-1.0, help="DV Shock Threshold (default: -1.0)")
    parser.add_argument("--dv-shock-op", default="lt", choices=["lt", "gt"], help="DV Shock Operator: lt or gt (default: lt)")
    args = parser.parse_args()

    watchlist_name = args.watchlist
    start_date = args.start
    dv_thresh = args.dv_shock_thresh
    dv_op = args.dv_shock_op
    
    print(f"================================================================================")
    print(f"  SHOCK & PRT CO-INFLECTION STUDY")
    print(f"  Watchlist: {watchlist_name}")
    print(f"  Entry start date: {start_date}")
    print(f"================================================================================")

    # 1. Fetch Watchlist Symbols
    try:
        symbols = get_watchlist_symbols(watchlist_name)
    except SystemExit:
        print(f"Error: Watchlist '{watchlist_name}' not found.")
        return

    print(f"Loaded {len(symbols)} symbols from watchlist '{watchlist_name}'.")
    
    # 2. Setup universal exit configuration
    exit_cfg = SavgolCTSExitConfig().universal_cross
    # Make sure exit rules are active
    exit_cfg.enabled = True
    exit_cfg.cts_near_miss_exit_enabled = True
    exit_cfg.prt_st_cross_enabled = True
    exit_cfg.cts_st_cross_enabled = True

    all_trades = []
    failed = []

    # 3. Process Symbols
    for idx, sym in enumerate(symbols, 1):
        try:
            print(f"[{idx}/{len(symbols)}] Simulating {sym}...", end="\r", flush=True)
            # NEVER pass start/end date during initialization to guarantee warmup
            engine = DivergenceEngine(sym)
            res = engine.run()
            
            ledger = res.ledger
            if ledger is None or ledger.empty:
                continue
                
            trades = simulate_shock_prt_trades(
                sym, ledger, start_date, exit_cfg,
                dv_shock_thresh=dv_thresh, dv_shock_op=dv_op
            )
            all_trades.extend(trades)
        except Exception as e:
            failed.append((sym, str(e)))
            print(f"\nFailed {sym}: {e}")

    print(f"\nProcessing complete! Simulated {len(symbols)} symbols. {len(failed)} failures.")
    
    # Filter trades entered after start_date (already filtered inside simulation, but good to double check)
    all_trades = [t for t in all_trades if str(t.entry_date) >= start_date]
    
    # 4. Generate Reports
    output_dir = PROJECT_ROOT / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "shock_prt_study.md"

    # Aggregating results
    total_trades = len(all_trades)
    
    print(f"\nTotal trades generated: {total_trades}")
    
    if total_trades == 0:
        print("No trades triggered under this configuration. Exit.")
        # Save a blank report
        with open(report_path, "w") as f:
            f.write(f"# Shock & PRT Co-inflection Study: {watchlist_name}\n\nNo trades generated since {start_date} under dv_shock {dv_op} {dv_thresh}.\n")
        return

    df_trades = pd.DataFrame([{
        "symbol": t.symbol,
        "entry_date": t.entry_date,
        "entry_price": t.entry_price,
        "exit_date": t.exit_date,
        "exit_price": t.exit_price,
        "pnl_pct": t.pnl_pct,
        "mfe_pct": t.mfe_pct,
        "mae_pct": t.mae_pct,
        "duration": t.duration,
        "exit_reason": t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason),
        "sig_date": getattr(t, "signal_date", ""),
        "dv_shock": getattr(t, "dv_shock_at_signal", 0.0),
        "prt": getattr(t, "prt_at_signal", 0.0),
        "prt_bt": getattr(t, "prt_bt_at_signal", 0.0),
    } for t in all_trades])

    winners = (df_trades["pnl_pct"] > 0).sum()
    losers = total_trades - winners
    win_rate = winners / total_trades * 100
    avg_pnl = df_trades["pnl_pct"].mean()
    median_pnl = df_trades["pnl_pct"].median()
    avg_win = df_trades.loc[df_trades["pnl_pct"] > 0, "pnl_pct"].mean() if winners > 0 else 0.0
    avg_loss = df_trades.loc[df_trades["pnl_pct"] <= 0, "pnl_pct"].mean() if losers > 0 else 0.0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
    profit_factor = compute_profit_factor(all_trades)
    expectancy = compute_expectancy(all_trades)
    symbols_traded = df_trades["symbol"].nunique()

    # Sort trades by exit date
    df_trades = df_trades.sort_values(by="exit_date")

    # Exit Breakdown
    exit_agg = (
        df_trades.groupby("exit_reason")
        .agg(count=("pnl_pct", "size"), avg_pnl=("pnl_pct", "mean"),
             win_rate=("pnl_pct", lambda x: round((x > 0).mean() * 100, 1)))
        .reset_index()
        .sort_values("count", ascending=False)
    )

    # Print summary metrics to console
    print("\n" + "=" * 60)
    print(f"  PERFORMANCE SUMMARY ({start_date} to today) | dv_shock {dv_op} {dv_thresh}")
    print("" + "=" * 60)
    print(f"  Trades:             {total_trades}")
    print(f"  Symbols Traded:     {symbols_traded}/{len(symbols)}")
    print(f"  Winners / Losers:   {winners} / {losers}")
    print(f"  Win Rate:           {win_rate:.1f}%")
    print(f"  Avg P&L:            {avg_pnl:+.2f}%")
    print(f"  Median P&L:         {median_pnl:+.2f}%")
    print(f"  Avg Winner:         {avg_win:+.2f}%")
    print(f"  Avg Loser:          {avg_loss:+.2f}%")
    print(f"  Payoff Ratio:       {payoff:.2f}x")
    print(f"  Profit Factor:      {profit_factor:.2f}")
    print(f"  Expectancy:         {expectancy:+.2f}%")
    print(f"  Avg Duration:       {df_trades['duration'].mean():.1f} bars")
    print("-" * 60)
    print(tabulate(exit_agg, headers=["Exit Reason", "Count", "Avg P&L%", "Win%"], tablefmt="simple", floatfmt=".2f"))
    print("=" * 60 + "\n")

    # Write Markdown Report
    with open(report_path, "w") as f:
        op_symbol = ">" if dv_op == "gt" else "<"
        f.write(f"# Quant Study: Shock & PRT Co-inflection\n\n")
        f.write(f"This report presents backtest results for the **Shock & PRT Co-inflection** entry signal, ")
        f.write(f"utilizing the standard `universal_cross` exit mechanics on the **{watchlist_name}** universe.\n\n")
        
        f.write(f"## Study Specifications\n")
        f.write(f"- **Watchlist**: `{watchlist_name}`\n")
        f.write(f"- **Backtest Period**: `{start_date}` to `{today_str()}`\n")
        f.write(f"- **Entry Criteria**:\n")
        f.write(f"  - `dv_shock {op_symbol} {dv_thresh}` (Institutional volume shock)\n")
        f.write(f"  - `prt < prt_buy_threshold` (Adaptive oversold limit)\n")
        f.write(f"- **Execution**: EOD-Lag model (Signal on $i$, buy on $i+1$ close, exit check from $i+2$ onwards)\n")
        f.write(f"- **Exit Mechanics**: SavgolCTS `exit_universal_cross` (Pure CTS Trailing + Near-Miss + PRT Cross exits)\n\n")
        
        f.write(f"## Performance Metrics\n\n")
        f.write(f"| Metric | Value |\n")
        f.write(f"| :--- | :--- |\n")
        f.write(f"| **Total Trades** | {total_trades} |\n")
        f.write(f"| **Symbols Traded** | {symbols_traded} / {len(symbols)} |\n")
        f.write(f"| **Winners / Losers** | {winners} / {losers} |\n")
        f.write(f"| **Win Rate** | {win_rate:.1f}% |\n")
        f.write(f"| **Avg P&L** | {avg_pnl:+.2f}% |\n")
        f.write(f"| **Median P&L** | {median_pnl:+.2f}% |\n")
        f.write(f"| **Avg Winner** | {avg_win:+.2f}% |\n")
        f.write(f"| **Avg Loser** | {avg_loss:+.2f}% |\n")
        f.write(f"| **Payoff Ratio** | {payoff:.2f}x |\n")
        f.write(f"| **Profit Factor** | {profit_factor:.2f} |\n")
        f.write(f"| **Expectancy** | {expectancy:+.2f}% |\n")
        f.write(f"| **Avg Duration** | {df_trades['duration'].mean():.1f} bars |\n\n")

        f.write(f"## Exit Breakdown\n\n")
        f.write(exit_agg.to_markdown(index=False))
        f.write(f"\n\n")

        f.write(f"## Trade Ledger\n\n")
        ledger_cols = [
            "symbol", "sig_date", "entry_date", "entry_price", "exit_date", "exit_price",
            "pnl_pct", "duration", "exit_reason", "dv_shock", "prt", "prt_bt"
        ]
        df_ledger = df_trades[ledger_cols].rename(columns={
            "symbol": "Symbol",
            "sig_date": "Signal Date",
            "entry_date": "Entry Date",
            "entry_price": "Entry Px",
            "exit_date": "Exit Date",
            "exit_price": "Exit Px",
            "pnl_pct": "PnL%",
            "duration": "Bars",
            "exit_reason": "Exit Reason",
            "dv_shock": "DV Shock",
            "prt": "PRT",
            "prt_bt": "PRT BT"
        })
        f.write(df_ledger.to_markdown(index=False))
        f.write(f"\n")

    print(f"Quantitative study report successfully saved to: {report_path}")

if __name__ == "__main__":
    main()
