#!/usr/bin/env python3
"""
Study on the effect of disabling PRT ST cross exit in Universal Cross exit path.
Compares Baseline (prt_st_cross_enabled = True) vs Experiment (prt_st_cross_enabled = False).
"""

import argparse
import sys
from pathlib import Path
import io
from datetime import datetime
import pandas as pd
import numpy as np
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str, compute_profit_factor, compute_expectancy
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals import SignalFactory

def summarize_metrics(trades: list) -> dict:
    if not trades:
        return {}
        
    df = pd.DataFrame([{
        "symbol": t.symbol,
        "pnl": t.pnl_pct, "mfe": t.mfe_pct, "mae": t.mae_pct,
        "bars": t.duration,
        "reason": t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason),
    } for t in trades])
    
    total = len(df)
    winners = (df["pnl"] > 0).sum()
    losers = total - winners
    win_rate = winners / total * 100 if total > 0 else 0.0
    avg_pnl = df["pnl"].mean()
    median_pnl = df["pnl"].median()
    avg_win = df.loc[df["pnl"] > 0, "pnl"].mean() if winners > 0 else 0
    avg_loss = df.loc[df["pnl"] <= 0, "pnl"].mean() if losers > 0 else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
    profit_factor = compute_profit_factor(trades)
    expectancy = compute_expectancy(trades)
    avg_mfe = df["mfe"].mean()
    avg_mae = df["mae"].mean()
    avg_dur = df["bars"].mean()
    
    # Also get exit reason breakdown
    exits = df.groupby("reason").size().to_dict()
    
    return {
        "trades": total,
        "winners": winners,
        "losers": losers,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "median_pnl": median_pnl,
        "payoff": payoff,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "avg_mfe": avg_mfe,
        "avg_mae": avg_mae,
        "avg_dur": avg_dur,
        "exits": exits
    }

