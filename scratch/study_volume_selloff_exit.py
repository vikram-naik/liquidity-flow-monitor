#!/usr/bin/env python3
import sys
import os
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade, SignalFactory
from src.trading.signals.savgol_cts.config import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState
from src.trading.signals.savgol_cts.symbol_configs import get_symbol_entry_config, get_symbol_exit_config
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross
from src.trading.signals.savgol_cts.exits.cwvap_guard import apply_cwvap_guard
from src.database import DB_PATH

def get_watchlist_symbols(name: str) -> list[str]:
    db = sqlite3.connect(str(DB_PATH))
    row = db.execute("SELECT id FROM watchlists WHERE name = ?", (name,)).fetchone()
    if not row:
        db.close()
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
    volume_exit_params: dict | None
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

    # 2. Run baseline CWVAP guard
    final_reason, st_val = apply_cwvap_guard(
        row, trade, exit_reason, st.to_int(),
        cfg, records, idx, tag
    )
    st = SavgolCTSExitState.from_int(st_val)

    # 3. Apply custom Volume Selloff Exit
    if final_reason is None and volume_exit_params is not None and volume_exit_params.get("enabled", False):
        # Check volume event conditions
        rdv = row.get("rdv", 0.0)
        dv_shock = row.get("dv_shock", 0.0)
        open_now = row.get("open", np.nan)
        close_now = row.get("close", np.nan)
        prev_close = prev_row.get("close", np.nan) if prev_row else np.nan

        # Surge thresholds
        rdv_thresh = volume_exit_params.get("rdv_threshold", 3.0)
        shock_thresh = volume_exit_params.get("shock_threshold", 2.0)
        
        has_volume_surge = (rdv >= rdv_thresh) or (dv_shock >= shock_thresh)
        
        # Price condition
        price_cond = volume_exit_params.get("price_condition", "close_under_open")
        is_down_day = False
        if price_cond == "close_under_open":
            is_down_day = (close_now < open_now)
        elif price_cond == "close_under_prev_close":
            is_down_day = (close_now < prev_close)
        elif price_cond == "both":
            is_down_day = (close_now < open_now) and (close_now < prev_close)
        elif price_cond == "negative_return_pct":
            min_ret = volume_exit_params.get("negative_return_threshold", -0.5)
            daily_ret = ((close_now / prev_close - 1) * 100) if not np.isnan(prev_close) else 0.0
            is_down_day = (daily_ret <= min_ret)

        if has_volume_surge and is_down_day:
            # PnL condition to protect winners
            pnl_pct = (close_now / trade.entry_price - 1) * 100.0
            max_pnl_allowed = volume_exit_params.get("max_pnl_allowed", 999.0)
            if pnl_pct <= max_pnl_allowed:
                final_reason = "ExitReason.VOLUME_SELLOFF"

    return final_reason, st_val

