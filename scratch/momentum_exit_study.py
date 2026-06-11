import sys
import numpy as np
import pandas as pd
from pathlib import Path
import copy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import DB_PATH
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import ExitReason, EntryTag
from scripts.walk_forward import get_watchlist_symbols, simulate_trades, compute_profit_factor, compute_expectancy

# We will implement a custom simulation loop inside the study script to try different min_hold periods.
def custom_simulate_trades(
    ticker: str, df: pd.DataFrame,
    entry_cfg, exit_cfg, signal,
    min_hold_bars: int = 0
) -> list[Trade]:
    records = df.to_dict("records")
    n = len(records)
    trades = []
    in_trade = False
    trade = None
    peak_close = 0.0
    delivery_bad_count = 0
    cwvap_values = []
    pending_signal = None
    pending_exit_reason = None

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

            # Call standard check_exit
            reason, delivery_bad_count = signal.check_exit(
                row, prev, trade, peak_close, bars_held,
                delivery_bad_count, cwvap_values, exit_cfg,
                records, i,
            )

            if reason:
                # Apply our experimental suppression logic for momentum trades
                is_momentum_trade = False
                if trade.regime_at_entry == "uptrend" or trade.entry_tag in [EntryTag.FLOW_MOMENTUM.value, EntryTag.COHERENT_PULLBACK.value]:
                    is_momentum_trade = True

                if is_momentum_trade and reason in [str(ExitReason.ST_CROSS), str(ExitReason.PRT_ST_CROSS)]:
                    if bars_held < min_hold_bars:
                        # Suppress the exit!
                        reason = None

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

def run_study(watchlist_name: str, min_holds: list[int]):
    symbols = get_watchlist_symbols(watchlist_name)
    print(f"Loaded {len(symbols)} symbols from watchlist {watchlist_name}")

    # Load all symbol data upfront to speed up iterations
    ledgers = {}
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            ledgers[sym] = res.ledger
        except Exception as e:
            print(f"Failed to load {sym}: {e}")

    signal = SignalFactory.get_signal("savgol_cts")
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()

    from src.trading.signals.savgol_cts import get_symbol_entry_config, get_symbol_exit_config

    # Run once to inspect trade tags and regimes
    sample_trades = []
    for sym, ledger in ledgers.items():
        sym_entry_cfg = get_symbol_entry_config(sym, entry_cfg)
        sym_exit_cfg = get_symbol_exit_config(sym, exit_cfg)
        trades = custom_simulate_trades(sym, ledger, sym_entry_cfg, sym_exit_cfg, signal, min_hold_bars=0)
        sample_trades.extend(trades)

    print("\n--- Diagnostic: Trade entry tags ---")
    tag_counts = pd.Series([t.entry_tag for t in sample_trades]).value_counts()
    print(tag_counts)
    
    print("\n--- Diagnostic: Trade regimes at entry ---")
    regime_counts = pd.Series([t.regime_at_entry for t in sample_trades]).value_counts()
    print(regime_counts)

    print("\n--- Diagnostic: Sample exit reasons ---")
    print(set(t.exit_reason for t in sample_trades))

    results = []

    for min_hold in min_holds:
        print(f"Evaluating min_hold_bars = {min_hold}...")
        all_train_trades = []
        all_test_trades = []

        for sym, ledger in ledgers.items():
            sym_entry_cfg = get_symbol_entry_config(sym, entry_cfg)
            sym_exit_cfg = get_symbol_exit_config(sym, exit_cfg)
            
            trades = custom_simulate_trades(sym, ledger, sym_entry_cfg, sym_exit_cfg, signal, min_hold_bars=min_hold)
            
            # Filter trades by periods
            train_trades = [t for t in trades if "2019-01-01" <= t.entry_date <= "2023-12-31"]
            test_trades = [t for t in trades if "2024-01-01" <= t.entry_date <= "2026-06-10"]
            
            all_train_trades.extend(train_trades)
            all_test_trades.extend(test_trades)

        def get_stats(trades):
            if not trades:
                return 0, 0.0, 0.0
            pnls = [t.pnl_pct for t in trades]
            win_rate = sum(1 for p in pnls if p > 0) / len(pnls) * 100
            avg_pnl = np.mean(pnls)
            pf = compute_profit_factor(trades)
            return len(trades), win_rate, avg_pnl, pf

        tr_count, tr_wr, tr_pnl, tr_pf = get_stats(all_train_trades)
        te_count, te_wr, te_pnl, te_pf = get_stats(all_test_trades)

        print(f"  TRAIN: Trades={tr_count}, WR={tr_wr:.1f}%, Avg P&L={tr_pnl:+.2f}%, PF={tr_pf:.2f}")
        print(f"  TEST : Trades={te_count}, WR={te_wr:.1f}%, Avg P&L={te_pnl:+.2f}%, PF={te_pf:.2f}")
        
        results.append({
            "min_hold": min_hold,
            "train_trades": tr_count,
            "train_wr": tr_wr,
            "train_pnl": tr_pnl,
            "train_pf": tr_pf,
            "test_trades": te_count,
            "test_wr": te_wr,
            "test_pnl": te_pnl,
            "test_pf": te_pf
        })

    df_res = pd.DataFrame(results)
    print("\n--- Summary Table ---")
    print(df_res.to_string(index=False))

if __name__ == "__main__":
    watchlist = "NIFTY 50"
    if len(sys.argv) > 1:
        watchlist = sys.argv[1]
    run_study(watchlist, [0, 1, 2, 3, 4, 5])
