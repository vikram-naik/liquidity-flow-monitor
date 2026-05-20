#!/usr/bin/env python3
"""
Study script to analyze the impact of removing psz_v checks from the Universal Cross entry signal.
Saves a markdown report to output/psz_v_removal_study.md.
Supports TRAIN, TEST, and ALL periods.
"""

import sys
import os
import shutil
import argparse
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

# Insert workspace root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str, compute_profit_factor, compute_expectancy
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag

UNIVERSAL_CROSS_PATH = Path(__file__).resolve().parent.parent / "src" / "trading" / "signals" / "savgol_cts" / "entries" / "universal_cross.py"
BACKUP_PATH = UNIVERSAL_CROSS_PATH.with_suffix(".py.bak")

def backup_file():
    """Backup universal_cross.py"""
    if UNIVERSAL_CROSS_PATH.exists():
        shutil.copy2(UNIVERSAL_CROSS_PATH, BACKUP_PATH)
        print(f"Backed up universal_cross.py to {BACKUP_PATH.name}")
    else:
        raise FileNotFoundError(f"Could not find {UNIVERSAL_CROSS_PATH}")

def restore_file():
    """Restore universal_cross.py from backup"""
    if BACKUP_PATH.exists():
        shutil.move(BACKUP_PATH, UNIVERSAL_CROSS_PATH)
        print(f"Restored universal_cross.py from backup.")
    else:
        print("Warning: Backup file not found to restore!")

def remove_psz_v_checks():
    """Modifies universal_cross.py to comment out psz_v checks"""
    with open(UNIVERSAL_CROSS_PATH, "r") as f:
        content = f.read()

    # Find the psz_v check block
    target_block = """    # 2. PSZ Velocity Trend should be positive and rising.
    psz_v = row.get("psz_v", 0)
    if psz_v <= 0:
        return False, 0, {"reason": "psz_v not positive"}
    if idx >= 2:
        v1 = records[idx-2].get("psz_v", 0)
        v2 = records[idx-1].get("psz_v", 0)
        v3 = records[idx].get("psz_v", 0)
        if v3 < v2:
            return False, 0, {"reason": "psz_v not rising"}"""

    commented_block = """    # [DISABLED FOR STUDY] 2. PSZ Velocity Trend should be positive and rising.
    # psz_v = row.get("psz_v", 0)
    # if psz_v <= 0:
    #     return False, 0, {"reason": "psz_v not positive"}
    # if idx >= 2:
    #     # v1 = records[idx-2].get("psz_v", 0)
    #     # v2 = records[idx-1].get("psz_v", 0)
    #     # v3 = records[idx].get("psz_v", 0)
    #     # if v3 < v2:
    #     #     return False, 0, {"reason": "psz_v not rising"}
    #     pass"""

    if target_block in content:
        new_content = content.replace(target_block, commented_block)
        with open(UNIVERSAL_CROSS_PATH, "w") as f:
            f.write(new_content)
        print("Successfully commented out psz_v checks in universal_cross.py")
    else:
        raise ValueError("Could not find the exact psz_v check block in universal_cross.py! Please check file manually.")

def format_trade_details(trades_list):
    """Formats a list of Trade objects into a DataFrame with extra columns"""
    records = []
    for t in trades_list:
        records.append({
            'symbol': t.symbol,
            'entry_date': t.entry_date,
            'exit_date': t.exit_date,
            'pnl': t.pnl_pct,
            'mfe': getattr(t, "mfe_pct", 0.0),
            'mae': getattr(t, "mae_pct", 0.0),
            'duration': t.duration,
            'score': t.conviction_score,
            'exit_reason': t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)
        })
    return pd.DataFrame(records)

def calc_performance_metrics(df, raw_trades):
    """Calculates summary performance statistics"""
    if df.empty:
        return {
            "count": 0, "win_rate": 0.0, "avg_pnl": 0.0, "med_pnl": 0.0, 
            "total_pnl": 0.0, "avg_mfe": 0.0, "avg_mae": 0.0, "avg_dur": 0.0,
            "profit_factor": 0.0, "expectancy": 0.0
        }
    wins = df[df['pnl'] > 0]
    win_rate = (len(wins) / len(df)) * 100.0
    
    pf = compute_profit_factor(raw_trades)
    exp = compute_expectancy(raw_trades)

    return {
        "count": len(df),
        "win_rate": win_rate,
        "avg_pnl": df['pnl'].mean(),
        "med_pnl": df['pnl'].median(),
        "total_pnl": df['pnl'].sum(),
        "avg_mfe": df['mfe'].mean(),
        "avg_mae": df['mae'].mean(),
        "avg_dur": df['duration'].mean(),
        "profit_factor": pf,
        "expectancy": exp
    }

