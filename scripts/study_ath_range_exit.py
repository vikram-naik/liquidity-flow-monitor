#!/usr/bin/env python3
"""
Study to optimize exit strategy based on range_pos_{x} and is_ath features.
Evaluates the baseline against multiple combinations of thresholds and guard bypass settings.
"""

import sys
import os
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from tabulate import tabulate
from tqdm import tqdm
from datetime import datetime

# Set up project path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade, SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState
from src.trading.signals.savgol_cts.symbol_configs import get_symbol_entry_config, get_symbol_exit_config
from src.trading.signals.enums import ExitReason, EntryTag
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross
from src.trading.signals.savgol_cts.exits.cwvap_guard import apply_cwvap_guard
from src.database import DB_PATH

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

def get_watchlist_symbols(name: str) -> list[str]:
    db = sqlite3.connect(str(DB_PATH))
    row = db.execute("SELECT id FROM watchlists WHERE name = ?", (name,)).fetchone()
    if not row:
        db.close()
        print(f"Watchlist '{name}' not found.")
        sys.exit(1)
    symbols = [
        r[0] for r in db.execute(
            "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY display_order",
            (row[0],),
        ).fetchall()
    ]
    db.close()
    return symbols

def check_exit_custom(
    row: dict,
    prev_row: dict,
    trade: Trade,
    peak_close: float,
    bars_held: int,
    delivery_bad_count: int,
    cwvap_values: list[float],
    cfg: SavgolCTSExitConfig,
    records: list[dict],
    idx: int,
    threshold: float,
    bypass_cwvap: bool,
) -> tuple[str | None, int]:
    st = SavgolCTSExitState.from_int(delivery_bad_count)
    tag = trade.entry_tag if trade is not None else ""
    close = row.get("close", np.nan)
    cwvap = row.get("cwvap", np.nan)

    if not np.isnan(close) and not np.isnan(cwvap) and close > cwvap:
        st.price_above_cwvap = True

    # 1. Run baseline path-specific exits (Universal Cross)
    uc_cfg = cfg.universal_cross
    exit_reason, state_val = exit_universal_cross(
        row, prev_row, trade, peak_close, bars_held, st.to_int(),
        uc_cfg, records, idx
    )
    st = SavgolCTSExitState.from_int(state_val)

    # Apply path-specific fallbacks for Anchor-Shock-Pullback trades
    if not exit_reason and tag == EntryTag.ANCHOR_SHOCK_PULLBACK.value:
        asp_exit_cfg = getattr(cfg, "anchor_shock_pullback", None)
        if asp_exit_cfg and asp_exit_cfg.enabled:
            # 1. Hard Stop Capping
            if asp_exit_cfg.hard_stop_enabled:
                pnl_pct = (close / trade.entry_price - 1.0) * 100.0
                if pnl_pct <= -asp_exit_cfg.hard_stop_pct:
                    exit_reason = ExitReason.HARD_STOP

            # 2. Time Decay Limit
            if not exit_reason and asp_exit_cfg.time_decay_enabled:
                if bars_held >= asp_exit_cfg.max_hold_bars:
                    exit_reason = ExitReason.TIME_DECAY

    # 2. Check ATH + range_pos_x maxed out condition
    ath_exit_triggered = False
    is_ath = row.get("is_ath", False)
    rp10 = row.get("range_pos_10", np.nan)
    rp22 = row.get("range_pos_22", np.nan)
    rp63 = row.get("range_pos_63", np.nan)
    rp252 = row.get("range_pos_252", np.nan)

    if is_ath and not any(np.isnan(x) for x in [rp10, rp22, rp63, rp252]):
        if rp10 >= threshold and rp22 >= threshold and rp63 >= threshold and rp252 >= threshold:
            ath_exit_triggered = True

    if ath_exit_triggered:
        if bypass_cwvap:
            # Direct exit, bypass CWVAP guard completely
            return "ath_max_range_exit", st.to_int()
        else:
            # Treat as standard exit reason, subject to CWVAP guard
            if not exit_reason:
                exit_reason = "ath_max_range_exit"

    # Apply common CWVAP guard logic (can suppress or trigger exits)
    final_reason, st_val = apply_cwvap_guard(
        row, trade, exit_reason, st.to_int(),
        cfg, records, idx, tag
    )

    if final_reason:
        return str(final_reason), st_val

    return None, st_val

