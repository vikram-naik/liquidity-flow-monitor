"""
CTS Bull Study
================================
Evolving a new entry path: 
1. cts > -0.7 and prev_cts < prev_bt and cts > bt 
2. cts_slope is rising.
3. cts_accel is rising.
3. psz_v is rising for 3-bars (v0 > v1 > v2).

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
            print(f"Exit : dt: {row.get("date")}, {pending_exit_reason}, {pnl_pct} \n")
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
                    elif bars_held >= 30:
                        pending_exit_reason = ExitReason.RECLAIM_TIMEOUT
                else:
                    # Phase 2: CTS trailing
                    if not np.isnan(cts) and not np.isnan(cts_st) and cts >= cts_st:
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
            trade.slope_delta = sig['details']['cts_slope_delta']
            trade.cts_accel = sig['details']['cts_accel']
            trade.cts_accel_threshold = sig['details']['cts_accel_threshold']
            peak_close = close
            state_val = 0
            in_trade = True

        else:
            # ENTRY CRITERIA
            cts_now = row.get("cts", 0)
            bt_now = row.get("cts_buy_threshold", 0)
            cts_1 = records[i-1].get("cts", 0)
            bt_1 = records[i-1].get("cts_buy_threshold", 0)

            psz_now = row.get("price_slope_z", 0)
            psz_1 = records[i-1].get("price_slope_z", 0)
            psz_2 = records[i-2].get("price_slope_z", 0)
            psz_3 = records[i-3].get("price_slope_z", 0)
            
            v_now = row.get("psz_v", 0)
            v_1 = records[i-1].get("psz_v", 0)
            v_2 = records[i-2].get("psz_v", 0)
            v_3 = records[i-3].get("psz_v", 0)
            
            cts_slope_now = row.get("cts_slope", 0)
            cts_slope_1 = records[i-1].get("cts_slope", 0)
            cts_slope_2 = records[i-2].get("cts_slope", 0)
            cts_slope_3 = records[i-3].get("cts_slope", 0)
            
            cts_accel_now = row.get("cts_accel", 0)
            cts_accel_1 = records[i-1].get("cts_accel", 0)
            cts_accel_2 = records[i-2].get("cts_accel", 0)
            cts_accel_3 = records[i-3].get("cts_accel", 0)

            v_now = row.get("psz_v", 0)
            v_1 = records[i-1].get("psz_v", 0)
            v_2 = records[i-2].get("psz_v", 0)
            v_3 = records[i-3].get("psz_v", 0)

            cwvap_now = row.get("cwvap", 0)
            regime = row.get("regime", "")
            regimes_not_allowed = ["notrend"]
            
            # 1. cts > -0.7 and prev_cts < prev_bt and cts > bt and bt > -0.8
            # 2. cts_slope is rising. now > 1 > 2 
            # 3. cts_accel is rising. now > 1 > 2 > 3 and (cts_accel - cts_accel_1)>= 0.01
            # 3. psz_v is rising for 3-bars (v0 > v1 > v2 > v3).
            # 3.1 psz_v > 0
            # 5. green cancel ( close > prev_close)
            # 6. regime not in ['notrend']
            # 7. if in downtrend / trainsition: close < cwvap.

            is_cts_bull_cross = -0.3 > cts_now > -0.8 and cts_1 < bt_1 and cts_now > bt_now and - 0.3 > bt_now > -0.75
            is_cts_slope_rising = cts_slope_now > cts_slope_1 >  cts_slope_2 
            is_psz_v_rising = v_now > v_1 > v_2 > v_3 and v_now > 0
            is_cts_accel_rising = cts_accel_now > cts_accel_1 > cts_accel_2 > cts_accel_3 and (cts_accel_now - cts_accel_1) >= 0.003    
            # is_candle_green = close > prev_close
            is_candle_green = True
            is_regime_allowed = regime not in regimes_not_allowed
            is_regime_price = False if regime in ["downtrend","transition"] and close > cwvap_now else True

            is_ath = row.get("is_ath", 0)
            # 10, 22, 63, 252
            rp_10 = row.get("range_pos_10",0)
            rp_22 = row.get("range_pos_22",0)
            rp_63 = row.get("range_pos_63",0)
            rp_252 = row.get("range_pos_252",0)

            # Distribution guard v3:                                                                                                                                                                                                            
            # a stock is distributing from a high when the 10-day range has collapsed (rp_10 < 0.50) 
            # but the monthly range hasn't corrected yet (rp_22 > 0.50) 
            # and the stock is elevated in its annual range (rp_252 > 0.70). 
            # That's a fresh selloff with no tested support — the correction hasn't matured. 
            # Once rp_22 drops below 0.50, supply has been absorbed and mean-reversion can work.                                                                                                                                                                               
            is_distributing = rp_10 < 0.50 and rp_22 > 0.50 and rp_252 > 0.70
            # Ceiling guard: near quarterly high but NOT in strong annual uptrend
            # → hitting resistance within a range, not trending through
            # is_at_ceiling = rp_63 >= 0.90 and rp_252 < 0.75
            # Overbought: all timeframes stretched, no upside room                                                                                                                                                                              
            is_overbought = rp_10 > 0.90 and rp_22 > 0.90 and rp_63 > 0.90 and rp_252 > 0.90 

            # 1. The Macro Anchor (Strong Long-Term Trend)
            macro_bull = rp_252 > 0.75

            # 2. The Multi-Week Discount (The Cooling Off Period)
            # We want it cheap, but not in absolute free-fall capitulation (< 0.15)
            medium_pullback = rp_22 > 0.15 and rp_22 < 0.70 

            # 3. The Short-Term Floor (Stabilization)
            # The bleeding has stopped, but it hasn't run away yet
            short_stabilizing = rp_10 > 0.25 and rp_10 <= 0.45

            # --- THE ENTRY SIGNAL ---
            # is_sweet_spot_entry = macro_bull and medium_pullback and short_stabilizing
            is_sweet_spot_entry = True

            if is_cts_bull_cross and \
                is_cts_slope_rising and \
                is_psz_v_rising and \
                is_cts_accel_rising and \
                is_candle_green and \
                is_regime_allowed and \
                is_regime_price and \
                not is_overbought and \
                not is_distributing and \
                is_sweet_spot_entry:
                print(f"Entry: dt: {row.get("date")}, is_ath:{is_ath}, rp_10:{rp_10:.2f}, rp_22:{rp_22:.2f}, rp_63:{rp_63:.2f}, rp_252:{rp_252:.2f}")
                # Scoring Logic (Experimental)
                score = 10
                if v_now > 0.1: score += 5
                if cts_now > 0: score += 5
                
                pending_signal = {
                    "details": {
                        "signal_date": str(row.get("date", ""))[:10],
                        "entry_tag": "CTS_BULL_CROSS",
                        "score": score,
                        "soft_filters": 10,
                        "psz_now": psz_now,
                        "cts_now": cts_now,
                        "regime": row.get("regime", ""),
                        "cts_slope_delta": cts_slope_now - cts_slope_1,
                        "cts_accel": row.get("cts_accel", 0),
                        "cts_accel_threshold": row.get("cts_accel_threshold", 0)
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
    df = pd.DataFrame([{
        "symbol": t.symbol, 
        "signal_date": getattr(t, "signal_date", ""),
        "pnl": t.pnl_pct, 
        "mfe": t.mfe_pct, 
        "mae": t.mae_pct, 
        "bars": t.duration, 
        "score": t.conviction_score, 
        "reason": str(t.exit_reason), 
        "entry_date": t.entry_date, 
        "exit_date": t.exit_date,
        "regime": t.regime_at_entry,
        "slope_delta": getattr(t, "slope_delta", 0),
        "total_slope_delta": getattr(t, "total_slope_delta", 0)
    } for t in trades])
    
    out.write(f"\n--- {label} RESULTS ---\n")
    out.write(f"Trades: {len(df)} | Win Rate: {(df['pnl']>0).mean()*100:.1f}% | Avg PnL: {df['pnl'].mean():+.2f}%\n")
    out.write(f"Avg MFE: {df['mfe'].mean():.2f}% | Avg MAE: {df['mae'].mean():.2f}% | Avg Duration: {df['bars'].mean():.1f} bars\n")
    
    reason_agg = df.groupby("reason").size().sort_values(ascending=False)
    out.write("\nExit Breakdown:\n" + reason_agg.to_string() + "\n")
    
    regime_agg = df.groupby("regime").agg(count=("pnl", "size"), win_rate=("pnl", lambda x: (x > 0).mean() * 100), avg_pnl=("pnl", "mean")).sort_values("avg_pnl", ascending=False)
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
