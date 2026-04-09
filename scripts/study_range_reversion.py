"""
Range Reversion Study
================================
Mean-reversion strategy using price range position features.

Entry (Oversold):
1. range_pos_252 < 0.25 — near 52-week low
2. range_pos_63 < 0.30 — quarterly range beaten down
3. range_pos_10 inflecting — short-term turning (current > prev)
4. Green candle — close > prev_close (not catching a falling knife)
5. bars_at_base >= 5 — sat in oversold territory for 5+ bars (base formed)
6. range_width_10 < 7.0% — 10-day range is narrow (flat rectangle, not falling)
7. NOT (cts_accel < 0 AND falling) — institutional selling not accelerating

Exit (PSZ Zero-Cross Cycle):
1. Wait phase: after entry, wait up to PSZ_PATIENCE bars for PSZ > 0.
   If PSZ doesn't cross zero within patience window → dead-trade abort.
2. Trail phase: once PSZ > 0, hold. Exit when PSZ drops back <= 0.
3. Hard stop: -8% (always active).

EOD-lag: signal on bar i, trade opens bar i+1 at close.
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.base import Trade
from src.trading.signals.enums import ExitReason

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


PSZ_PATIENCE = 8  # max bars to wait for PSZ to cross above zero


def simulate_trades(ticker: str, df: pd.DataFrame) -> list[Trade]:
    records = df.to_dict("records")
    n = len(records)
    trades = []
    in_trade = False
    trade = None
    psz_crossed_zero = False  # Phase tracking: False=wait, True=trail
    pending_signal: dict | None = None
    pending_exit_reason: ExitReason | str | None = None

    for i in range(10, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        prev_close = prev.get("close", np.nan)
        if np.isnan(close):
            continue

        # --- Pending exit execution (EOD-lag) ---
        if pending_exit_reason is not None:
            open_price = row.get("open", np.nan)
            exit_price = open_price if not np.isnan(open_price) else close
            pnl_pct = round((exit_price / trade.entry_price - 1) * 100, 2)
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
            psz_crossed_zero = False
            pending_exit_reason = None
            continue

        # --- In trade: track MFE/MAE and check exits ---
        if in_trade:
            mfe = max(trade.mfe_pct, (close / trade.entry_price - 1) * 100)
            mae_val = (close / trade.entry_price - 1) * 100
            trade.mfe_pct = mfe
            if -mae_val > trade.mae_pct:
                trade.mae_pct = -mae_val

            pnl_pct = (close / trade.entry_price - 1) * 100.0
            bars_held = i - trade.entry_idx
            psz = row.get("price_slope_z", 0)

            # 1. Hard stop (always active)
            if pnl_pct <= -8.0:
                pending_exit_reason = ExitReason.HARD_STOP

            # 2. PSZ zero-cross cycle
            elif psz_crossed_zero:
                # Trail phase: PSZ was above zero, exit when it drops back
                if psz <= 0:
                    pending_exit_reason = ExitReason.PSZ_GLIDE
            else:
                # Wait phase: check if PSZ crossed above zero
                if psz > 0:
                    psz_crossed_zero = True
                elif bars_held >= PSZ_PATIENCE:
                    # Dead trade: PSZ never crossed zero within patience window
                    pending_exit_reason = ExitReason.TIME_DECAY

            if pending_exit_reason:
                rp10 = row.get("range_pos_10", 0)
                rp22 = row.get("range_pos_22", 0)
                rp63 = row.get("range_pos_63", 0)
                rp252 = row.get("range_pos_252", 0)
                print(f"  Exit : dt:{row.get('date')}, reason:{pending_exit_reason}, pnl:{pnl_pct:+.2f}%, "
                      f"bars:{bars_held}, psz:{psz:.4f}, psz_crossed:{psz_crossed_zero}, "
                      f"rp_10:{rp10:.2f}, rp_22:{rp22:.2f}, "
                      f"cts:{row.get('cts', 0):.4f}")

        # --- Pending entry execution (EOD-lag) ---
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
                soft_filters_passed=0,
                entry_tag=sig["entry_tag"],
                conviction_score=sig["score"],
            )
            trade.signal_date = sig["signal_date"]
            trade.psz_at_entry = sig["psz"]
            trade.cts_at_signal = sig["cts"]
            trade.regime_at_entry = sig["regime"]
            in_trade = True
            psz_crossed_zero = False

        # --- Entry scan (oversold detection) ---
        else:
            rp_10 = row.get("range_pos_10", 0.5)
            rp_22 = row.get("range_pos_22", 0.5)
            rp_63 = row.get("range_pos_63", 0.5)
            rp_252 = row.get("range_pos_252", 0.5)
            rp_10_prev = prev.get("range_pos_10", 0.5)

            rw_252 = row.get("range_width_252", 0)

            cts = row.get("cts", 0)
            cts_slope = row.get("cts_slope", 0)
            cts_accel = row.get("cts_accel", 0)
            psz = row.get("price_slope_z", 0)
            psz_v = row.get("psz_v", 0)
            regime = row.get("regime", "")
            cwvap = row.get("cwvap", 0)
            cwvap_dist = (close - cwvap) / cwvap * 100 if cwvap > 0 else 0

            dh_10 = row.get("dist_high_10", 0)
            dh_22 = row.get("dist_high_22", 0)
            dh_63 = row.get("dist_high_63", 0)
            dh_252 = row.get("dist_high_252", 0)
            dl_10 = row.get("dist_low_10", 0)
            dl_252 = row.get("dist_low_252", 0)

            is_ath = row.get("is_ath", False)

            # Basing features
            bars_at_base = row.get("bars_at_base", 0)
            base_tightness = row.get("base_tightness", 1.0)
            atr = row.get("atr_20", 0)

            # --- Oversold entry conditions ---
            is_annual_oversold = rp_252 < 0.25
            is_quarterly_oversold = rp_63 < 0.30
            is_short_term_inflecting = rp_10 > rp_10_prev  # turning up
            is_green = close > prev_close
            rw_10 = row.get("range_width_10", 99)
            cts_accel_prev = prev.get("cts_accel", 0)
            cts_slope_prev = prev.get("cts_slope", 0)
            psz_prev = prev.get("price_slope_z", 0)

            # Basing guard: ATR-relative range width — the 10-day range
            # must be narrow relative to the stock's own volatility.
            # A flat rectangle base has a tight range; a falling knife
            # has a wide range even if % looks small on a volatile stock.
            # Tightened to 2.0 to block more falling knives (e.g. ADANIENT 2025-02)
            rw10_in_atrs = (rw_10 / 100 * close) / atr if atr > 0 else 99

            # Institutional acceleration guard: reject when cts_accel is
            # negative AND falling — smart money selling is accelerating,
            # the base is about to break
            accel_deteriorating = cts_accel < 0 and cts_accel < cts_accel_prev

            # PSZ direction guard: reject when PSZ velocity is negative or zero.
            # A valid base bounce should have PSZ momentum improving (psz_v > 0).
            # (Caught ADANIENT 2025-02-04)
            psz_falling = psz_v <= 0

            # PSZ sign guard: for mean-reversion, PSZ must be negative.
            # If PSZ is already positive, the reversal momentum phase has
            # begun — we missed the entry window.
            # (Caught TATACONSUM 2023-03-09, CIPLA 2026-03-02)
            # REJECTED for NIFTY 50 — momentum entries often outperform
            psz_already_positive = psz > 0

            # CTS slope freefall guard: reject when cts_slope is deeply
            # negative — institutions are still aggressively selling.
            # (Caught TITAN 2025-02-27 at cts_slope=-0.090)
            cts_slope_freefall = cts_slope < -0.05

            # CTS capitulation guard: require deep institutional oversold.
            # CTS > -0.50 means institutions haven't fully capitulated —
            # the sell cycle isn't complete. (Caught SBIN 2019 CTS=+0.86,
            # ITC 2025-12 CTS=-0.21, DRREDDY 2022 CTS=-0.31)
            cts_not_capitulated = cts > -0.50

            has_base = bars_at_base >= 5 and rw10_in_atrs < 2.0

            if is_annual_oversold and is_quarterly_oversold and \
               is_short_term_inflecting and is_green and has_base and \
               not accel_deteriorating and not psz_falling and \
               not cts_slope_freefall and \
               not cts_not_capitulated:

                # Conviction scoring
                score = 10
                if rp_252 < 0.15: score += 5    # deeply oversold
                if rp_63 < 0.15: score += 5     # quarterly deeply oversold
                if rp_10 < 0.30: score += 3     # short-term still low (room to run)
                if bars_at_base >= 10: score += 3  # well-established base
                if rw_10 < 3.0: score += 2  # very tight consolidation
                if cts < -0.50: score += 2       # institutional capitulation
                if cts_slope > prev.get("cts_slope", 0): score += 3  # institutional improving
                if psz_v > prev.get("psz_v", 0): score += 2  # momentum accelerating

                print(f"  Entry: dt:{row.get('date')}, regime:{regime}, score:{score}, "
                      f"rp_10:{rp_10:.2f}, rp_63:{rp_63:.2f}, rp_252:{rp_252:.2f}, "
                      f"bars_base:{bars_at_base}, rw10_atrs:{rw10_in_atrs:.2f}, "
                      f"cts:{cts:.4f}, cts_slope:{cts_slope:.4f}, cts_accel:{cts_accel:.4f}, "
                      f"psz:{psz:.4f}, psz_v:{psz_v:.4f}")

                pending_signal = {
                    "signal_date": str(row.get("date", ""))[:10],
                    "entry_tag": "RANGE_OVERSOLD",
                    "score": score,
                    "psz": psz,
                    "cts": cts,
                    "regime": regime,
                }

    # Close any open trade at end of data
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


def summarize_study(trades: list[Trade], label: str, out: io.StringIO):
    if not trades:
        out.write(f"\n{label}: No trades found.\n")
        return

    df = pd.DataFrame([{
        "symbol": t.symbol,
        "signal_date": getattr(t, "signal_date", ""),
        "entry_date": t.entry_date,
        "exit_date": t.exit_date,
        "pnl": t.pnl_pct,
        "mfe": t.mfe_pct,
        "mae": t.mae_pct,
        "bars": t.duration,
        "score": t.conviction_score,
        "reason": str(t.exit_reason),
        "regime": t.regime_at_entry,
        "cts": t.cts_at_signal,
        "psz": t.psz_at_entry,
    } for t in trades])

    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    avg_win = wins["pnl"].mean() if len(wins) else 0
    avg_loss = losses["pnl"].mean() if len(losses) else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    out.write(f"\n{'='*60}\n")
    out.write(f"  {label}\n")
    out.write(f"{'='*60}\n")
    out.write(f"Trades: {len(df)} | Win Rate: {(df['pnl']>0).mean()*100:.1f}% | Avg PnL: {df['pnl'].mean():+.2f}%\n")
    out.write(f"Avg Win: {avg_win:+.2f}% | Avg Loss: {avg_loss:+.2f}% | Payoff: {payoff:.2f}x\n")
    out.write(f"Avg MFE: {df['mfe'].mean():.2f}% | Avg MAE: {df['mae'].mean():.2f}% | Avg Duration: {df['bars'].mean():.1f} bars\n")
    out.write(f"Total PnL: {df['pnl'].sum():+.2f}%\n")

    out.write("\nExit Breakdown:\n")
    reason_agg = df.groupby("reason").agg(
        count=("pnl", "size"),
        win_rate=("pnl", lambda x: (x > 0).mean() * 100),
        avg_pnl=("pnl", "mean"),
    ).sort_values("avg_pnl", ascending=False)
    out.write(tabulate(reason_agg, headers="keys", tablefmt="simple", floatfmt=".2f") + "\n")

    out.write("\nRegime Breakdown:\n")
    regime_agg = df.groupby("regime").agg(
        count=("pnl", "size"),
        win_rate=("pnl", lambda x: (x > 0).mean() * 100),
        avg_pnl=("pnl", "mean"),
    ).sort_values("avg_pnl", ascending=False)
    out.write(tabulate(regime_agg, headers="keys", tablefmt="simple", floatfmt=".2f") + "\n")

    # Score breakdown
    out.write("\nScore Breakdown:\n")
    score_agg = df.groupby("score").agg(
        count=("pnl", "size"),
        win_rate=("pnl", lambda x: (x > 0).mean() * 100),
        avg_pnl=("pnl", "mean"),
    ).sort_values("avg_pnl", ascending=False)
    out.write(tabulate(score_agg, headers="keys", tablefmt="simple", floatfmt=".2f") + "\n")

    # Per-trade detail
    out.write("\nTrade Log:\n")
    detail = df[["symbol", "signal_date", "entry_date", "exit_date", "pnl", "mfe", "mae",
                 "bars", "score", "reason", "regime", "cts", "psz"]].sort_values("entry_date")
    out.write(tabulate(detail, headers="keys", tablefmt="simple", floatfmt=".2f", showindex=False) + "\n")


def summarize_period(trades: list[Trade], label: str, period: str, out: io.StringIO) -> dict:
    """Summarize trades for a period and return stats dict for comparison."""
    if not trades:
        out.write(f"\n{label} ({period}): No trades found.\n")
        return {}

    df = pd.DataFrame([{
        "symbol": t.symbol,
        "signal_date": getattr(t, "signal_date", ""),
        "entry_date": t.entry_date,
        "exit_date": t.exit_date,
        "pnl": t.pnl_pct,
        "mfe": t.mfe_pct,
        "mae": t.mae_pct,
        "bars": t.duration,
        "score": t.conviction_score,
        "reason": str(t.exit_reason),
        "regime": t.regime_at_entry,
        "cts": t.cts_at_signal,
        "psz": t.psz_at_entry,
    } for t in trades])

    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    avg_win = wins["pnl"].mean() if len(wins) else 0
    avg_loss = losses["pnl"].mean() if len(losses) else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
    wr = (df["pnl"] > 0).mean() * 100
    gross_win = wins["pnl"].sum() if len(wins) else 0
    gross_loss = abs(losses["pnl"].sum()) if len(losses) else 0
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    median_pnl = df["pnl"].median()
    symbols_traded = df["symbol"].nunique()

    sep = "=" * 60
    thin = "-" * 42
    out.write(f"\n{sep}\n")
    out.write(f"  {label}  ({period})\n")
    out.write(f"{sep}\n\n")
    out.write(f"  {'Metric':<22} {'Value':>10}\n")
    out.write(f"  {thin}\n")
    out.write(f"  {'Trades':<22} {len(df):>10}\n")
    out.write(f"  {'Symbols traded':<22} {symbols_traded:>10}\n")
    out.write(f"  {'Winners / Losers':<22} {f'{len(wins)} / {len(losses)}':>10}\n")
    out.write(f"  {'Win rate':<22} {wr:>9.1f}%\n")
    out.write(f"  {'Avg PnL':<22} {df['pnl'].mean():>+9.2f}%\n")
    out.write(f"  {'Median PnL':<22} {median_pnl:>+9.2f}%\n")
    out.write(f"  {'Avg winner':<22} {avg_win:>+9.2f}%\n")
    out.write(f"  {'Avg loser':<22} {avg_loss:>+9.2f}%\n")
    out.write(f"  {'Payoff ratio':<22} {payoff:>9.2f}x\n")
    out.write(f"  {'Profit factor':<22} {pf:>9.2f}\n")
    out.write(f"  {'Total PnL':<22} {df['pnl'].sum():>+9.2f}%\n")
    out.write(f"  {'Avg MFE':<22} {df['mfe'].mean():>+9.2f}%\n")
    out.write(f"  {'Avg MAE':<22} {df['mae'].mean():>9.2f}%\n")
    out.write(f"  {'Avg duration':<22} {df['bars'].mean():>8.1f} bars\n")

    out.write("\n  Exit Breakdown:\n")
    reason_agg = df.groupby("reason").agg(
        count=("pnl", "size"),
        win_rate=("pnl", lambda x: (x > 0).mean() * 100),
        avg_pnl=("pnl", "mean"),
    ).sort_values("avg_pnl", ascending=False)
    out.write(tabulate(reason_agg, headers="keys", tablefmt="simple", floatfmt=".2f") + "\n")

    out.write("\n  Regime Breakdown:\n")
    regime_agg = df.groupby("regime").agg(
        count=("pnl", "size"),
        win_rate=("pnl", lambda x: (x > 0).mean() * 100),
        avg_pnl=("pnl", "mean"),
    ).sort_values("avg_pnl", ascending=False)
    out.write(tabulate(regime_agg, headers="keys", tablefmt="simple", floatfmt=".2f") + "\n")

    out.write("\n  Trade Log:\n")
    detail = df[["symbol", "signal_date", "entry_date", "exit_date", "pnl", "mfe", "mae",
                 "bars", "score", "reason", "regime", "cts", "psz"]].sort_values("entry_date")
    out.write(tabulate(detail, headers="keys", tablefmt="simple", floatfmt=".2f", showindex=False) + "\n")

    return {
        "trades": len(df), "win_rate": round(wr, 1),
        "avg_pnl": round(df["pnl"].mean(), 2), "median_pnl": round(median_pnl, 2),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "payoff": round(payoff, 2), "profit_factor": round(pf, 2),
        "total_pnl": round(df["pnl"].sum(), 2),
        "avg_mfe": round(df["mfe"].mean(), 2), "avg_mae": round(df["mae"].mean(), 2),
        "hard_stops": int((df["reason"].str.contains("HARD_STOP")).sum()),
        "time_decays": int((df["reason"].str.contains("TIME_DECAY")).sum()),
    }


def main():
    parser = argparse.ArgumentParser(description="Study: Range Reversion (Oversold → Overbought)")
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--symbol", help="Run for a single symbol")
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--walk-forward", action="store_true",
                        help="Run walk-forward validation (train/test split)")
    parser.add_argument("--train-end", default="2023-12-31",
                        help="Train period end date (default: 2023-12-31)")
    parser.add_argument("--test-start", default="2024-01-01",
                        help="Test period start date (default: 2024-01-01)")
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else get_watchlist_symbols(args.watchlist)
    if not symbols:
        print(f"No symbols found for watchlist {args.watchlist}")
        return

    # Collect all trades across all symbols
    all_trades = []
    for sym in symbols:
        print(f"Analyzing {sym}...")
        try:
            engine = DivergenceEngine(sym)
            result = engine.run()
            trades = simulate_trades(sym, result.ledger)
            if args.start_date:
                trades = [t for t in trades if t.entry_date >= args.start_date]
            all_trades.extend(trades)
        except Exception as e:
            print(f"  Error: {e}")

    out = io.StringIO()
    entry_desc = ("rp_252<0.25 AND rp_63<0.30 AND rp_10 inflecting AND green "
                  "AND bars_base>=5 AND rw10_atrs<2.0 AND cts<-0.50 "
                  "AND psz_v>0 AND NOT(cts_slope<-0.05) AND NOT(accel<0 AND falling)")
    exit_desc = f"PSZ zero-cross cycle (patience={PSZ_PATIENCE} bars) OR hard stop -8%"

    if args.walk_forward:
        # --- Walk-Forward Mode ---
        train_end = args.train_end
        test_start = args.test_start
        test_end = datetime.now().strftime("%Y-%m-%d")

        train_trades = [t for t in all_trades if t.entry_date <= train_end]
        test_trades = [t for t in all_trades if t.entry_date >= test_start]

        sep = "=" * 60
        out.write(f"{sep}\n")
        out.write(f"  RANGE REVERSION — WALK-FORWARD VALIDATION\n")
        out.write(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        out.write(f"{sep}\n\n")
        out.write(f"  Watchlist:  {args.watchlist} ({len(symbols)} symbols)\n")
        out.write(f"  Train:      start to {train_end}\n")
        out.write(f"  Test:       {test_start} to {test_end}\n")
        out.write(f"  Entry:      {entry_desc}\n")
        out.write(f"  Exit:       {exit_desc}\n")

        train_stats = summarize_period(train_trades, "TRAIN", f"start to {train_end}", out)
        test_stats = summarize_period(test_trades, "TEST", f"{test_start} to {test_end}", out)

        # Stability comparison
        if train_stats and test_stats:
            out.write(f"\n{sep}\n")
            out.write(f"  PARAMETER STABILITY  (Test vs Train)\n")
            out.write(f"{sep}\n\n")
            out.write(f"  {'Metric':<22} {'Train':>10} {'Test':>10} {'Delta':>10}\n")
            out.write(f"  {'-'*54}\n")
            compare_keys = [
                ("trades",         "",  0),
                ("win_rate",       "%", 1),
                ("avg_pnl",        "%", 2),
                ("median_pnl",     "%", 2),
                ("avg_win",        "%", 2),
                ("avg_loss",       "%", 2),
                ("payoff",         "x", 2),
                ("profit_factor",  "",  2),
                ("total_pnl",      "%", 2),
                ("avg_mfe",        "%", 2),
                ("avg_mae",        "%", 2),
                ("hard_stops",     "",  0),
                ("time_decays",    "",  0),
            ]
            for key, suffix, dec in compare_keys:
                t = train_stats.get(key, 0)
                s = test_stats.get(key, 0)
                delta = s - t
                fmt = f".{dec}f"
                out.write(f"  {key:<22} {t:>9{fmt}}{suffix} {s:>9{fmt}}{suffix} {delta:>+9{fmt}}{suffix}\n")

            # Verdict
            out.write(f"\n  {'─'*54}\n")
            wr_ok = test_stats["win_rate"] >= 50.0
            pf_ok = test_stats["payoff"] >= 3.0
            stable_wr = abs(test_stats["win_rate"] - train_stats["win_rate"]) < 10
            stable_pf = test_stats["payoff"] >= train_stats["payoff"] * 0.6
            out.write(f"  Test WR >= 50%:       {'PASS' if wr_ok else 'FAIL'} ({test_stats['win_rate']:.1f}%)\n")
            out.write(f"  Test Payoff >= 3x:    {'PASS' if pf_ok else 'FAIL'} ({test_stats['payoff']:.2f}x)\n")
            out.write(f"  WR stable (<10pp):    {'PASS' if stable_wr else 'FAIL'} (delta={test_stats['win_rate']-train_stats['win_rate']:+.1f}pp)\n")
            out.write(f"  Payoff stable (>60%): {'PASS' if stable_pf else 'FAIL'} (test/train={test_stats['payoff']/train_stats['payoff']*100:.0f}%)\n")
            all_pass = wr_ok and pf_ok and stable_wr and stable_pf
            out.write(f"\n  VERDICT: {'PASS — ready for paper trading' if all_pass else 'NEEDS REVIEW'}\n")

    else:
        # --- Standard Study Mode ---
        out.write(f"Range Reversion Study (Oversold → Overbought)\n")
        out.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        out.write(f"Watchlist: {args.watchlist}\n")
        out.write(f"Entry: {entry_desc}\n")
        out.write(f"Exit:  {exit_desc}\n")
        summarize_study(all_trades, "RANGE REVERSION — FULL STUDY", out)

    report = out.getvalue()
    print(report)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    mode = "wf" if args.walk_forward else "study"
    report_path = OUTPUT_DIR / f"range_reversion_{mode}_{ts}.txt"
    report_path.write_text(report)

    if all_trades:
        csv_path = OUTPUT_DIR / f"range_reversion_{mode}_{ts}.csv"
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
            "cts": t.cts_at_signal,
            "psz": t.psz_at_entry,
        } for t in all_trades]).to_csv(csv_path, index=False)
        print(f"\nCSV saved: {csv_path}")

    print(f"Report saved: {report_path}")


if __name__ == "__main__":
    main()