def simulate_trades_custom(
    ticker: str, df: pd.DataFrame,
    entry_cfg, exit_cfg, signal,
    threshold: float | None = None,
    bypass_cwvap: bool = False
) -> list[dict]:
    records = df.to_dict("records")
    n = len(records)
    trades = []
    in_trade = False
    trade = None
    peak_close = 0.0
    delivery_bad_count = 0
    cwvap_values: list[float] = []
    pending_signal: dict | None = None
    pending_exit_reason: str | None = None

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
            pnl = (exit_price / trade.entry_price - 1) * 100.0
            
            trades.append({
                "symbol": ticker,
                "entry_date": trade.entry_date,
                "entry_price": trade.entry_price,
                "exit_date": str(row.get("date", ""))[:10],
                "exit_price": round(exit_price, 2),
                "exit_reason": pending_exit_reason,
                "pnl_pct": round(pnl, 2),
                "mfe_pct": round(trade.mfe_pct, 2),
                "mae_pct": round(trade.mae_pct, 2),
                "duration": i - trade.entry_idx
            })
            
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

            if threshold is None:
                # Baseline
                reason, delivery_bad_count = signal.check_exit(
                    row, prev, trade, peak_close, bars_held,
                    delivery_bad_count, cwvap_values, exit_cfg,
                    records, i,
                )
            else:
                # Custom Exit Rule
                reason, delivery_bad_count = check_exit_custom(
                    row, prev, trade, peak_close, bars_held,
                    delivery_bad_count, cwvap_values, exit_cfg,
                    records, i, threshold, bypass_cwvap
                )

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
        pnl = (last.get("close", trade.entry_price) / trade.entry_price - 1) * 100.0
        trades.append({
            "symbol": ticker,
            "entry_date": trade.entry_date,
            "entry_price": trade.entry_price,
            "exit_date": str(last.get("date", ""))[:10],
            "exit_price": round(last.get("close", trade.entry_price), 2),
            "exit_reason": "End of Data (Open Trade)",
            "pnl_pct": round(pnl, 2),
            "mfe_pct": round(trade.mfe_pct, 2),
            "mae_pct": round(trade.mae_pct, 2),
            "duration": n - 1 - trade.entry_idx
        })

    return trades

