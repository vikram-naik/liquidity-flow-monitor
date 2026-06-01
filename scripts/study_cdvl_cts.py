#!/usr/bin/env python3
"""
Study for a new entry type based on CDVL and CTS indicators:
1) cdvl turning positive (crossover above 0)
2) cts should be negative and should be rising (1-bar check: cts < 0 and cts > prev_cts)

Exit is based on universal_cross exit mechanics.
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade
from src.trading.signals.savgol_cts import SavgolCTSExitConfig
from src.trading.signals.savgol_cts.exits.universal_cross import exit_universal_cross
from src.trading.signals.savgol_cts.exits.cwvap_guard import apply_cwvap_guard
from src.trading.signals.savgol_cts.state import SavgolCTSExitState
from src.trading.signals.enums import ExitReason
from src.database import DB_PATH

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

TRAIN_START = "2019-01-01"
TRAIN_END = "2023-12-31"
TEST_START = "2024-01-01"

SEP = "=" * 72
THIN_SEP = "-" * 72


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def get_watchlist_symbols(name: str) -> list[str]:
    db = sqlite3.connect(str(DB_PATH))
    row = db.execute("SELECT id FROM watchlists WHERE name = ?", (name,)).fetchone()
    if not row:
        avail = [r[0] for r in db.execute("SELECT name FROM watchlists ORDER BY name").fetchall()]
        db.close()
        print(f"Watchlist '{name}' not found. Available: {avail}")
        sys.exit(1)
    symbols = [
        r[0] for r in db.execute(
            "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY display_order",
            (row[0],),
        ).fetchall()
    ]
    db.close()
    return symbols


class CDVLCTSSignal:
    """Custom Signal for the study.
    
    Entry:
    1. CDVL turns positive (prev_cdvl <= 0 and cdvl > 0)
    2. CTS is negative and rising (cts < 0 and cts > prev_cts)
    
    Exit:
    Universal Cross exit mechanics.
    """

    def check_entry(
        self,
        row: dict,
        prev_row: dict,
        records: list[dict] | None = None,
        idx: int = 0,
    ) -> tuple[bool, dict]:
        cdvl = row.get("cdvl", np.nan)
        prev_cdvl = prev_row.get("cdvl", np.nan)
        cts = row.get("cts", np.nan)
        prev_cts = prev_row.get("cts", np.nan)
        cts_accel = row.get("cts_accel", np.nan)
        prev_cts_accel = prev_row.get("cts_accel", np.nan)
        cwc = row.get("cwc", np.nan)

        if (np.isnan(cdvl) or np.isnan(prev_cdvl) or np.isnan(cts) or np.isnan(prev_cts) or 
                np.isnan(cts_accel) or np.isnan(prev_cts_accel) or np.isnan(cwc)):
            return False, {"reason": "Missing data"}

        # 1) cdvl turning positive
        cdvl_turn_positive = (prev_cdvl <= 0.0 and cdvl > 0.0)

        # 2) cts negative and (rising or flat at absolute floor -1.0)
        cts_neg_and_rising_or_flat_floor = (cts < 0.0 and (cts > prev_cts or (cts == -1.0 and prev_cts == -1.0)))

        # 3) cts <= cts_buy_threshold (with special floor logic: if cts == -1.0, cts_buy_threshold must be == -1.0)
        cts_buy_threshold = row.get("cts_buy_threshold", -0.8)
        if cts == -1.0:
            cts_under_threshold = (cts_buy_threshold == -1.0)
        else:
            cts_under_threshold = (cts <= cts_buy_threshold)

        # 4) cts_accel is rising (1-bar check)
        accel_rising = (cts_accel > prev_cts_accel)

        # 5) cwc > 0.5
        cwc_coherent = (cwc > 0.5)

        # 6) cts_accel > cts_accel_threshold
        cts_accel_threshold = row.get("cts_accel_threshold", 0.0)
        accel_above_threshold = (cts_accel > cts_accel_threshold)

        if (cdvl_turn_positive and cts_neg_and_rising_or_flat_floor and 
                cts_under_threshold and accel_rising and cwc_coherent and accel_above_threshold):
            return True, {
                "reason": "CDVL turn positive, CTS neg rising/flat-floor, CTS <= buy threshold, CTS Accel rising & above threshold, and CWC > 0.5",
                "entry_tag": "CDVL_CTS_Study",
                "score": 80,
            }

        return False, {"reason": "Conditions not met"}

    def check_exit(
        self,
        row: dict,
        prev_row: dict,
        trade: Trade,
        peak_close: float,
        bars_held: int,
        delivery_bad_count: int,
        cwvap_values: list[float],
        cfg: SavgolCTSExitConfig,
        records: list[dict] | None = None,
        idx: int = 0,
    ) -> tuple[str | None, int]:
        st = SavgolCTSExitState.from_int(delivery_bad_count)
        tag = trade.entry_tag if trade is not None else ""
        close = row.get("close", np.nan)
        cwvap = row.get("cwvap", np.nan)

        if not np.isnan(close) and not np.isnan(cwvap) and close > cwvap:
            st.price_above_cwvap = True

        # Route through Universal Cross exit logic
        exit_reason, state_val = exit_universal_cross(
            row, prev_row, trade, peak_close, bars_held, st.to_int(),
            cfg.universal_cross, records, idx
        )
        st = SavgolCTSExitState.from_int(state_val)

        # Apply common CWVAP guard logic
        final_reason, st_val = apply_cwvap_guard(
            row, trade, exit_reason, st.to_int(),
            cfg, records, idx, tag
        )

        if final_reason:
            return str(final_reason), st_val

        return None, st_val


def simulate_trades_custom(
    ticker: str, df: pd.DataFrame,
    exit_cfg: SavgolCTSExitConfig, signal: CDVLCTSSignal
) -> list[Trade]:
    """Walk through ledger bar-by-bar, enter and exit trades using custom rules."""
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
    pending_cdvl_exit: ExitReason | str | None = None

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
            pending_cdvl_exit = None
            continue

        if in_trade:
            # Hard stop of 10% based on daily low
            low = row.get("low", close)
            if not np.isnan(low):
                drawdown_pct = (low / trade.entry_price - 1) * 100
                if drawdown_pct <= -10.0:
                    stop_price = trade.entry_price * 0.90
                    exit_price = min(stop_price, low)
                    trade.exit_date = str(row.get("date", ""))[:10]
                    trade.exit_price = round(exit_price, 2)
                    trade.exit_reason = ExitReason.HARD_STOP
                    trade.pnl_pct = round((exit_price / trade.entry_price - 1) * 100, 2)
                    trade.duration = i - trade.entry_idx
                    trade.mfe_pct = round(trade.mfe_pct, 2)
                    trade.mae_pct = round(max(trade.mae_pct, abs(drawdown_pct)), 2)
                    trades.append(trade)
                    in_trade = False
                    trade = None
                    delivery_bad_count = 0
                    pending_exit_reason = None
                    pending_cdvl_exit = None
                    continue

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

            # Defer exit if cdvl is still positive, trailing until cdvl becomes non-positive (<= 0)
            exit_signal = reason if reason else pending_cdvl_exit
            if exit_signal:
                cdvl = row.get("cdvl", 0.0)
                if cdvl > 0.0:
                    pending_cdvl_exit = exit_signal
                else:
                    pending_exit_reason = exit_signal
                    pending_cdvl_exit = None

        elif pending_signal is not None:
            sig = pending_signal
            pending_signal = None
            atr = row.get("atr_20", 0)
            if atr <= 0 or np.isnan(atr):
                continue
            psz_now = row.get("price_slope_z", np.nan)
            trade = Trade(
                symbol=ticker,
                entry_date=str(prev.get("date", ""))[:10], # Signal Date (T)
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
            qualifies, fdetails = signal.check_entry(row, prev, records, i)
            if qualifies:
                pending_signal = {"details": fdetails}

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


def compute_profit_factor(trades: list[Trade]) -> float:
    gross_win = sum(t.pnl_pct for t in trades if t.pnl_pct > 0)
    gross_loss = abs(sum(t.pnl_pct for t in trades if t.pnl_pct <= 0))
    if gross_loss == 0:
        return float("inf")
    return gross_win / gross_loss


def compute_expectancy(trades: list[Trade]) -> float:
    if not trades:
        return 0.0
    winners = [t.pnl_pct for t in trades if t.pnl_pct > 0]
    losers = [t.pnl_pct for t in trades if t.pnl_pct <= 0]
    n = len(trades)
    avg_win = np.mean(winners) if winners else 0.0
    avg_loss = abs(np.mean(losers)) if losers else 0.0
    wr = len(winners) / n
    lr = len(losers) / n
    return avg_win * wr - avg_loss * lr


def run_period(symbols: list[str], start: str, end: str,
               exit_cfg: SavgolCTSExitConfig, label: str, signal: CDVLCTSSignal) -> list[Trade]:
    all_trades = []
    failed = 0
    for sym in symbols:
        try:
            # Always run with full history for warm-up
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            result = engine.run()
            trades = simulate_trades_custom(sym, result.ledger, exit_cfg, signal)
            period_trades = [t for t in trades if start <= str(t.entry_date) <= end]
            all_trades.extend(period_trades)
        except Exception as e:
            failed += 1
            print(f"Failed {sym}: {repr(e)}")

    traded = len(set(t.symbol for t in all_trades))
    print(f"  {label}: {len(all_trades)} trades across {traded}/{len(symbols)} symbols | {failed} failed")
    return all_trades


def summarize(trades: list[Trade], label: str, period_start: str, period_end: str, out: io.StringIO) -> dict:
    def w(line: str = ""):
        out.write(line + "\n")

    if not trades:
        w(f"\n{label}: No trades.")
        return {}

    df = pd.DataFrame([{
        "symbol": t.symbol,
        "pnl": t.pnl_pct, "mfe": t.mfe_pct, "mae": t.mae_pct,
        "bars": t.duration,
        "reason": t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason),
        "entry_tag": t.entry_tag,
        "entry_date": t.entry_date, "exit_date": t.exit_date,
    } for t in trades])

    total = len(df)
    winners = (df["pnl"] > 0).sum()
    losers = total - winners
    win_rate = winners / total * 100
    avg_pnl = df["pnl"].mean()
    avg_win = df.loc[df["pnl"] > 0, "pnl"].mean() if winners > 0 else 0
    avg_loss = df.loc[df["pnl"] <= 0, "pnl"].mean() if losers > 0 else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
    profit_factor = compute_profit_factor(trades)
    expectancy = compute_expectancy(trades)
    median_pnl = df["pnl"].median()
    symbols_traded = df["symbol"].nunique()

    w(f"\n{SEP}")
    w(f"  {label} RESULTS  ({period_start} to {period_end})")
    w(SEP)
    w()
    w(f"  {'Metric':<22} {'Value':>10}")
    w(f"  {THIN_SEP[:34]}")
    w(f"  {'Trades':<22} {total:>10}")
    w(f"  {'Symbols traded':<22} {symbols_traded:>10}")
    w(f"  {'Winners / Losers':<22} {f'{winners} / {losers}':>10}")
    w(f"  {'Win rate':<22} {win_rate:>9.1f}%")
    w(f"  {'Avg P&L':<22} {avg_pnl:>+9.2f}%")
    w(f"  {'Median P&L':<22} {median_pnl:>+9.2f}%")
    w(f"  {'Avg winner':<22} {avg_win:>+9.2f}%")
    w(f"  {'Avg loser':<22} {avg_loss:>+9.2f}%")
    w(f"  {'Payoff ratio':<22} {payoff:>9.2f}x")
    w(f"  {'Profit factor':<22} {profit_factor:>9.2f}")
    w(f"  {'Expectancy / trade':<22} {expectancy:>+9.2f}%")
    w(f"  {'Avg MFE':<22} {df['mfe'].mean():>+9.2f}%")
    w(f"  {'Avg MAE':<22} {df['mae'].mean():>9.2f}%")
    w(f"  {'Avg duration':<22} {df['bars'].mean():>8.1f} bars")

    # Exit breakdown
    reason_agg = (
        df.groupby("reason")
        .agg(count=("pnl", "size"), avg_pnl=("pnl", "mean"),
             win_rate=("pnl", lambda x: round((x > 0).mean() * 100, 1)))
        .reset_index()
        .sort_values("count", ascending=False)
        .round(2)
    )
    w(f"\n  Exit Breakdown:")
    w(tabulate(reason_agg, headers=["Exit Reason", "Count", "Avg P&L%", "Win%"],
               tablefmt="simple", floatfmt=".2f", showindex=False))

    # Top / bottom symbols
    sym_agg = (
        df.groupby("symbol")
        .agg(count=("pnl", "size"), total_pnl=("pnl", "sum"), avg_pnl=("pnl", "mean"),
             win_rate=("pnl", lambda x: round((x > 0).mean() * 100, 1)))
        .reset_index()
        .round(2)
    )
    top5 = sym_agg.nlargest(5, "total_pnl")
    bot5 = sym_agg.nsmallest(5, "total_pnl")
    w(f"\n  Top 5 Symbols (by total P&L%):")
    w(tabulate(top5, headers=["Symbol", "Trades", "Total P&L%", "Avg P&L%", "Win%"],
               tablefmt="simple", floatfmt=".2f", showindex=False))
    w(f"\n  Bottom 5 Symbols (by total P&L%):")
    w(tabulate(bot5, headers=["Symbol", "Trades", "Total P&L%", "Avg P&L%", "Win%"],
               tablefmt="simple", floatfmt=".2f", showindex=False))

    return {
        "trades": total, "win_rate": round(win_rate, 1),
        "avg_pnl": round(avg_pnl, 2), "payoff": round(payoff, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy": round(expectancy, 2),
        "avg_mfe": round(df["mfe"].mean(), 2), "avg_mae": round(df["mae"].mean(), 2),
        "median_pnl": round(median_pnl, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="CDVL/CTS Entry Study")
    parser.add_argument("--watchlist", default="NSE F&O")
    args = parser.parse_args()

    test_end = today_str()
    symbols = get_watchlist_symbols(args.watchlist)

    out = io.StringIO()

    def w(line: str = ""):
        out.write(line + "\n")

    w(SEP)
    w(f"  CDVL / CTS STUDY BACKTEST REPORT")
    w(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w(SEP)
    w()
    w(f"  Watchlist:  {args.watchlist} ({len(symbols)} symbols)")
    w(f"  Train:      {TRAIN_START} to {TRAIN_END}")
    w(f"  Test:       {TEST_START} to {test_end}")
    w()

    exit_cfg = SavgolCTSExitConfig()
    signal = CDVLCTSSignal()

    print(f"Running TRAIN period ({TRAIN_START} to {TRAIN_END})...", flush=True)
    train_trades = run_period(symbols, TRAIN_START, TRAIN_END, exit_cfg, "TRAIN", signal)
    print(f"Running TEST period ({TEST_START} to {test_end})...", flush=True)
    test_trades = run_period(symbols, TEST_START, test_end, exit_cfg, "TEST", signal)

    train_stats = summarize(train_trades, "TRAIN", TRAIN_START, TRAIN_END, out)
    test_stats = summarize(test_trades, "TEST", TEST_START, test_end, out)

    if train_stats and test_stats:
        w(f"\n{SEP}")
        w("  PARAMETER STABILITY  (Test vs Train)")
        w(SEP)
        w()
        w(f"  {'Metric':<22} {'Train':>10} {'Test':>10} {'Delta':>10}")
        w(f"  {THIN_SEP[:54]}")
        compare_keys = [
            ("trades",         "",  0),
            ("win_rate",       "%", 1),
            ("avg_pnl",        "%", 2),
            ("median_pnl",     "%", 2),
            ("payoff",         "x", 2),
            ("profit_factor",  "",  2),
            ("expectancy",     "%", 2),
            ("avg_mfe",        "%", 2),
            ("avg_mae",        "%", 2),
        ]
        for key, suffix, dec in compare_keys:
            t = train_stats.get(key, 0)
            s = test_stats.get(key, 0)
            delta = s - t
            fmt = f".{dec}f"
            w(f"  {key:<22} {t:>9{fmt}}{suffix} {s:>9{fmt}}{suffix} {delta:>+9{fmt}}{suffix}")

    w(f"\n{SEP}")

    report = out.getvalue()
    print(report)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%d-%b-%Y_%H:%M")
    sanitized_wl = args.watchlist.replace(" ", "_")
    filename = f"cdvl_cts_study_{sanitized_wl}_{ts}.txt"
    outpath = OUTPUT_DIR / filename
    outpath.write_text(report)
    print(f"Report saved to {outpath}")


if __name__ == "__main__":
    main()
