"""Optimize near-miss ST_CROSS exits by simulating different parameters.

Tests the impact of adding a CTS near-miss rollover exit across NIFTY 50 trades.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag, ExitReason

# The 18 near-miss trades we want to track closely
TARGET_KEYS = [
    ("TECHM", "2026-03-09"),
    ("DRREDDY", "2025-04-11"),
    ("MARUTI", "2022-05-17"),
    ("CIPLA", "2022-05-18"),
    ("SBILIFE", "2026-05-06"),
    ("BHARTIARTL", "2026-03-06"),
    ("ADANIENT", "2023-03-08"),
    ("HINDALCO", "2019-02-22"),
    ("WIPRO", "2023-06-29"),
    ("ONGC", "2023-02-09"),
    ("CIPLA", "2025-04-09"),
    ("MAXHEALTH", "2026-01-12"),
    ("ADANIENT", "2020-06-24"),
    ("HDFCBANK", "2024-05-20"),
    ("ULTRACEMCO", "2022-10-03"),
    ("KOTAKBANK", "2023-11-03"),
    ("RELIANCE", "2019-09-12"),
    ("LT", "2020-09-22"),
]


def simulate_trades_with_near_miss_exit(
    ticker: str, df: pd.DataFrame,
    entry_cfg, exit_cfg, signal,
    near_miss_gap: float,
    exit_cts_level: float,
) -> list[Trade]:
    records = df.to_dict("records")
    n = len(records)
    trades = []
    in_trade = False
    trade = None
    peak_close = 0.0
    delivery_bad_count = 0
    cwvap_values: list[float] = []
    pending_signal: dict | None = None
    pending_exit_reason = None
    
    # State tracking for near-miss
    cts_near_miss_active = False

    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue

        cw = row.get("cwvap", np.nan)
        cwvap_values.append(cw)

        # Execute pending exit at today's open (EOD-lag)
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
            delivery_bad_count = 0
            pending_exit_reason = None
            cts_near_miss_active = False
            continue

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

            # Base signal exit check
            reason, delivery_bad_count = signal.check_exit(
                row, prev, trade, peak_close, bars_held,
                delivery_bad_count, cwvap_values, exit_cfg,
                records, i,
            )

            # Custom Near-Miss logic
            cts = row.get("cts", np.nan)
            st = row.get("cts_sell_threshold", np.nan)
            
            if not np.isnan(cts) and not np.isnan(st):
                # 1. Detect Near Miss
                if cts < st and (st - cts) <= near_miss_gap:
                    cts_near_miss_active = True
                
                # 2. Trigger Rollover Exit
                # If we had a near-miss, and now CTS rolls over below the target level
                if cts_near_miss_active and cts < exit_cts_level:
                    # Make sure we don't override standard bypass reasons (Hard Stop, Gap Down)
                    if reason in [ExitReason.HARD_STOP, ExitReason.GAP_DOWN_LOSS, ExitReason.PNL_CAP]:
                        pass
                    else:
                        reason = "CTS_NEAR_MISS_ROLLOVER"

            if reason:
                pending_exit_reason = reason

        elif pending_signal is not None:
            sig = pending_signal
            pending_signal = None
            atr = row.get("atr_20", 0)
            if atr <= 0 or np.isnan(atr):
                continue
            psz_now = row.get("price_slope_z", np.nan)
            trade = Trade(
                symbol=ticker,
                entry_date=str(row.get("date", ""))[:10],
                entry_price=close,
                entry_idx=i,
                atr_at_entry=atr,
                conviction_score=sig.get("details", {}).get("score", 0),
                regime_at_entry=sig.get("details", {}).get("regime", "-"),
                entry_tag=sig.get("details", {}).get("entry_tag", ""),
                psz_at_entry=psz_now if not np.isnan(psz_now) else 0.0,
                psz_peak=psz_now if not np.isnan(psz_now) else 0.0,
            )
            peak_close = close
            delivery_bad_count = 0
            in_trade = True
            cts_near_miss_active = False

        else:
            qualifies, soft_count, fdetails = signal.check_entry(row, prev, entry_cfg, records, i)
            if qualifies:
                pending_signal = {"soft_count": soft_count, "details": fdetails}

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


def run_parameter_sweep(symbols, entry_cfg, exit_cfg, signal_cls, ledgers):
    # Parameter combinations to test
    gaps = [0.05, 0.08, 0.10]
    levels = [0.50, 0.30, 0.00]
    
    results = []
    
    for gap in gaps:
        for lvl in levels:
            print(f"Testing Near-Miss Gap={gap:.2f}, Exit Level={lvl:.2f}...", flush=True)
            
            simulated_trades = {}
            for sym in symbols:
                signal = signal_cls("savgol_cts")
                trades = simulate_trades_with_near_miss_exit(
                    sym, ledgers[sym], entry_cfg, exit_cfg, signal,
                    near_miss_gap=gap,
                    exit_cts_level=lvl
                )
                
                # Extract Universal trades
                u_trades = [t for t in trades if t.entry_tag == EntryTag.UNIVERSAL_CROSS.value]
                for t in u_trades:
                    simulated_trades[(t.symbol, t.entry_date)] = t
            
            # Evaluate target trades
            total_pnl = 0.0
            improved_count = 0
            worsened_count = 0
            unchanged_count = 0
            
            details = []
            
            for sym, entry_dt in TARGET_KEYS:
                # Find matching simulated trade
                t = simulated_trades.get((sym, entry_dt))
                if t:
                    total_pnl += t.pnl_pct
                    details.append(t)
                else:
                    details.append(None)
                    
            results.append({
                "gap": gap,
                "level": lvl,
                "total_pnl": total_pnl,
                "trades": details,
            })
            
    return results


def main():
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal_cls = SignalFactory.get_signal

    symbols = get_watchlist_symbols("NIFTY 50")
    
    print("Loading symbol ledgers into memory...", flush=True)
    ledgers = {}
    for sym in symbols:
        engine = DivergenceEngine(sym)
        res = engine.run()
        ledgers[sym] = res.ledger

    # Baseline: No near-miss exit
    print("\nRunning Baseline simulation...", flush=True)
    baseline_trades = {}
    for sym in symbols:
        signal = signal_cls("savgol_cts")
        trades = simulate_trades_with_near_miss_exit(
            sym, ledgers[sym], entry_cfg, exit_cfg, signal,
            near_miss_gap=-1.0,  # disabled
            exit_cts_level=-99.0
        )
        u_trades = [t for t in trades if t.entry_tag == EntryTag.UNIVERSAL_CROSS.value]
        for t in u_trades:
            baseline_trades[(t.symbol, t.entry_date)] = t
            
    # Calculate baseline stats for target trades
    baseline_total_pnl = 0.0
    print("\nBaseline details for 18 Near-Miss Setups:")
    for sym, entry_dt in TARGET_KEYS:
        t = baseline_trades.get((sym, entry_dt))
        if t:
            baseline_total_pnl += t.pnl_pct
            print(f"  {sym:<12} | Entry: {entry_dt} | Exit: {t.exit_date} | PnL: {t.pnl_pct:>+7.2f}% | Reason: {t.exit_reason}")
    print(f"TOTAL BASELINE PNL: {baseline_total_pnl:.2f}%")

    # Run parameter sweep
    print("\n" + "="*80)
    print("PARAMETER SWEEP")
    print("="*80)
    sweep = run_parameter_sweep(symbols, entry_cfg, exit_cfg, signal_cls, ledgers)
    
    # Sort sweep results by total PnL descending
    sweep.sort(key=lambda x: x["total_pnl"], reverse=True)
    
    print("\nSweep Results:")
    print(f"{'Gap':>5} | {'ExitLvl':>7} | {'Total PnL%':>12} | {'Change vs Base':>15}")
    print("-" * 50)
    for s in sweep:
        diff = s["total_pnl"] - baseline_total_pnl
        print(f"{s['gap']:>5.2f} | {s['level']:>7.2f} | {s['total_pnl']:>11.2f}% | {diff:>+14.2f}%")
        
    # Print detailed comparison for the BEST parameter set
    best = sweep[0]
    print(f"\n{'='*120}")
    print(f"DETAILED COMPARISON: BASELINE vs BEST PARAMETERS (Gap={best['gap']:.2f}, ExitLvl={best['level']:.2f})")
    print(f"{'='*120}")
    
    header = (
        f"{'Symbol':<12} | {'Entry':<10} | "
        f"{'BASE Exit':<10} | {'BASE PnL':>8} | {'BASE Reason':<30} || "
        f"{'NEW Exit':<10} | {'NEW PnL':>8} | {'NEW Reason':<30} | {'Δ PnL':>8}"
    )
    print(header)
    print("-" * len(header))
    
    for i, (sym, entry_dt) in enumerate(TARGET_KEYS):
        base = baseline_trades.get((sym, entry_dt))
        new = best["trades"][i]
        
        if base and new:
            base_reason = base.exit_reason.value if hasattr(base.exit_reason, "value") else str(base.exit_reason)
            new_reason = new.exit_reason.value if hasattr(new.exit_reason, "value") else str(new.exit_reason)
            delta = new.pnl_pct - base.pnl_pct
            
            print(
                f"{sym:<12} | {entry_dt:<10} | "
                f"{base.exit_date:<10} | {base.pnl_pct:>+8.2f} | {base_reason:<30} || "
                f"{new.exit_date:<10} | {new.pnl_pct:>+8.2f} | {new_reason:<30} | {delta:>+8.2f}"
            )
            
    diff = best["total_pnl"] - baseline_total_pnl
    print("-" * len(header))
    print(f"{'TOTAL':<12} | {'':10} | {'':10} | {baseline_total_pnl:>+8.2f} | {'':30} || "
          f"{'':10} | {best['total_pnl']:>+8.2f} | {'':30} | {diff:>+8.2f}")


if __name__ == "__main__":
    main()