def main():
    parser = argparse.ArgumentParser(description="Study PRT ST Cross Exit")
    parser.add_argument("--watchlist", default="NIFTY 50", help="Watchlist to run study on")
    args = parser.parse_args()
    
    symbols = get_watchlist_symbols(args.watchlist)
    test_end = today_str()
    
    TRAIN_START = "2019-01-01"
    TRAIN_END = "2023-12-31"
    TEST_START = "2024-01-01"
    
    print(f"Watchlist: {args.watchlist} ({len(symbols)} symbols)")
    print(f"Train period: {TRAIN_START} to {TRAIN_END}")
    print(f"Test period: {TEST_START} to {test_end}")
    
    signal = SignalFactory.get_signal("savgol_cts")
    
    # ------------------ Run Baseline ------------------
    print("\n>>> Running Baseline (PRT ST Cross Enabled)...")
    entry_cfg_base = SavgolCTSEntryConfig()
    exit_cfg_base = SavgolCTSExitConfig()
    exit_cfg_base.universal_cross.prt_st_cross_enabled = True
    
    train_trades_base = run_period(symbols, TRAIN_START, TRAIN_END, entry_cfg_base, exit_cfg_base, "TRAIN_BASE", signal)
    test_trades_base = run_period(symbols, TEST_START, test_end, entry_cfg_base, exit_cfg_base, "TEST_BASE", signal)
    
    metrics_train_base = summarize_metrics(train_trades_base)
    metrics_test_base = summarize_metrics(test_trades_base)
    
    # ------------------ Run Experiment ------------------
    print("\n>>> Running Experiment (PRT ST Cross Disabled)...")
    entry_cfg_exp = SavgolCTSEntryConfig()
    exit_cfg_exp = SavgolCTSExitConfig()
    exit_cfg_exp.universal_cross.prt_st_cross_enabled = False
    
    train_trades_exp = run_period(symbols, TRAIN_START, TRAIN_END, entry_cfg_exp, exit_cfg_exp, "TRAIN_EXP", signal)
    test_trades_exp = run_period(symbols, TEST_START, test_end, entry_cfg_exp, exit_cfg_exp, "TEST_EXP", signal)
    
    metrics_train_exp = summarize_metrics(train_trades_exp)
    metrics_test_exp = summarize_metrics(test_trades_exp)
    
    # ------------------ Generate Report ------------------
    out = io.StringIO()
    def w(line: str = ""):
        out.write(line + "\n")
        
    w("# Study Report: Disabling PRT ST Cross Exit")
    w(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w(f"**Watchlist:** {args.watchlist} ({len(symbols)} symbols)")
    w()
    
    w("## Overview")
    w("This study investigates the impact of disabling the `PRT_ST_CROSS` exit condition in the `universal_cross` exit path.")
    w("The `PRT_ST_CROSS` exit is triggered when the smoothed price range trend (PRT) crosses below its sell threshold (ST), while the primary trend (CTS) is not above its own sell threshold.")
    w("We run a walk-forward backtest comparing the Baseline (enabled) to the Experiment (disabled).")
    w()
    
    w("## Train Period Comparison (2019-01-01 to 2023-12-31)")
    w()
    
    headers = ["Metric", "Baseline (Enabled)", "Experiment (Disabled)", "Delta"]
    
    def get_compare_row(name, key, suffix="", dec=2):
        val_base = metrics_train_base.get(key, 0)
        val_exp = metrics_train_exp.get(key, 0)
        delta = val_exp - val_base
        fmt = f".{dec}f"
        return [
            name,
            f"{val_base:{fmt}}{suffix}",
            f"{val_exp:{fmt}}{suffix}",
            f"{delta:+{fmt}}{suffix}"
        ]
        
    train_rows = [
        get_compare_row("Trades", "trades", "", 0),
        get_compare_row("Win Rate", "win_rate", "%", 1),
        get_compare_row("Avg PnL", "avg_pnl", "%", 2),
        get_compare_row("Median PnL", "median_pnl", "%", 2),
        get_compare_row("Payoff Ratio", "payoff", "x", 2),
        get_compare_row("Profit Factor", "profit_factor", "", 2),
        get_compare_row("Expectancy", "expectancy", "%", 2),
        get_compare_row("Avg MFE", "avg_mfe", "%", 2),
        get_compare_row("Avg MAE", "avg_mae", "%", 2),
        get_compare_row("Avg Duration", "avg_dur", " bars", 1),
    ]
    
    w(tabulate(train_rows, headers=headers, tablefmt="github"))
    w()
    
    w("## Test Period Comparison (2024-01-01 to present)")
    w()
    
    test_rows = [
        ["Trades", f"{metrics_test_base.get('trades', 0):.0f}", f"{metrics_test_exp.get('trades', 0):.0f}", f"{metrics_test_exp.get('trades', 0) - metrics_test_base.get('trades', 0):+.0f}"],
        ["Win Rate", f"{metrics_test_base.get('win_rate', 0):.1f}%", f"{metrics_test_exp.get('win_rate', 0):.1f}%", f"{metrics_test_exp.get('win_rate', 0) - metrics_test_base.get('win_rate', 0):+.1f}%"],
        ["Avg PnL", f"{metrics_test_base.get('avg_pnl', 0):+.2f}%", f"{metrics_test_exp.get('avg_pnl', 0):+.2f}%", f"{metrics_test_exp.get('avg_pnl', 0) - metrics_test_base.get('avg_pnl', 0):+.2f}%"],
        ["Median PnL", f"{metrics_test_base.get('median_pnl', 0):+.2f}%", f"{metrics_test_exp.get('median_pnl', 0):+.2f}%", f"{metrics_test_exp.get('median_pnl', 0) - metrics_test_base.get('median_pnl', 0):+.2f}%"],
        ["Payoff Ratio", f"{metrics_test_base.get('payoff', 0):.2f}x", f"{metrics_test_exp.get('payoff', 0):.2f}x", f"{metrics_test_exp.get('payoff', 0) - metrics_test_base.get('payoff', 0):+.2f}x"],
        ["Profit Factor", f"{metrics_test_base.get('profit_factor', 0):.2f}", f"{metrics_test_exp.get('profit_factor', 0):.2f}", f"{metrics_test_exp.get('profit_factor', 0) - metrics_test_base.get('profit_factor', 0):+.2f}"],
        ["Expectancy", f"{metrics_test_base.get('expectancy', 0):+.2f}%", f"{metrics_test_exp.get('expectancy', 0):+.2f}%", f"{metrics_test_exp.get('expectancy', 0) - metrics_test_base.get('expectancy', 0):+.2f}%"],
        ["Avg MFE", f"{metrics_test_base.get('avg_mfe', 0):+.2f}%", f"{metrics_test_exp.get('avg_mfe', 0):+.2f}%", f"{metrics_test_exp.get('avg_mfe', 0) - metrics_test_base.get('avg_mfe', 0):+.2f}%"],
        ["Avg MAE", f"{metrics_test_base.get('avg_mae', 0):+.2f}%", f"{metrics_test_exp.get('avg_mae', 0):+.2f}%", f"{metrics_test_exp.get('avg_mae', 0) - metrics_test_base.get('avg_mae', 0):+.2f}%"],
        ["Avg Duration", f"{metrics_test_base.get('avg_dur', 0):.1f} bars", f"{metrics_test_exp.get('avg_dur', 0):.1f} bars", f"{metrics_test_exp.get('avg_dur', 0) - metrics_test_base.get('avg_dur', 0):+.1f} bars"],
    ]
    
    w(tabulate(test_rows, headers=headers, tablefmt="github"))
    w()
    
    w("## Exit Reason Breakdown Comparison")
    w()
    
    w("### Train Exits")
    all_reasons_train = sorted(list(set(metrics_train_base["exits"].keys()) | set(metrics_train_exp["exits"].keys())))
    exit_train_rows = []
    for r in all_reasons_train:
        b_cnt = metrics_train_base["exits"].get(r, 0)
        e_cnt = metrics_train_exp["exits"].get(r, 0)
        exit_train_rows.append([r, b_cnt, e_cnt, e_cnt - b_cnt])
    w(tabulate(exit_train_rows, headers=["Exit Reason", "Baseline Count", "Experiment Count", "Delta"], tablefmt="github"))
    w()
    
    w("### Test Exits")
    all_reasons_test = sorted(list(set(metrics_test_base["exits"].keys()) | set(metrics_test_exp["exits"].keys())))
    exit_test_rows = []
    for r in all_reasons_test:
        b_cnt = metrics_test_base["exits"].get(r, 0)
        e_cnt = metrics_test_exp["exits"].get(r, 0)
        exit_test_rows.append([r, b_cnt, e_cnt, e_cnt - b_cnt])
    w(tabulate(exit_test_rows, headers=["Exit Reason", "Baseline Count", "Experiment Count", "Delta"], tablefmt="github"))
    w()
    
    report_content = out.getvalue()
    print(report_content)
    
    output_dir = Path(__file__).resolve().parent.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_file = output_dir / "prt_st_cross_study.md"
    report_file.write_text(report_content)
    print(f"\nReport written to {report_file}")

if __name__ == "__main__":
    main()