def simulate_trades_custom(
    ticker: str, df: pd.DataFrame,
    entry_cfg, exit_cfg, signal, volume_exit_params: dict | None
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

            reason, delivery_bad_count = check_exit_custom(
                row, prev, trade, peak_close, bars_held,
                delivery_bad_count, cwvap_values, exit_cfg,
                records, i, volume_exit_params
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

def evaluate_run(symbols, start_date, volume_exit_params):
    signal = SignalFactory.get_signal("savgol_cts")
    all_trades = []
    
    for sym in symbols:
        try:
            # We must load full history so indicators warm up correctly
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            result = engine.run()
            
            entry_cfg = get_symbol_entry_config(sym)
            exit_cfg = get_symbol_exit_config(sym)
            
            trades = simulate_trades_custom(sym, result.ledger, entry_cfg, exit_cfg, signal, volume_exit_params)
            period_trades = [t for t in trades if start_date <= t["entry_date"]]
            all_trades.extend(period_trades)
        except Exception as e:
            pass
            
    if not all_trades:
        return {}
        
    df = pd.DataFrame(all_trades)
    total = len(df)
    winners = (df["pnl_pct"] > 0).sum()
    win_rate = winners / total * 100
    avg_pnl = df["pnl_pct"].mean()
    median_pnl = df["pnl_pct"].median()
    avg_mfe = df["mfe_pct"].mean()
    avg_mae = df["mae_pct"].mean()
    avg_dur = df["duration"].mean()
    
    # Calculate profit factor
    gross_win = sum(t["pnl_pct"] for t in all_trades if t["pnl_pct"] > 0)
    gross_loss = abs(sum(t["pnl_pct"] for t in all_trades if t["pnl_pct"] <= 0))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
    
    # Check JINDALSTEL trade if it exists
    jindal_trade = df[(df["symbol"] == "JINDALSTEL") & (df["entry_date"] >= "2026-05-01")]
    if not jindal_trade.empty:
        jindal_info = {
            "entry_date": jindal_trade["entry_date"].values[0],
            "exit_date": jindal_trade["exit_date"].values[0],
            "pnl": jindal_trade["pnl_pct"].values[0],
            "reason": jindal_trade["exit_reason"].values[0]
        }
    else:
        jindal_info = None

    return {
        "trades": total,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "median_pnl": median_pnl,
        "profit_factor": profit_factor,
        "avg_mfe": avg_mfe,
        "avg_mae": avg_mae,
        "avg_dur": avg_dur,
        "jindal_info": jindal_info
    }

def main():
    watchlist_name = "NSE F&O"
    start_date = "2024-01-01"
    symbols = get_watchlist_symbols(watchlist_name)
    if "JINDALSTEL" not in symbols:
        symbols.append("JINDALSTEL")
    
    print(f"Evaluating volume selloff exit on {watchlist_name} (from {start_date})...")
    
    # Define combinations of parameters
    param_sets = [
        {"name": "Baseline (No Volume Exit)", "params": None},
        {"name": "RDV >= 3.5, Return <= -1.0%, PnL <= 0.0%", 
         "params": {"enabled": True, "rdv_threshold": 3.5, "shock_threshold": 999.0, "price_condition": "negative_return_pct", "negative_return_threshold": -1.0, "max_pnl_allowed": 0.0}},
        {"name": "RDV >= 4.0, Return <= -1.0%, PnL <= 0.0%", 
         "params": {"enabled": True, "rdv_threshold": 4.0, "shock_threshold": 999.0, "price_condition": "negative_return_pct", "negative_return_threshold": -1.0, "max_pnl_allowed": 0.0}},
    ]
    
    results = []
    for pset in param_sets:
        name = pset["name"]
        print(f"Running: {name}...")
        res = evaluate_run(symbols, start_date, pset["params"])
        if res:
            res["name"] = name
            results.append(res)
            
    # Print results
    print("\n" + "=" * 130)
    print("                                   VOLUME SELLOFF EXIT PARAMETER SWEEP")
    print("=" * 130)
    
    headers = ["Strategy Name", "Trades", "Win Rate%", "Avg P&L%", "Med P&L%", "Profit Factor", "Avg Dur", "JINDALSTEL Trade (May 2026)"]
    rows = []
    for r in results:
        j_info = "-"
        if r["jindal_info"]:
            j = r["jindal_info"]
            j_info = f"Entry {j['entry_date']} -> Exit {j['exit_date']} PnL: {j['pnl']:+.2f}% ({j['reason']})"
            
        rows.append([
            r["name"],
            r["trades"],
            f"{r['win_rate']:.2f}%",
            f"{r['avg_pnl']:+.2f}%",
            f"{r['median_pnl']:+.2f}%",
            f"{r['profit_factor']:.2f}",
            f"{r['avg_dur']:.1f}",
            j_info
        ])
        
    print(tabulate(rows, headers=headers, tablefmt="simple"))
    print("=" * 130)

if __name__ == "__main__":
    main()
