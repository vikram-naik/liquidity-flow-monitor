"""Check PRT vs prt_buy_threshold on the ST_CROSS SIGNAL day (bar before exit_date).

Uses dump_trades output to identify ST_CROSS trades, then looks at the
bar where the exit signal actually fired (one bar before exit_date due to EOD-lag).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.walk_forward import run_period, get_watchlist_symbols, today_str
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag, ExitReason


def main():
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    symbols = get_watchlist_symbols("NIFTY 50")
    test_end = today_str()

    all_trades = []
    print("Running TRAIN period...", flush=True)
    all_trades.extend(run_period(symbols, "2019-01-01", "2023-12-31",
                                 entry_cfg, exit_cfg, "TRAIN", signal))
    print("Running TEST period...", flush=True)
    all_trades.extend(run_period(symbols, "2024-01-01", test_end,
                                 entry_cfg, exit_cfg, "TEST", signal))

    # Filter to Universal + ST_CROSS (string match like dump_trades does)
    st_cross_trades = [
        t for t in all_trades
        if t.entry_tag == EntryTag.UNIVERSAL_CROSS.value
        and "st_cross" in str(t.exit_reason).lower()
        and "prt" not in str(t.exit_reason).lower()
    ]

    print(f"\nTotal trades: {len(all_trades)}")
    print(f"Universal + ST_CROSS exits: {len(st_cross_trades)}")

    # Group by symbol
    by_sym = {}
    for t in st_cross_trades:
        by_sym.setdefault(t.symbol, []).append(t)

    results = []

    for sym, trades in by_sym.items():
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            ledger = res.ledger
            if ledger is None or ledger.empty:
                continue

            ledger['date_str'] = ledger['date'].astype(str).str[:10]

            for t in trades:
                # exit_date is bar i+1 (execution day).
                # ST_CROSS signal fires on bar i (one bar BEFORE exit_date).
                exit_matches = ledger[ledger['date_str'] == t.exit_date]
                if exit_matches.empty:
                    continue

                exit_exec_idx = exit_matches.index[0]
                signal_idx = exit_exec_idx - 1  # The bar where ST_CROSS actually fired

                if signal_idx < 0:
                    continue

                signal_row = ledger.iloc[signal_idx]
                signal_date = str(signal_row["date"])[:10]

                prt = signal_row.get("prt", np.nan)
                prt_bt = signal_row.get("prt_buy_threshold", np.nan)
                prt_st = signal_row.get("prt_sell_threshold", np.nan)
                cts = signal_row.get("cts", np.nan)
                cts_st = signal_row.get("cts_sell_threshold", np.nan)
                prt_slope = signal_row.get("prt_slope", np.nan)

                # PRT position on signal day
                if np.isnan(prt) or np.isnan(prt_bt):
                    prt_pos = "N/A"
                elif prt <= prt_bt:
                    prt_pos = "BELOW_BT"
                elif not np.isnan(prt_st) and prt >= prt_st:
                    prt_pos = "ABOVE_ST"
                else:
                    prt_pos = "BETWEEN"

                results.append({
                    "symbol": sym,
                    "entry_date": t.entry_date,
                    "exit_date": t.exit_date,
                    "signal_date": signal_date,
                    "pnl_pct": t.pnl_pct,
                    "mfe_pct": t.mfe_pct,
                    "mae_pct": t.mae_pct,
                    "duration": t.duration,
                    "prt_at_signal": prt,
                    "prt_bt": prt_bt,
                    "prt_st": prt_st,
                    "prt_slope": prt_slope,
                    "cts_at_signal": cts,
                    "cts_st": cts_st,
                    "prt_position": prt_pos,
                })
        except Exception as e:
            print(f"  ERROR: {sym}: {e}")

    if not results:
        print("No results.")
        return

    df = pd.DataFrame(results)
    df = df.sort_values("exit_date")

    # Print detailed table
    print(f"\n{'='*160}")
    print(f"ST_CROSS EXIT ANALYSIS: PRT Position on SIGNAL DAY (bar before exit_date)")
    print(f"{'='*160}")
    header = (
        f"{'#':>3} | {'Symbol':<12} | {'Entry':<10} | {'SigDay':<10} | {'Exit':<10} | "
        f"{'PnL%':>7} | {'MFE%':>7} | {'Bars':>4} | "
        f"{'PRT@Sig':>9} | {'PRT_BT':>8} | {'PRT_ST':>8} | "
        f"{'PRT_Slope':>9} | {'CTS@Sig':>8} | {'CTS_ST':>8} | {'PRT Pos'}"
    )
    print(header)
    print("-" * len(header))

    for i, (_, row) in enumerate(df.iterrows(), 1):
        print(
            f"{i:>3} | {row['symbol']:<12} | {row['entry_date']:<10} | "
            f"{row['signal_date']:<10} | {row['exit_date']:<10} | "
            f"{row['pnl_pct']:>7.2f} | {row['mfe_pct']:>7.2f} | "
            f"{row['duration']:>4} | {row['prt_at_signal']:>9.4f} | "
            f"{row['prt_bt']:>8.4f} | {row['prt_st']:>8.4f} | "
            f"{row['prt_slope']:>9.4f} | "
            f"{row['cts_at_signal']:>8.4f} | {row['cts_st']:>8.4f} | {row['prt_position']}"
        )

    # Summary
    print(f"\n{'='*80}")
    print(f"SUMMARY: PRT Position on ST_CROSS Signal Day")
    print(f"{'='*80}")

    total = len(df)
    for pos in ["BELOW_BT", "BETWEEN", "ABOVE_ST", "N/A"]:
        subset = df[df["prt_position"] == pos]
        count = len(subset)
        pct = count / total * 100
        avg_pnl = subset["pnl_pct"].mean() if count > 0 else 0
        avg_mfe = subset["mfe_pct"].mean() if count > 0 else 0
        win_rate = (subset["pnl_pct"] > 0).mean() * 100 if count > 0 else 0
        print(
            f"  {pos:<12}: {count:>3} trades ({pct:>5.1f}%)  |  "
            f"Avg PnL: {avg_pnl:>+6.2f}%  |  Avg MFE: {avg_mfe:>6.2f}%  |  "
            f"Win Rate: {win_rate:>5.1f}%"
        )
    print(f"\n  TOTAL: {total} trades")

    # Print the BELOW_BT trades specifically
    below_bt = df[df["prt_position"] == "BELOW_BT"]
    if not below_bt.empty:
        print(f"\n{'='*80}")
        print(f"BELOW_BT TRADES (PRT in buy territory on signal day)")
        print(f"{'='*80}")
        for _, row in below_bt.iterrows():
            print(f"  {row['symbol']:<12} | Entry: {row['entry_date']} | "
                  f"Signal: {row['signal_date']} | Exit: {row['exit_date']} | "
                  f"PnL: {row['pnl_pct']:>+7.2f}% | MFE: {row['mfe_pct']:>6.2f}% | "
                  f"Bars: {row['duration']:>3} | "
                  f"PRT: {row['prt_at_signal']:>+.4f} vs BT: {row['prt_bt']:>+.4f}")


if __name__ == "__main__":
    main()