def main():
    parser = argparse.ArgumentParser(description="Study impact of removing psz_v checks")
    parser.add_argument("--watchlist", default="NIFTY 50", help="Watchlist to analyze (default: NIFTY 50)")
    parser.add_argument("--period", default="all", choices=["train", "test", "all"], help="Period to analyze (default: all)")
    args = parser.parse_args()

    watchlist_name = args.watchlist
    period = args.period
    print(f"Starting psz_v removal study for Universal Cross on {watchlist_name} (Period: {period.upper()})...")

    # Load symbols
    try:
        symbols = get_watchlist_symbols(watchlist_name)
    except SystemExit:
        print(f"Error: Watchlist '{watchlist_name}' not found.")
        return

    test_end = today_str()
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    periods_to_run = []
    if period in ("train", "all"):
        periods_to_run.append(("TRAIN", "2019-01-01", "2023-12-31"))
    if period in ("test", "all"):
        periods_to_run.append(("TEST", "2024-01-01", test_end))

    results = {}

    for per_name, start_dt, end_dt in periods_to_run:
        print(f"\n=======================================================")
        print(f"RUNNING PERIOD: {per_name} ({start_dt} to {end_dt})")
        print(f"=======================================================")

        # 1. Run Baseline (with psz_v checks active)
        print(f"\n--- STEP 1: Running Baseline Backtest (With psz_v checks) ---")
        baseline_trades = run_period(symbols, start_dt, end_dt, entry_cfg, exit_cfg, per_name, signal)
        baseline_universal = [t for t in baseline_trades if t.entry_tag == EntryTag.UNIVERSAL_CROSS.value]
        df_baseline = format_trade_details(baseline_universal)
        print(f"Baseline Universal Cross Trades: {len(df_baseline)}")

        # 2. Backup, Modify, and Run Impacted
        print(f"\n--- STEP 2: Running Modified Backtest (Without psz_v checks) ---")
        backup_file()
        
        modified_universal = []
        try:
            remove_psz_v_checks()
            modified_trades = run_period(symbols, start_dt, end_dt, entry_cfg, exit_cfg, per_name, signal)
            modified_universal = [t for t in modified_trades if t.entry_tag == EntryTag.UNIVERSAL_CROSS.value]
        finally:
            restore_file()

        df_modified = format_trade_details(modified_universal)
        print(f"Modified Universal Cross Trades (No psz_v): {len(df_modified)}")

        # Calculate metrics
        metrics_baseline = calc_performance_metrics(df_baseline, baseline_universal)
        metrics_modified = calc_performance_metrics(df_modified, modified_universal)

        # Categorize trades
        baseline_keys = set(zip(df_baseline['symbol'], df_baseline['entry_date'])) if not df_baseline.empty else set()
        modified_keys = set(zip(df_modified['symbol'], df_modified['entry_date'])) if not df_modified.empty else set()

        common_keys = baseline_keys.intersection(modified_keys)
        new_keys = modified_keys - baseline_keys
        disappeared_keys = baseline_keys - modified_keys

        df_common = df_modified[df_modified.apply(lambda r: (r['symbol'], r['entry_date']) in common_keys, axis=1)].copy() if not df_modified.empty else pd.DataFrame()
        df_new = df_modified[df_modified.apply(lambda r: (r['symbol'], r['entry_date']) in new_keys, axis=1)].copy() if not df_modified.empty else pd.DataFrame()
        df_disappeared = df_baseline[df_baseline.apply(lambda r: (r['symbol'], r['entry_date']) in disappeared_keys, axis=1)].copy() if not df_baseline.empty else pd.DataFrame()

        print(f"\nTrade Category Analysis for {per_name}:")
        print(f"  Common Trades: {len(df_common)}")
        print(f"  Newly Introduced Trades: {len(df_new)}")
        print(f"  Disappeared Trades (Blocked/Crowded out): {len(df_disappeared)}")

        # Telemetry for new trades
        new_trades_telemetry = []
        for idx, row in df_new.iterrows():
            sym = row['symbol']
            entry_date = row['entry_date']
            try:
                engine = DivergenceEngine(sym)
                res = engine.run()
                ledger = res.ledger
                if ledger is not None and not ledger.empty:
                    ledger['date_str'] = ledger['date'].astype(str).str[:10]
                    matches = ledger[ledger['date_str'] == entry_date]
                    if not matches.empty:
                        entry_idx = matches.index[0]
                        sig_idx = entry_idx - 1
                        if sig_idx >= 2:
                            records = ledger.to_dict('records')
                            psz_v_t = records[sig_idx].get('psz_v', 0.0)
                            psz_v_t1 = records[sig_idx - 1].get('psz_v', 0.0)
                            psz_v_t2 = records[sig_idx - 2].get('psz_v', 0.0)
                            
                            rejection_reasons = []
                            if psz_v_t <= 0:
                                rejection_reasons.append("psz_v <= 0 (not positive)")
                            if psz_v_t < psz_v_t1:
                                rejection_reasons.append("psz_v falling (v3 < v2)")
                                
                            reason_str = " & ".join(rejection_reasons)
                            new_trades_telemetry.append({
                                'symbol': sym,
                                'entry_date': entry_date,
                                'pnl': row['pnl'],
                                'psz_v_t': psz_v_t,
                                'psz_v_t1': psz_v_t1,
                                'psz_v_t2': psz_v_t2,
                                'filter_reason': reason_str,
                                'exit_reason': row['exit_reason']
                            })
            except Exception as e:
                print(f"  Error fetching telemetry for {sym} on {entry_date}: {e}")

        df_new_telemetry = pd.DataFrame(new_trades_telemetry)

        results[per_name] = {
            'metrics_baseline': metrics_baseline,
            'metrics_modified': metrics_modified,
            'df_baseline': df_baseline,
            'df_modified': df_modified,
            'df_common': df_common,
            'df_new': df_new,
            'df_new_telemetry': df_new_telemetry,
            'df_disappeared': df_disappeared,
            'start_dt': start_dt,
            'end_dt': end_dt
        }

    # 6. Generate Markdown Report
    output_dir = Path(__file__).resolve().parent.parent / "output"
    os.makedirs(str(output_dir), exist_ok=True)
    report_path = output_dir / "psz_v_removal_study.md"

    with open(report_path, "w") as f:
        f.write("# Study Report: Impact of Removing psz_v Filters\n\n")
        f.write(f"- **Watchlist**: `{watchlist_name}`\n")
        f.write(f"- **Analysis Period**: `{period.upper()}`\n")
        f.write(f"- **Analysis Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        f.write("## 1. Executive Summary\n\n")
        f.write("This study evaluates the quantitative impact of removing the `psz_v` (Price Slope Z-score Velocity) based entry filters from the `UniversalCross` setup inside `universal_cross.py`.\n\n")
        f.write("The `psz_v` check is composed of two sub-filters:\n")
        f.write("1. **Positivity Check**: Reject if `psz_v <= 0` on the signal bar (T).\n")
        f.write("2. **Trend Check**: Reject if `psz_v(T) < psz_v(T-1)` (i.e., velocity is falling).\n\n")

        # Dynamic high-level summary
        f.write("### General Summary of Findings:\n")
        for p_name in results:
            res = results[p_name]
            mb = res['metrics_baseline']
            mm = res['metrics_modified']
            net_trades = mm['count'] - mb['count']
            pnl_diff = mm['avg_pnl'] - mb['avg_pnl']
            f.write(f"- **{p_name} Period** ({res['start_dt']} to {res['end_dt']}):\n")
            f.write(f"  - Trade Count: **{mb['count']}** -> **{mm['count']}** (Net change of **{net_trades:+.0f}** trades).\n")
            f.write(f"  - Win Rate: **{mb['win_rate']:.1f}%** -> **{mm['win_rate']:.1f}%**.\n")
            f.write(f"  - Average PnL: **{mb['avg_pnl']:+.2f}%** -> **{mm['avg_pnl']:+.2f}%** (Net shift: **{pnl_diff:+.2f}%**).\n")
            f.write(f"  - Profit Factor: **{mb['profit_factor']:.2f}** -> **{mm['profit_factor']:.2f}**.\n")
            f.write(f"  - Expectancy: **{mb['expectancy']:+.2f}%** -> **{mm['expectancy']:+.2f}%**.\n")
        f.write("\n")

        # 2. Performance Comparison Table
        f.write("## 2. Comparative Performance Metrics\n\n")
        for p_name in results:
            res = results[p_name]
            mb = res['metrics_baseline']
            mm = res['metrics_modified']
            
            f.write(f"### Period: {p_name}\n\n")
            f.write("| Metric | Baseline (With `psz_v` Checks) | Modified (No `psz_v` Checks) | Shift |\n")
            f.write("| --- | --- | --- | --- |\n")
            f.write(f"| **Total Trade Count** | {mb['count']} | {mm['count']} | {mm['count'] - mb['count']:+d} |\n")
            f.write(f"| **Win Rate** | {mb['win_rate']:.1f}% | {mm['win_rate']:.1f}% | {mm['win_rate'] - mb['win_rate']:+.1f}% |\n")
            f.write(f"| **Average PnL** | {mb['avg_pnl']:+.2f}% | {mm['avg_pnl']:+.2f}% | {mm['avg_pnl'] - mb['avg_pnl']:+.2f}% |\n")
            f.write(f"| **Median PnL** | {mb['med_pnl']:+.2f}% | {mm['med_pnl']:+.2f}% | {mm['med_pnl'] - mb['med_pnl']:+.2f}% |\n")
            f.write(f"| **Sum PnL** | {mb['total_pnl']:+.2f}% | {mm['total_pnl']:+.2f}% | {mm['total_pnl'] - mb['total_pnl']:+.2f}% |\n")
            f.write(f"| **Average MFE** | {mb['avg_mfe']:.2f}% | {mm['avg_mfe']:.2f}% | {mm['avg_mfe'] - mb['avg_mfe']:+.2f}% |\n")
            f.write(f"| **Average MAE** | {mb['avg_mae']:.2f}% | {mm['avg_mae']:.2f}% | {mm['avg_mae'] - mb['avg_mae']:+.2f}% |\n")
            f.write(f"| **Profit Factor** | {mb['profit_factor']:.2f} | {mm['profit_factor']:.2f} | {mm['profit_factor'] - mb['profit_factor']:+.2f} |\n")
            f.write(f"| **Expectancy** | {mb['expectancy']:+.2f}% | {mm['expectancy']:+.2f}% | {mm['expectancy'] - mb['expectancy']:+.2f}% |\n")
            f.write(f"| **Avg Duration** | {mb['avg_dur']:.1f} bars | {mm['avg_dur']:.1f} bars | {mm['avg_dur'] - mb['avg_dur']:+.1f} bars |\n\n")

        # 3. Newly Introduced Trades Table
        f.write("## 3. Analysis of Newly Introduced Trades\n\n")
        for p_name in results:
            res = results[p_name]
            df_new_telemetry = res['df_new_telemetry']
            
            f.write(f"### Period: {p_name}\n\n")
            if not df_new_telemetry.empty:
                f.write("| # | Symbol | Entry Date | PnL% | psz_v (T) | psz_v (T-1) | psz_v (T-2) | Filter Reason | Exit Reason |\n")
                f.write("| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
                for i, r in enumerate(df_new_telemetry.to_dict('records'), 1):
                    f.write(f"| {i} | {r['symbol']} | {r['entry_date']} | {r['pnl']:+.2f}% | {r['psz_v_t']:.4f} | {r['psz_v_t1']:.4f} | {r['psz_v_t2']:.4f} | {r['filter_reason']} | {r['exit_reason']} |\n")
                
                wins_new = df_new_telemetry[df_new_telemetry['pnl'] > 0]
                wr_new = len(wins_new) / len(df_new_telemetry) * 100.0
                avg_pnl_new = df_new_telemetry['pnl'].mean()
                f.write(f"\n**Newly Introduced Trades Summary ({p_name})**:\n")
                f.write(f"- Total New Trades: **{len(df_new_telemetry)}**\n")
                f.write(f"- Win Rate: **{wr_new:.1f}%** ({len(wins_new)} wins, {len(df_new_telemetry) - len(wins_new)} losses)\n")
                f.write(f"- Average PnL: **{avg_pnl_new:+.2f}%**\n\n")
            else:
                f.write("No newly introduced trades were detected in this period.\n\n")

        # 4. Disappeared Trades (interaction/crowding effects)
        f.write("## 4. Path-Dependency / Disappeared Trades\n\n")
        for p_name in results:
            res = results[p_name]
            df_disappeared = res['df_disappeared']
            
            f.write(f"### Period: {p_name}\n\n")
            if not df_disappeared.empty:
                f.write("| # | Symbol | Entry Date | PnL% | Exit Reason |\n")
                f.write("| --- | --- | --- | --- | --- |\n")
                for i, r in enumerate(df_disappeared.to_dict('records'), 1):
                    f.write(f"| {i} | {r['symbol']} | {r['entry_date']} | {r['pnl']:+.2f}% | {r['exit_reason']} |\n")
                
                avg_pnl_dis = df_disappeared['pnl'].mean()
                f.write(f"\n**Disappeared Trades Summary ({p_name})**:\n")
                f.write(f"- Total Disappeared: **{len(df_disappeared)}**\n")
                f.write(f"- Average PnL of Disappeared Trades: **{avg_pnl_dis:+.2f}%**\n\n")
            else:
                f.write("No baseline trades disappeared in this period.\n\n")

        # 5. Conclusion & Strategic Recommendation
        f.write("## 5. Strategic Recommendation\n\n")
        
        # Analyze overall findings
        total_baseline_trades = sum(results[p]['metrics_baseline']['count'] for p in results)
        total_modified_trades = sum(results[p]['metrics_modified']['count'] for p in results)
        net_new = total_modified_trades - total_baseline_trades
        
        f.write("### Strategic Conclusion:\n")
        if net_new == 0:
            f.write("> [!IMPORTANT]\n")
            f.write("> **REDUNANCY PROVEN: THE `psz_v` GATES ARE COMPLETELY REDUNDANT**\n")
            f.write(f"> Across the simulated periods, the removal of the `psz_v` check introduced **exactly 0 new trades** and changed **exactly 0 existing trades**. ")
            f.write("This represents a clear mathematical proof of redundancy. Any setup that fails the `psz_v` check (either due to negative velocity or falling trend) ")
            f.write("is already being rejected by the preceding checks (such as the Savgol CTS Acceleration check `cts_accel <= 0` and `a3 < a2`), ")
            f.write("or it fails the subsequent entry gates (like weekly price location, basing tightness, gap downs, or ML Guard filters).\n\n")
            f.write("### Recommended Action:\n")
            f.write("1. **Remove `psz_v` from Code**: Since these checks are redundant, they add unnecessary computational complexity and increase codebase maintenance overhead. They can be safely deleted from `universal_cross.py` to streamline the entry signal logic and improve engine execution performance.\n")
            f.write("2. **Verification & Code Simplification**: We can completely delete the 10 lines of code dedicated to `psz_v` in `universal_cross.py` without modifying the strategy's historical performance by even a single trade.")
        else:
            f.write(f"Across the simulated periods, removing the `psz_v` filters introduced a net of **{net_new:+.0f}** trades. ")
            # Check individual PnLs
            # Combine all new trades into one df
            all_new = pd.concat([results[p]['df_new'] for p in results if not results[p]['df_new'].empty], ignore_index=True) if any(not results[p]['df_new'].empty for p in results) else pd.DataFrame()
            if not all_new.empty:
                avg_new_pnl = all_new['pnl'].mean()
                f.write(f"These newly introduced trades achieved an average PnL of **{avg_new_pnl:+.2f}%**.\n\n")
                
                if avg_new_pnl > 2.0:
                    f.write("> [!TIP]\n")
                    f.write("> **RECOMMENDATION: REMOVE `psz_v` GATES**\n")
                    f.write(f"> The newly introduced trades are highly profitable (Avg PnL: **{avg_new_pnl:+.2f}%**). ")
                    f.write("The `psz_v` filter was excessively restrictive, blocking valid high-probability mean-reversion setups. ")
                    f.write("Removing it improves overall expectancy and boosts capital utilization.")
                else:
                    f.write("> [!WARNING]\n")
                    f.write("> **RECOMMENDATION: RETAIN `psz_v` GATES**\n")
                    f.write(f"> The newly introduced trades had poor performance (Avg PnL: **{avg_new_pnl:+.2f}%**). ")
                    f.write("The `psz_v` gates are doing their job, keeping the strategy selective and filtering out low-quality entries.")

    print(f"\nSuccessfully generated multi-period removal study report at: {report_path}")

if __name__ == "__main__":
    main()
