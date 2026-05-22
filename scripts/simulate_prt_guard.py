"""Simulate suppressing ST_CROSS exit when PRT is below prt_buy_threshold.

For the 6 known trades where ST_CROSS fired while PRT was in buy territory,
re-run the simulation with a modified exit that suppresses ST_CROSS in that
condition, letting the trade trail further until the next exit signal fires.

Reports before/after comparison.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag, ExitReason


# The 5 trades where ST_CROSS fired while PRT was BELOW prt_buy_threshold on signal day
TARGET_TRADES = [
    {"symbol": "ITC",        "entry_date": "2019-06-12"},
    {"symbol": "HINDUNILVR", "entry_date": "2021-11-09"},
    {"symbol": "ADANIENT",   "entry_date": "2023-02-09"},
    {"symbol": "ADANIENT",   "entry_date": "2025-08-18"},
    {"symbol": "HCLTECH",    "entry_date": "2026-03-10"},
]


def simulate_trades_with_prt_guard(
    ticker: str, df: pd.DataFrame,
    entry_cfg, exit_cfg, signal,
    suppress_st_cross_below_bt: bool = False,
) -> list[Trade]:
    """Walk through ledger bar-by-bar. Optionally suppress ST_CROSS when PRT < BT."""
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
    suppression_log: list[dict] = []

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
            # Attach suppression log if any
            trade._suppression_log = suppression_log.copy()
            trades.append(trade)
            in_trade = False
            trade = None
            delivery_bad_count = 0
            pending_exit_reason = None
            suppression_log = []
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

            reason, delivery_bad_count = signal.check_exit(
                row, prev, trade, peak_close, bars_held,
                delivery_bad_count, cwvap_values, exit_cfg,
                records, i,
            )

            if reason:
                # Check if we should suppress ST_CROSS when PRT < BT
                reason_str = str(reason)
                is_st_cross = "ST_CROSS" in reason_str and "PRT" not in reason_str

                if suppress_st_cross_below_bt and is_st_cross:
                    prt = row.get("prt", np.nan)
                    prt_bt = row.get("prt_buy_threshold", np.nan)
                    if not np.isnan(prt) and not np.isnan(prt_bt) and prt <= prt_bt:
                        # SUPPRESS: PRT is in buy territory, don't sell
                        pnl_now = (close / trade.entry_price - 1) * 100
                        suppression_log.append({
                            "date": str(row.get("date", ""))[:10],
                            "reason": reason_str,
                            "prt": prt,
                            "prt_bt": prt_bt,
                            "pnl_at_suppress": round(pnl_now, 2),
                            "bar": bars_held,
                        })
                        continue  # Suppress and trail

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
            suppression_log = []
            in_trade = True

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
        trade._suppression_log = suppression_log.copy()
        trades.append(trade)

    return trades


def find_matching_trade(trades, entry_date):
    """Find the trade matching the target entry date."""
    for t in trades:
        if t.entry_date == entry_date:
            return t
    return None


def main():
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal_cls = SignalFactory.get_signal

    # Get unique symbols
    symbols = list(dict.fromkeys(t["symbol"] for t in TARGET_TRADES))

    print("=" * 120)
    print("PHASE 1: Current ST_CROSS Exits (baseline)")
    print("=" * 120)

    baseline_trades = {}
    for sym in symbols:
        print(f"\n  Processing {sym}...")
        engine = DivergenceEngine(sym)
        result = engine.run()
        signal = signal_cls("savgol_cts")

        trades = simulate_trades_with_prt_guard(
            sym, result.ledger, entry_cfg, exit_cfg, signal,
            suppress_st_cross_below_bt=False,
        )
        for target in TARGET_TRADES:
            if target["symbol"] == sym:
                t = find_matching_trade(trades, target["entry_date"])
                if t:
                    baseline_trades[(sym, target["entry_date"])] = t
                    print(f"    Found: {sym} {target['entry_date']} -> Exit: {t.exit_date} | "
                          f"PnL: {t.pnl_pct:+.2f}% | MFE: {t.mfe_pct:.2f}% | MAE: {t.mae_pct:.2f}% | "
                          f"Bars: {t.duration} | Reason: {t.exit_reason}")
                else:
                    print(f"    NOT FOUND: {sym} {target['entry_date']}")

    print(f"\n\n{'=' * 120}")
    print("PHASE 2: Simulated with PRT Guard (suppress ST_CROSS when PRT < BT)")
    print("=" * 120)

    guarded_trades = {}
    for sym in symbols:
        print(f"\n  Processing {sym} (with PRT guard)...")
        engine = DivergenceEngine(sym)
        result = engine.run()
        signal = signal_cls("savgol_cts")

        trades = simulate_trades_with_prt_guard(
            sym, result.ledger, entry_cfg, exit_cfg, signal,
            suppress_st_cross_below_bt=True,
        )
        for target in TARGET_TRADES:
            if target["symbol"] == sym:
                t = find_matching_trade(trades, target["entry_date"])
                if t:
                    guarded_trades[(sym, target["entry_date"])] = t
                    reason_str = str(t.exit_reason)
                    sup_log = getattr(t, "_suppression_log", [])
                    print(f"    Found: {sym} {target['entry_date']} -> Exit: {t.exit_date} | "
                          f"PnL: {t.pnl_pct:+.2f}% | MFE: {t.mfe_pct:.2f}% | MAE: {t.mae_pct:.2f}% | "
                          f"Bars: {t.duration} | Reason: {reason_str}")
                    if sup_log:
                        print(f"      Suppressions ({len(sup_log)}):")
                        for s in sup_log:
                            print(f"        {s['date']}: Suppressed {s['reason']} | "
                                  f"PRT={s['prt']:.4f} vs BT={s['prt_bt']:.4f} | "
                                  f"PnL@suppress: {s['pnl_at_suppress']:+.2f}%")
                else:
                    print(f"    NOT FOUND: {sym} {target['entry_date']}")

    # Comparison table
    print(f"\n\n{'=' * 120}")
    print("COMPARISON: Baseline vs PRT Guard")
    print("=" * 120)

    header = (
        f"{'Symbol':<12} | {'Entry':<10} | "
        f"{'BASE Exit':<10} | {'BASE PnL':>8} | {'BASE MFE':>8} | {'BASE MAE':>8} | {'BASE Bars':>9} | {'BASE Reason':<35} || "
        f"{'NEW Exit':<10} | {'NEW PnL':>8} | {'NEW MFE':>8} | {'NEW MAE':>8} | {'NEW Bars':>9} | {'NEW Reason':<35} | {'Δ PnL':>8}"
    )
    print(header)
    print("-" * len(header))

    total_base_pnl = 0.0
    total_new_pnl = 0.0

    for target in TARGET_TRADES:
        key = (target["symbol"], target["entry_date"])
        base = baseline_trades.get(key)
        new = guarded_trades.get(key)

        if base and new:
            base_reason = str(base.exit_reason)
            new_reason = str(new.exit_reason)
            delta = new.pnl_pct - base.pnl_pct
            total_base_pnl += base.pnl_pct
            total_new_pnl += new.pnl_pct

            print(
                f"{target['symbol']:<12} | {target['entry_date']:<10} | "
                f"{base.exit_date:<10} | {base.pnl_pct:>+8.2f} | {base.mfe_pct:>8.2f} | {base.mae_pct:>8.2f} | {base.duration:>9} | {base_reason:<35} || "
                f"{new.exit_date:<10} | {new.pnl_pct:>+8.2f} | {new.mfe_pct:>8.2f} | {new.mae_pct:>8.2f} | {new.duration:>9} | {new_reason:<35} | {delta:>+8.2f}"
            )

    print("-" * len(header))
    print(f"{'TOTAL':<12} | {'':10} | {'':10} | {total_base_pnl:>+8.2f} | {'':8} | {'':8} | {'':9} | {'':35} || "
          f"{'':10} | {total_new_pnl:>+8.2f} | {'':8} | {'':8} | {'':9} | {'':35} | {total_new_pnl - total_base_pnl:>+8.2f}")


if __name__ == "__main__":
    main()
