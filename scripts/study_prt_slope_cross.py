"""
PRT Slope Cross Study
================================
Evolving a new entry path: 
1. prt_slope crossses zero ( prev_prt_slope < 0 and prt_slope > 0) 
2. fas < 0.

Exit Logic:
- Institutional Floor methodology: 
  Phase 1: PSZ zero-cross cycle (Wait for PSZ > 0, then PSZ < 0).
  Phase 2: CTS trail (Hold until CTS >= cts_sell_threshold).
- PnL cap and hard stop active.
"""

import argparse
import io
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from tabulate import tabulate

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.savgol_cts.state import SavgolCTSExitState

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

def get_watchlist_symbols(name: str) -> list[str]:
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return []
    db = sqlite3.connect(str(DB_PATH))
    row = db.execute("SELECT id FROM watchlists WHERE name = ?", (name,)).fetchone()
    if not row:
        avail = [r[0] for r in db.execute("SELECT name FROM watchlists ORDER BY name").fetchall()]
        db.close()
        print(f"Watchlist '{name}' not found. Available: {avail}")
        return []
    symbols = [
        r[0] for r in db.execute(
            "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY display_order",
            (row[0],),
        ).fetchall()
    ]
    db.close()
    return symbols

def simulate_trades_study(
    ticker: str, df: pd.DataFrame,
    entry_cfg: SavgolCTSEntryConfig, exit_cfg: SavgolCTSExitConfig
) -> list[Trade]:
    records = df.to_dict("records")
    n = len(records)
    trades = []
    in_trade = False
    trade = None
    peak_close = 0.0
    # bitfield packed state
    state_val = 0
    pending_signal: dict | None = None
    pending_exit_reason: ExitReason | str | None = None

    ecfg = exit_cfg.institutional_floor

    for i in range(10, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        prev_close = prev.get("close", np.nan)
        if np.isnan(close):
            continue

        if pending_exit_reason is not None:
            

            open_price = row.get("open", np.nan)
            exit_price = open_price if not np.isnan(open_price) else close
            pnl_pct = round((exit_price / trade.entry_price - 1) * 100, 2)
            # print(f"Exit : dt: {row.get("date")}, {pending_exit_reason}, {pnl_pct} \n")
            trade.exit_date = str(row.get("date", ""))[:10]
            trade.exit_price = round(exit_price, 2)
            trade.exit_reason = pending_exit_reason
            trade.pnl_pct = pnl_pct
            trade.duration = i - trade.entry_idx
            trade.mfe_pct = round(trade.mfe_pct, 2)
            trade.mae_pct = round(trade.mae_pct, 2)
            trades.append(trade)
            in_trade = False
            trade = None
            state_val = 0
            pending_exit_reason = None
            continue

        if in_trade:
            if close > peak_close:
                peak_close = close
            
            mfe = max(trade.mfe_pct, (close / trade.entry_price - 1) * 100)
            mae_val = (close / trade.entry_price - 1) * 100
            mae = min(-trade.mae_pct, mae_val)
            trade.mfe_pct = mfe
            if -mae > trade.mae_pct: trade.mae_pct = -mae

            # --- NEW EXIT LOGIC ---
            st = SavgolCTSExitState.from_int(state_val)
            pnl_pct = (close / trade.entry_price - 1) * 100.0
            bars_held = i - trade.entry_idx
            fas = row.get("fas", np.nan)

            # 1. Hard Stop (8%)
            # print(f"pnl_pct: {pnl_pct}")
            if pnl_pct <= -30.0:
                pending_exit_reason = ExitReason.HARD_STOP
            else:
                cwvap = row.get("cwvap", np.nan)
                cts = row.get("cts", np.nan)
                cts_st = row.get("cts_sell_threshold", np.nan)

                if not st.price_above_cwvap:
                    # Phase 1: Waiting for CWVAP reclaim
                    if not np.isnan(close) and not np.isnan(cwvap) and close > cwvap:
                        st.price_above_cwvap = True
                    elif bars_held >= 8:
                        pending_exit_reason = ExitReason.RECLAIM_TIMEOUT
                else:
                    # Phase 2: fas trailing
                    if not np.isnan(fas) and (fas < -0.05 or fas >= 1):
                        pending_exit_reason = ExitReason.ST_CROSS
            
            state_val = st.to_int()

        elif pending_signal is not None:
            sig = pending_signal
            pending_signal = None
            atr = row.get("atr_20", 0)
            if atr <= 0 or np.isnan(atr): 
                atr = close * 0.02
                
            trade = Trade(
                symbol=ticker,
                entry_date=str(row.get("date", ""))[:10],
                entry_price=close,
                entry_idx=i,
                atr_at_entry=atr,
                soft_filters_passed=sig["details"]["soft_filters"],
                entry_tag=sig["details"]["entry_tag"],
                conviction_score=sig["details"]["score"],
            )
            trade.signal_date = sig["details"]["signal_date"]
            trade.psz_at_entry = sig['details']['psz_now']
            trade.cts_at_signal = sig['details']['cts_now']
            trade.regime_at_entry = sig['details']['regime']
            # trade.slope_delta = sig['details']['cts_slope_delta']
            trade.cts_accel = sig['details']['cts_accel']
            trade.cts_accel_threshold = sig['details']['cts_accel_threshold']
            trade.prt_accel = sig['details'].get('prt_accel', 0)
            trade.bars_at_base = sig['details'].get('bars_at_base', 0)
            trade.base_tightness = sig['details'].get('base_tightness', 0)
            trade.range_width_10 = sig['details'].get('range_width_10', 0)
            trade.range_width_63 = sig['details'].get('range_width_63', 0)
            trade.range_width_252 = sig['details'].get('range_width_252', 0)
            trade.rdv_pos = sig['details'].get('is_rdv_+ve', 0)
            trade.cts_accel_delta = sig['details'].get('cts_accel_delta',0)
            peak_close = close
            state_val = 0
            in_trade = True

        else:
            # ENTRY CRITERIA
            cts = row.get("cts", np.nan)
            prt_s = row.get("prt_slope", np.nan)
            prt_accel = row.get("prt_accel", np.nan)
            prev_prt_s = prev.get("prt_slope", np.nan)
            
            ps_1 = prev.get("prt_slope", np.nan)
            ps_2 = records[i-2].get("prt_slope", np.nan)
            
            psz = row.get("price_slope_z", np.nan)
            psz_v = row.get("psz_v", np.nan)
            psz_v_1 = prev.get("psz_v", np.nan)
            
            rsz = row.get("rdv_slope_z", np.nan)
            cs = row.get("cts_slope", np.nan)
            
            fas = row.get("fas", np.nan)
            
            is_distributing = row.get("is_distributing", False)
            is_overbought = row.get("is_overbought", False)
            
            # --- NEXTGEN GATES ---
            macro_bull = row.get("macro_bullish", False)
            medium_pullback = row.get("medium_pullback", False)
            short_stabilizing = row.get("short_stabilizing", False)
            
            filter_on_rsz = rsz < 0
            
            has_prt_s_crossed_zero = prev_prt_s < 0 and prt_s > 0
            is_ps_rising = prt_s > ps_1 > ps_2 and prt_s >= 0.05
            
            is_fas_negative = fas < 0 and fas >= -1.0
            
            is_psz_below_threshold = psz < -0.1
            is_psz_rising = psz_v > 0 and psz_v > psz_v_1 
            is_rsz_positive = rsz > 0
            is_cs_negative = cs < 0
            is_cs_rising = True 
            is_cts_negative = cts < 0
            cts_accel = row.get("cts_accel", np.nan)
            cts_accel_threshold = row.get("cts_accel_threshold", np.nan)
            is_cs_accelerating = cts_accel > cts_accel_threshold
            filter_on_accel = (prt_accel > 0.1 and (cts_accel-cts_accel_threshold) < 0.01)


            if has_prt_s_crossed_zero and is_fas_negative and \
                is_ps_rising and is_psz_below_threshold and is_psz_rising \
                and is_cs_negative and is_cs_rising and is_cs_accelerating \
                and is_cts_negative and not(filter_on_rsz) and not(filter_on_accel) :
                # Scoring Logic (Experimental)
                score = 10
                
                pending_signal = {
                    "details": {
                        "signal_date": str(row.get("date", ""))[:10],
                        "entry_tag": "CTS_BULL_CROSS",
                        "score": score,
                        "soft_filters": 10,
                        "psz_now": 0,
                        "cts_now": 0,
                        "regime": row.get("regime", ""),
                        "cts_accel": row.get("cts_accel", 0),
                        "cts_accel_threshold": row.get("cts_accel_threshold", 0),
                        "prt_accel": prt_accel,
                        "bars_at_base": row.get("bars_at_base", 0),
                        "base_tightness": row.get("base_tightness", 0),
                        "range_width_10": row.get("range_width_10", 0),
                        "range_width_63": row.get("range_width_63", 0),
                        "range_width_252": row.get("range_width_252", 0),
                        "is_rdv_+ve": is_rsz_positive,
                        "cts_accel_delta": f"{(cts_accel - cts_accel_threshold):.4f}"
                    }
                }

    if in_trade and trade:
        last = records[-1]
        trade.exit_date, trade.exit_price = str(last.get("date", ""))[:10], last.get("close", trade.entry_price)
        trade.exit_reason = ExitReason.END_OF_DATA
        trade.pnl_pct, trade.duration = round((trade.exit_price / trade.entry_price - 1) * 100, 2), n - 1 - trade.entry_idx
        trade.mfe_pct, trade.mae_pct = round(trade.mfe_pct, 2), round(trade.mae_pct, 2)
        trades.append(trade)
    return trades

def summarize_study(trades: list[Trade], label: str, out: io.StringIO):
    if not trades:
        out.write(f"\n{label}: No trades found.\n")
        return
    
    trade_dicts = []
    for t in trades:
        trade_dicts.append({
            "Symbol": t.symbol, 
            "Entry": t.entry_date, 
            "Exit": t.exit_date,
            "PnL%": t.pnl_pct, 
            "MFE%": t.mfe_pct, 
            "MAE%": t.mae_pct, 
            "Bars": t.duration, 
            "Exit Reason": str(t.exit_reason).replace("ExitReason.", ""), 
            "Regime": t.regime_at_entry,
            "PRT Accel": getattr(t, "prt_accel", 0),
            "CTS_Accel": getattr(t,"cts_accel_delta",0),
            "Base Bars": getattr(t, "bars_at_base", 0),
            "Tightness": getattr(t, "base_tightness", 0),
            "RW_10": getattr(t, "range_width_10", 0),
            "RW_63": getattr(t, "range_width_63", 0),
            "RW_252": getattr(t, "range_width_252", 0),
            "RDV+": getattr(t, "rdv_pos", 0)
        })
    df = pd.DataFrame(trade_dicts)
    
    out.write(f"\n--- {label} RESULTS ---\n")
    out.write(f"Trades: {len(df)} | Win Rate: {(df['PnL%']>0).mean()*100:.1f}% | Avg PnL: {df['PnL%'].mean():+.2f}%\n")
    out.write(f"Avg MFE: {df['MFE%'].mean():.2f}% | Avg MAE: {df['MAE%'].mean():.2f}% | Avg Duration: {df['Bars'].mean():.1f} bars\n\n")
    
    # Print the DataFrame with trade details
    out.write("--- TRADE LOG ---\n")
    out.write(tabulate(df.sort_values(by="PnL%", ascending=False), headers="keys", tablefmt="simple", showindex=False, floatfmt=".2f"))
    out.write("\n")
    
    reason_agg = df.groupby("Exit Reason").size().sort_values(ascending=False)
    out.write("\nExit Breakdown:\n" + reason_agg.to_string() + "\n")
    
    regime_agg = df.groupby("Regime").agg(count=("PnL%", "size"), win_rate=("PnL%", lambda x: (x > 0).mean() * 100), avg_pnl=("PnL%", "mean")).sort_values("avg_pnl", ascending=False)
    out.write("\nRegime Breakdown:\n" + tabulate(regime_agg, headers="keys", tablefmt="simple", floatfmt=".2f") + "\n")

def main():
    parser = argparse.ArgumentParser(description="Study: PSZ & CTS Slope Zero-Cross")
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--symbol", help="Run for a single symbol")
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD)")
    args = parser.parse_args()
    
    symbols = [args.symbol] if args.symbol else get_watchlist_symbols(args.watchlist)
    if not symbols: 
        print(f"No symbols found for watchlist {args.watchlist}")
        return
        
    out = io.StringIO()
    out.write(f"CTS Bull-Cross Study\n")
    out.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    out.write(f"Watchlist: {args.watchlist}\n\n")
    
    entry_cfg, exit_cfg = SavgolCTSEntryConfig(), SavgolCTSExitConfig()
    all_trades = []
    
    for sym in symbols:
        print(f"Analyzing {sym}...")
        try:
            engine = DivergenceEngine(sym)
            result = engine.run()
            trades = simulate_trades_study(sym, result.ledger, entry_cfg, exit_cfg)
            if args.start_date: 
                trades = [t for t in trades if t.entry_date >= args.start_date]
            all_trades.extend(trades)
        except Exception as e: 
            print(f"Error analyzing {sym}: {e}")
            
    summarize_study(all_trades, "FULL STUDY", out)
    
    report = out.getvalue()
    print(report)
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (OUTPUT_DIR / f"study_psz_cts_cross_{ts}.txt").write_text(report)
    
    if all_trades:
        pd.DataFrame([{
            "symbol": t.symbol, 
            "signal_date": getattr(t, "signal_date", ""),
            "entry_date": t.entry_date, 
            "exit_date": t.exit_date, 
            "pnl_pct": t.pnl_pct, 
            "mfe_pct": t.mfe_pct, 
            "mae_pct": t.mae_pct, 
            "duration": t.duration, 
            "exit_reason": str(t.exit_reason),
            "score": t.conviction_score,
            "regime": t.regime_at_entry,
            "psz_at_entry": t.psz_at_entry,
            "cts_at_signal": t.cts_at_signal,
            "slope_delta": getattr(t, "slope_delta", 0),
            "total_slope_delta": getattr(t, "total_slope_delta", 0),
            "cts_accel": getattr(t, "cts_accel", 0),
            "cts_accel_threshold": getattr(t, "cts_accel_threshold", 0)
        } for t in all_trades]).to_csv(OUTPUT_DIR / f"study_psz_cts_cross_{ts}.csv", index=False)

if __name__ == "__main__": 
    main()
