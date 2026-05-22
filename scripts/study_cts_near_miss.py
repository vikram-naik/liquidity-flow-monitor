"""Study CTS near-miss ST_CROSS exits.

Identifies trades where CTS came close to the sell threshold but did not cross,
leading to a loss or significantly lower final PnL.
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
from src.trading.signals.enums import EntryTag

def main():
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal_cls = SignalFactory.get_signal

    symbols = get_watchlist_symbols("NIFTY 50")
    test_end = today_str()

    print("Running simulations to collect all trades...", flush=True)
    all_trades = []
    
    # Run TRAIN and TEST to get the full historical dataset
    signal = signal_cls("savgol_cts")
    all_trades.extend(run_period(symbols, "2019-01-01", "2023-12-31", entry_cfg, exit_cfg, "TRAIN", signal))
    all_trades.extend(run_period(symbols, "2024-01-01", test_end, entry_cfg, exit_cfg, "TEST", signal))

    # Filter to Universal entry trades
    universal_trades = [t for t in all_trades if t.entry_tag == EntryTag.UNIVERSAL_CROSS.value]
    print(f"Total Universal trades: {len(universal_trades)}")

    # Group by symbol for engine lookup
    by_sym = {}
    for t in universal_trades:
        by_sym.setdefault(t.symbol, []).append(t)

    near_miss_results = []
    gap_threshold = 0.10  # CTS within 0.10 of ST is considered a "near miss"

    print("Analyzing trade lifecycles and tracking CTS relative to ST...", flush=True)
    for sym, trades in by_sym.items():
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            ledger = res.ledger
            if ledger is None or ledger.empty:
                continue

            ledger['date_str'] = ledger['date'].astype(str).str[:10]

            for t in trades:
                # Find entry row index
                entry_matches = ledger[ledger['date_str'] == t.entry_date]
                if entry_matches.empty:
                    continue
                entry_idx = entry_matches.index[0]

                # Find exit row index
                exit_matches = ledger[ledger['date_str'] == t.exit_date]
                if exit_matches.empty:
                    exit_idx = len(ledger) - 1
                else:
                    exit_idx = exit_matches.index[0]

                # Extract trade period data (inclusive of entry bar up to signal bar)
                # Note: exit signal day is exit_idx - 1 (EOD-lag)
                signal_end_idx = max(entry_idx, exit_idx - 1)
                trade_data = ledger.iloc[entry_idx : signal_end_idx + 1]

                if trade_data.empty:
                    continue

                # Bar-by-bar analysis
                ever_crossed_st = False
                min_gap = 999.0
                near_miss_bar_info = None

                for idx_rel, (_, row) in enumerate(trade_data.iterrows()):
                    cts = row.get("cts", np.nan)
                    st = row.get("cts_sell_threshold", np.nan)
                    close = row.get("close", np.nan)
                    date_str = row.get("date_str")

                    if np.isnan(cts) or np.isnan(st) or np.isnan(close):
                        continue

                    # If CTS crosses above ST, then it's a normal ST cross setup
                    if cts >= st:
                        ever_crossed_st = True
                        break

                    gap = st - cts
                    if gap < min_gap:
                        min_gap = gap
                        
                        # Calculate unrealized PnL at this near-miss bar
                        pnl_at_bar = (close / t.entry_price - 1) * 100.0
                        near_miss_bar_info = {
                            "date": date_str,
                            "cts": cts,
                            "st": st,
                            "close": close,
                            "pnl": pnl_at_bar,
                            "bar_num": idx_rel + 1
                        }

                # We only care about trades where:
                # 1. CTS never crossed above the sell threshold during the trade
                # 2. CTS came within gap_threshold (0.10) of the sell threshold
                if not ever_crossed_st and min_gap <= gap_threshold and near_miss_bar_info is not None:
                    final_pnl = t.pnl_pct
                    peak_pnl = near_miss_bar_info["pnl"]
                    giveback = peak_pnl - final_pnl

                    near_miss_results.append({
                        "symbol": t.symbol,
                        "entry_date": t.entry_date,
                        "exit_date": t.exit_date,
                        "duration": t.duration,
                        "min_gap": min_gap,
                        "peak_cts": near_miss_bar_info["cts"],
                        "peak_st": near_miss_bar_info["st"],
                        "near_miss_date": near_miss_bar_info["date"],
                        "near_miss_bar": near_miss_bar_info["bar_num"],
                        "near_miss_pnl": peak_pnl,
                        "final_pnl": final_pnl,
                        "giveback": giveback,
                        "exit_reason": t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason),
                    })

        except Exception as e:
            print(f"Error analyzing {sym}: {e}")

    if not near_miss_results:
        print("No near-miss setups found.")
        return

    df_results = pd.DataFrame(near_miss_results)
    # Sort by giveback (descending)
    df_results = df_results.sort_values("giveback", ascending=False)

    # Print Detailed Table
    print(f"\n{'='*160}")
    print(f"CTS NEAR-MISS ST_CROSS ANALYSIS (gap <= {gap_threshold})")
    print(f"{'='*160}")
    header = (
        f"{'#':>3} | {'Symbol':<12} | {'Entry':<10} | {'NearMissDt':<10} | {'Exit':<10} | "
        f"{'Peak CTS':>8} | {'ST':>6} | {'Gap':>6} | "
        f"{'NearMsPnL%':>10} | {'FinalPnL%':>9} | {'Giveback%':>9} | {'Bars':>4} | {'Exit Reason'}"
    )
    print(header)
    print("-" * len(header))

    for i, (_, row) in enumerate(df_results.iterrows(), 1):
        print(
            f"{i:>3} | {row['symbol']:<12} | {row['entry_date']:<10} | {row['near_miss_date']:<10} | {row['exit_date']:<10} | "
            f"{row['peak_cts']:>8.4f} | {row['peak_st']:>6.3f} | {row['min_gap']:>6.4f} | "
            f"{row['near_miss_pnl']:>10.2f} | {row['final_pnl']:>9.2f} | {row['giveback']:>9.2f} | {row['duration']:>4} | {row['exit_reason']}"
        )

    # Aggregates
    print(f"\n{'='*80}")
    print(f"SUMMARY STATISTICS: CTS Near-Misses (gap <= {gap_threshold})")
    print(f"{'='*80}")
    total = len(df_results)
    avg_giveback = df_results["giveback"].mean()
    med_giveback = df_results["giveback"].median()
    max_giveback = df_results["giveback"].max()
    ended_in_loss = df_results[df_results["final_pnl"] < 0]
    loss_count = len(ended_in_loss)
    loss_pct = loss_count / total * 100

    print(f"  Total Near-Miss Setups: {total}")
    print(f"  Average PnL Giveback  : {avg_giveback:+.2f}%")
    print(f"  Median PnL Giveback   : {med_giveback:+.2f}%")
    print(f"  Maximum PnL Giveback  : {max_giveback:+.2f}%")
    print(f"  Trades Ending in Loss : {loss_count} ({loss_pct:.1f}%)")
    
    if loss_count > 0:
        print(f"    Avg Loss of these   : {ended_in_loss['final_pnl'].mean():.2f}%")

if __name__ == "__main__":
    main()
