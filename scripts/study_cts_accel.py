
"""
CTS Acceleration Cross Study
============================
Evolving a new entry path: 
1. cts_slope crossing zero upward (momentum inflection).
2. cts_accel is rising (current > previous).
3. cts_accel > cts_accel_threshold (adaptive strength gate).
4. psz_now in (0.0, 0.2] (fresh price turn).
5. cts_now > 0 (institutional backing).

Scoring Logic (Max 18 baseline + 2 velocity):
- Accel Trend: A0>A1(+1), A1>A2(+2), A2>A3(+3)
- Slope Trend: S0>S1(+1), S1>S2(+2), S2>S3(+3)
- Stability Bonus: Delta < Threshold/0.1 (+2 for t-1, +1 for t-2)
- Velocity Turn: V0>V1 (+2) or V0<=V1 (-2)

Accumulation Bonus:
- Flat-bars: Count of bars where abs(slope) < 0.05 and abs(accel) < threshold.
- Reward: +2 per bar (Up to +10)

Chain Analysis:
- chain_length: Bars of continuous A_n > A_{n+1} AND V_n > V_{n+1}.
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
from src.trading.signals.enums import ExitReason, EntryTag
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig, SavgolCTSSignal
from src.trading.signals.savgol_cts.exits.cwvap_guard import apply_cwvap_guard
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
    entry_cfg: SavgolCTSEntryConfig, exit_cfg: SavgolCTSExitConfig, signal: SavgolCTSSignal
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
    pending_exit_reason: ExitReason | str | None = None

    for i in range(10, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue

        cw = row.get("cwvap", np.nan)
        cwvap_values.append(cw)

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
            if -mae > trade.mae_pct: trade.mae_pct = -mae

            st = SavgolCTSExitState.from_int(delivery_bad_count)
            cwvap = row.get("cwvap", np.nan)
            if not st.price_above_cwvap:
                if not np.isnan(close) and not np.isnan(cwvap) and close > cwvap:
                    st.price_above_cwvap = True
                pnl = (close / trade.entry_price - 1) * 100
                # if pnl <= -8.0:
                #     reason, delivery_bad_count = ExitReason.HARD_STOP, st.to_int()
                if (i - trade.entry_idx) >= 20:
                    reason, delivery_bad_count = ExitReason.TIME_DECAY, st.to_int()
                else:
                    reason, delivery_bad_count = None, st.to_int()
            else:
                proposed_res = None
                if not np.isnan(close) and not np.isnan(cwvap) and close < cwvap:
                    proposed_res = ExitReason.CWVAP_LOST
                reason, delivery_bad_count = apply_cwvap_guard(
                    row, trade, proposed_res, st.to_int(),
                    exit_cfg, records, i
                )
            if reason: pending_exit_reason = reason

        elif pending_signal is not None:
            sig = pending_signal
            pending_signal = None
            atr = row.get("atr_20", 0)
            if atr <= 0 or np.isnan(atr): continue
            trade = Trade(
                symbol=ticker,
                entry_date=str(row.get("date", ""))[:10],
                entry_price=close,
                entry_idx=i,
                atr_at_entry=atr,
                soft_filters_passed=1,
                entry_tag=sig["details"]["entry_tag"],
                conviction_score=sig["details"]["score"],
            )
            trade.psz_at_entry = sig['details']['psz_at_entry']
            trade.cts_at_signal = sig['details']['cts_at_signal']
            trade.soft_filters_passed = sig['details']['cts_slope']
            trade.atr_at_entry = sig['details']['cwvap_dist']
            trade.regime_at_entry = f"{sig['details']['grade']}|L{sig['details']['chain_len']}|F{sig['details']['flat_bars']}|CWC{sig['details']['cwc_slope']:.4f}|V{sig['details']['psz_v']:.4f}"
            peak_close = close
            delivery_bad_count = 0
            in_trade = True

        else:
            s_now, s_prev = row.get("cts_slope", 0), prev.get("cts_slope", 0)
            a_now, a_prev = row.get("cts_accel", 0), prev.get("cts_accel", 0)
            at = row.get("cts_accel_threshold", 0)
            psz_now = row.get("price_slope_z", 0)
            cts_now = row.get("cts", 0)
            v_now, v_prev = row.get("psz_v", 0), prev.get("psz_v", 0)
            cwvap = row.get("cwvap", 0)
            cwvap_dist = (close - cwvap) / cwvap * 100.0 if cwvap > 0 else 0
            cwc_slope = row.get("cwc_slope", 0)
            
            if all([s_now > 0 and s_prev <= 0, a_now > a_prev, a_now > at, 0.0 < psz_now <= 0.2, 0.0 < cts_now <= 0.5, cwvap_dist <= 8.0, cwc_slope > 0]):
                # 1. Accel Scoring
                a_1, a_2, a_3 = records[i-1].get("cts_accel", 0), records[i-2].get("cts_accel", 0), records[i-3].get("cts_accel", 0)
                accel_score = 0
                if a_now > a_1: 
                    accel_score += 1
                    # --- CLIMAX PENALTY vs STABILITY REWARD ---
                    delta = a_now - a_1
                    if delta < at: 
                        accel_score += 2 # Tight/Stable
                    elif delta > (2 * at):
                        accel_score -= 5 # CLIMAX (Likely falling knife)
                if a_1 > a_2:   
                    accel_score += 2
                    if (a_1 - a_2) < at: accel_score += 1
                if a_2 > a_3: accel_score += 3
                
                # 2. Slope Scoring
                s_1, s_2, s_3 = records[i-1].get("cts_slope", 0), records[i-2].get("cts_slope", 0), records[i-3].get("cts_slope", 0)
                slope_score = 0
                if s_now > s_1: 
                    slope_score += 1
                    if (s_now - s_1) < 0.1: slope_score += 2
                if s_1 > s_2:   
                    slope_score += 2
                    if (s_1 - s_2) < 0.1: slope_score += 1
                if s_2 > s_3: slope_score += 3
                
                # 3. PSZ Velocity Trend (3 bars)
                v_1, v_2 = records[i-1].get("psz_v", 0), records[i-2].get("psz_v", 0)
                v_score = 0
                if v_now > v_1: 
                    v_score += 1
                    if (v_now - v_1) < 0.05: v_score += 1 # Tight turn
                if v_1 > v_2: v_score += 2
                
                total_score = accel_score + slope_score + v_score
                
                # Accumulation Bonus (Cap at 4 bars)
                tight_range_slope, tight_range_accel = 0.05, (at if at > 0 else 0.01)
                flat_bars = 0
                for j in range(1, 6):
                    if i-j < 0: break
                    if abs(records[i-j].get("cts_slope", 0)) <= tight_range_slope and abs(records[i-j].get("cts_accel", 0)) <= tight_range_accel:
                        flat_bars += 1
                    else: break
                total_score += (min(flat_bars, 4) * 2)
                
                # Early Stage Institutional Bonus (CTS in 0.0 to 0.25)
                if 0.0 < cts_now <= 0.25: total_score += 4
                
                # Grade (Max 18 trend + 6 stability + 4 velocity + 8 accumulation + 4 CTS bonus = 40)
                if total_score >= 30:   grade = "ELITE"
                elif total_score >= 20: grade = "STRONG"
                elif total_score >= 12: grade = "MODERATE"
                else:                  grade = "WEAK"

                chain_len = 1
                for j in range(1, 10):
                    if i-j-1 < 0: break
                    if records[i-j].get("cts_accel", 0) > records[i-j-1].get("cts_accel", 0) and records[i-j].get("psz_v", 0) > records[i-j-1].get("psz_v", 0):
                        chain_len += 1
                    else: break
                
                pending_signal = {"details": {"entry_tag": f"ACCEL_{grade}", "score": total_score, "grade": grade, "chain_len": chain_len, "flat_bars": flat_bars, "psz_at_entry": psz_now, "cts_at_signal": cts_now, "cts_slope": s_now, "cwvap_dist": cwvap_dist, "cwc_slope": row.get("cwc_slope", 0), "psz_v": v_now}}

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
        "pnl": t.pnl_pct, 
        "mfe": t.mfe_pct, 
        "mae": t.mae_pct, 
        "bars": t.duration, 
        "grade": t.regime_at_entry.split("|")[0], 
        "chain_len": int(t.regime_at_entry.split("|L")[1].split("|")[0]), 
        "flat_bars": int(t.regime_at_entry.split("|F")[1].split("|")[0]), 
        "score": t.conviction_score, 
        "reason": str(t.exit_reason), 
        "entry_tag": t.entry_tag, 
        "entry_date": t.entry_date, 
        "exit_date": t.exit_date
    } for t in trades])
    out.write(f"\n--- {label} RESULTS ---\nTrades: {len(df)} | Win Rate: {(df['pnl']>0).mean()*100:.1f}% | Avg PnL: {df['pnl'].mean():+.2f}%\n")
    tag_agg = df.groupby("grade").agg(count=("pnl", "size"), win_rate=("pnl", lambda x: (x > 0).mean() * 100), avg_pnl=("pnl", "mean")).sort_values("avg_pnl", ascending=False)
    out.write("\nGrade Breakdown:\n" + tabulate(tag_agg, headers="keys", tablefmt="simple", floatfmt=".2f") + "\n")
    flat_agg = df.groupby("flat_bars").agg(count=("pnl", "size"), win_rate=("pnl", lambda x: (x > 0).mean() * 100), avg_pnl=("pnl", "mean")).sort_values("flat_bars")
    out.write("\nAccumulation Breakdown:\n" + tabulate(flat_agg, headers="keys", tablefmt="simple", floatfmt=".2f") + "\n")
    chain_agg = df.groupby("chain_len").agg(count=("pnl", "size"), win_rate=("pnl", lambda x: (x > 0).mean() * 100), avg_pnl=("pnl", "mean")).sort_values("chain_len")
    out.write("\nChain Length Breakdown:\n" + tabulate(chain_agg, headers="keys", tablefmt="simple", floatfmt=".2f") + "\n")
    reason_agg = df.groupby("reason").size().sort_values(ascending=False)
    out.write("\nExit Breakdown:\n" + reason_agg.to_string() + "\n")

def main():
    parser = argparse.ArgumentParser(description="Study: CTS Acceleration Cross")
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--symbol", help="Run for a single symbol")
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD)")
    args = parser.parse_args()
    symbols = [args.symbol] if args.symbol else get_watchlist_symbols(args.watchlist)
    if not symbols: return
    out = io.StringIO()
    out.write(f"CTS Acceleration Cross Study (Stabilized Velocity)\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\nWatchlist: {args.watchlist}\n\n")
    entry_cfg, exit_cfg, signal = SavgolCTSEntryConfig(), SavgolCTSExitConfig(), SavgolCTSSignal()
    all_trades = []
    for sym in symbols:
        print(f"Analyzing {sym}...")
        try:
            engine = DivergenceEngine(sym)
            result = engine.run()
            trades = simulate_trades_study(sym, result.ledger, entry_cfg, exit_cfg, signal)
            if args.start_date: trades = [t for t in trades if t.entry_date >= args.start_date]
            all_trades.extend(trades)
        except Exception as e: print(f"Error analyzing {sym}: {e}")
    summarize_study(all_trades, "FULL STUDY", out)
    
    # Gated Study: Score >= 20
    gated_trades = [t for t in all_trades if t.conviction_score >= 20]
    summarize_study(gated_trades, "SCORE >= 20 GATE", out)

    # Gated Study: Score >= 20 AND chain_len >= 3
    gated_trades_v2 = [t for t in all_trades if t.conviction_score >= 20 and int(t.regime_at_entry.split("|L")[1].split("|")[0]) >= 3]
    summarize_study(gated_trades_v2, "SCORE >= 20 + CHAIN >= 3 GATE", out)

    # Gated Study: Score >= 20 AND flat_bars < 5
    gated_trades_v3 = [t for t in all_trades if t.conviction_score >= 20 and int(t.regime_at_entry.split("|F")[1].split("|")[0]) < 5]
    summarize_study(gated_trades_v3, "SCORE >= 20 + FLAT_BARS < 5 GATE", out)

    # Sub-Path Study: ACCEL_BOOM (Score >= 20, Flat Bars >= 4)
    boom_trades = [t for t in all_trades if t.conviction_score >= 20 and int(t.regime_at_entry.split("|F")[1].split("|")[0]) >= 4]
    summarize_study(boom_trades, "ACCEL_BOOM (SCORE >= 20, FLAT >= 4)", out)

    # Sub-Path Study: ACCEL_TREND (Score >= 20, Flat < 4, Chain >= 3)
    trend_trades = [t for t in all_trades if t.conviction_score >= 20 and int(t.regime_at_entry.split("|F")[1].split("|")[0]) < 4 and int(t.regime_at_entry.split("|L")[1].split("|")[0]) >= 3]
    summarize_study(trend_trades, "ACCEL_TREND (SCORE >= 20, FLAT < 4, CHAIN >= 3)", out)

    # FINAL PRODUCTION GATE: (BOOM OR TREND) AND Score < 30
    prod_trades = []
    for t in all_trades:
        score = t.conviction_score
        flat = int(t.regime_at_entry.split("|F")[1].split("|")[0])
        chain = int(t.regime_at_entry.split("|L")[1].split("|")[0])

        is_boom = (score >= 20 and flat >= 4)
        is_trend = (score >= 20 and flat < 4 and chain >= 3)

        if (is_boom or is_trend) and score < 30:
            prod_trades.append(t)

    summarize_study(prod_trades, "FINAL PRODUCTION GATE (BOOM/TREND, EXCL ELITE)", out)
    report = out.getvalue()
    print(report)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (OUTPUT_DIR / f"study_accel_cross_{ts}.txt").write_text(report)
    if all_trades:
        pd.DataFrame([{
            "symbol": t.symbol, 
            "entry_date": t.entry_date, 
            "exit_date": t.exit_date, 
            "grade": t.regime_at_entry.split("|")[0], 
            "chain_len": int(t.regime_at_entry.split("|L")[1].split("|")[0]), 
            "flat_bars": int(t.regime_at_entry.split("|F")[1].split("|")[0]), 
            "cwc_slope": float(t.regime_at_entry.split("|CWC")[1].split("|")[0]),
            "psz_v": float(t.regime_at_entry.split("|V")[1]),
            "score": t.conviction_score, 
            "psz_at_entry": t.psz_at_entry, 
            "cts_at_signal": t.cts_at_signal, 
            "cts_slope": t.soft_filters_passed, 
            "cwvap_dist": t.atr_at_entry, 
            "pnl_pct": t.pnl_pct, 
            "mfe_pct": t.mfe_pct, 
            "mae_pct": t.mae_pct, 
            "duration": t.duration, 
            "exit_reason": str(t.exit_reason)
        } for t in all_trades]).to_csv(OUTPUT_DIR / f"study_accel_cross_{ts}.csv", index=False)

if __name__ == "__main__": main()