def run_study():
    watchlists = ["NIFTY 50", "NSE F&O"]
    periods = [
        ("TRAIN", "2019-01-01", "2023-12-31"),
        ("TEST", "2024-01-01", datetime.now().strftime("%Y-%m-%d")),
        ("COMBINED", "2019-01-01", datetime.now().strftime("%Y-%m-%d"))
    ]
    
    thresholds = [1.0, 0.99, 0.98, 0.95, 0.90]
    bypass_options = [True, False]
    
    signal = SignalFactory.get_signal("savgol_cts")
    
    # Pre-load all watchlists and symbols
    wl_symbols = {}
    all_unique_symbols = set()
    for wl in watchlists:
        symbols = get_watchlist_symbols(wl)
        wl_symbols[wl] = symbols
        all_unique_symbols.update(symbols)
        
    print(f"Pre-loading data and warming up DivergenceEngine for {len(all_unique_symbols)} unique symbols...")
    cached_ledgers = {}
    for sym in tqdm(sorted(all_unique_symbols)):
        try:
            # Full history run for warm-up
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            res = engine.run()
            cached_ledgers[sym] = res.ledger
        except Exception as e:
            print(f"\nFailed to load {sym}: {e}")
            
    # Now run simulations for each watchlist
    output_lines = []
    def w(line: str = ""):
        output_lines.append(line)
        print(line)
        
    w("=" * 80)
    w("             EXIT OPTIMIZATION STUDY: ATH & RANGE POSITION MAX")
    w(f"             Run Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    w("=" * 80)
    w()
    
    for wl in watchlists:
        symbols = [s for s in wl_symbols[wl] if s in cached_ledgers]
        w("*" * 80)
        w(f" WATCHLIST: {wl} ({len(symbols)} active symbols)")
        w("*" * 80)
        w()
        
        # 1. Run all trades under baseline and custom configurations first, then filter by period
        configs = [("baseline", None, False)]
        for thr in thresholds:
            for bypass in bypass_options:
                cfg_name = f"ATH_Range_{thr:.2f}_Bypass_{bypass}"
                configs.append((cfg_name, thr, bypass))
                
        # Run all trades for each config across all symbols
        all_config_trades = {}
        for cfg_name, thr, bypass in configs:
            trades = []
            for sym in symbols:
                entry_cfg = get_symbol_entry_config(sym)
                exit_cfg = get_symbol_exit_config(sym)
                ledger = cached_ledgers[sym]
                sym_trades = simulate_trades_custom(sym, ledger, entry_cfg, exit_cfg, signal, thr, bypass)
                trades.extend(sym_trades)
            all_config_trades[cfg_name] = trades

        # 2. Summarize results for each period
        for period_label, start_d, end_d in periods:
            w("-" * 80)
            w(f" PERIOD: {period_label} ({start_d} to {end_d})")
            w("-" * 80)
            
            results = []
            for cfg_name, thr, bypass in configs:
                trades = all_config_trades[cfg_name]
                # Filter by period
                period_trades = [t for t in trades if start_d <= t["entry_date"] <= end_d]
                
                if not period_trades:
                    results.append({
                        "Strategy": cfg_name, "Trades": 0, "Win%": 0.0, "Avg PnL%": 0.0,
                        "Med PnL%": 0.0, "Profit Factor": 0.0, "Avg MFE%": 0.0, "Avg MAE%": 0.0, "Avg Dur": 0.0
                    })
                    continue
                
                df_trades = pd.DataFrame(period_trades)
                winners = (df_trades["pnl_pct"] > 0).sum()
                total = len(df_trades)
                win_rate = winners / total * 100.0
                avg_pnl = df_trades["pnl_pct"].mean()
                median_pnl = df_trades["pnl_pct"].median()
                avg_mfe = df_trades["mfe_pct"].mean()
                avg_mae = df_trades["mae_pct"].mean()
                avg_dur = df_trades["duration"].mean()
                
                gross_win = sum(t["pnl_pct"] for t in period_trades if t["pnl_pct"] > 0)
                gross_loss = abs(sum(t["pnl_pct"] for t in period_trades if t["pnl_pct"] <= 0))
                profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
                
                results.append({
                    "Strategy": cfg_name,
                    "Trades": total,
                    "Win%": round(win_rate, 2),
                    "Avg PnL%": round(avg_pnl, 2),
                    "Med PnL%": round(median_pnl, 2),
                    "Profit Factor": round(profit_factor, 2),
                    "Avg MFE%": round(avg_mfe, 2),
                    "Avg MAE%": round(avg_mae, 2),
                    "Avg Dur": round(avg_dur, 1)
                })
                
            # Render using tabulate
            headers = ["Strategy", "Trades", "Win%", "Avg PnL%", "Med PnL%", "Profit Factor", "Avg MFE%", "Avg MAE%", "Avg Dur"]
            table_rows = []
            for r in results:
                table_rows.append([
                    r["Strategy"], r["Trades"], f"{r['Win%']:.2f}%", f"{r['Avg PnL%']:+.2f}%",
                    f"{r['Med PnL%']:+.2f}%", f"{r['Profit Factor']:.2f}", f"{r['Avg MFE%']:.2f}%",
                    f"{r['Avg MAE%']:.2f}%", f"{r['Avg Dur']:.1f}"
                ])
                
            w(tabulate(table_rows, headers=headers, tablefmt="simple"))
            w()
            
    # Save report
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_text = "\n".join(output_lines)
    report_path = OUTPUT_DIR / "study_ath_range_exit_report.txt"
    report_path.write_text(report_text)
    print(f"Study complete. Detailed report saved to {report_path}")

if __name__ == "__main__":
    run_study()
