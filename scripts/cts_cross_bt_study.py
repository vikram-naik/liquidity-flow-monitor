"""Study: CTS crosses BT from below in oversold territory as entry signal."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3
from src.divergence_engine.engine import DivergenceEngine
import pandas as pd
import numpy as np

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"


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

watchlist = sys.argv[1] if len(sys.argv) > 1 else "NIFTY 50"
tickers = get_watchlist_symbols(watchlist)
print(f"Running CTS-cross-BT study on {watchlist} ({len(tickers)} tickers)")

# Sweep oversold thresholds
cts_thresholds = [-0.50, -0.60, -0.70, -0.80, -0.90]

all_trades = {t: [] for t in cts_thresholds}

for ti, ticker in enumerate(tickers):
    try:
        engine = DivergenceEngine(ticker)
        result = engine.run()
        df = result.ledger.copy()
        df['date'] = pd.to_datetime(df['date'])
        if len(df) < 120:
            continue
    except Exception:
        continue

    records = df.to_dict('records')

    for thresh in cts_thresholds:
        in_trade = False
        entry_bar = None
        entry_price = None
        entry_date = None
        entry_cts = None
        entry_bt = None
        cts_rose = False
        peak_pnl = 0.0

        for i in range(1, len(records)):
            row = records[i]
            prev = records[i - 1]
            cts = row.get('cts', np.nan)
            bt = row.get('cts_buy_threshold', np.nan)
            prev_cts = prev.get('cts', np.nan)
            prev_bt = prev.get('cts_buy_threshold', np.nan)
            close = row.get('close', np.nan)
            st = row.get('cts_sell_threshold', np.nan)

            if np.isnan(cts) or np.isnan(bt):
                continue

            if not in_trade:
                # Entry: CTS crosses BT from below, in oversold zone
                if np.isnan(prev_cts) or np.isnan(prev_bt):
                    continue
                if prev_cts <= prev_bt and cts > bt and cts <= thresh:
                    # Execute on next bar (T+1)
                    if i + 1 < len(records):
                        exec_bar = records[i + 1]
                        in_trade = True
                        entry_bar = i + 1
                        entry_price = exec_bar['close']
                        entry_date = exec_bar['date']
                        entry_cts = cts
                        entry_bt = bt
                        cts_rose = False
                        peak_pnl = 0.0
            else:
                # Track
                pnl_pct = (close - entry_price) / entry_price * 100
                peak_pnl = max(peak_pnl, pnl_pct)
                bars_held = i - entry_bar

                if cts > -1.0:
                    cts_rose = True

                # Exit logic (simplified CTS exits)
                exit_reason = None

                if cts >= 1.0:
                    exit_reason = "ceiling"
                elif cts_rose and not np.isnan(st) and cts < st and prev_cts > (st - 0.03):
                    exit_reason = "sell_threshold"
                elif cts_rose and not np.isnan(bt) and cts <= bt:
                    exit_reason = "hit_bt"
                elif cts_rose and cts <= -1.0:
                    exit_reason = "hit_floor"

                if exit_reason:
                    all_trades[thresh].append({
                        'ticker': ticker,
                        'entry_date': entry_date,
                        'exit_date': row['date'],
                        'entry_price': entry_price,
                        'exit_price': close,
                        'pnl_pct': pnl_pct,
                        'mfe_pct': peak_pnl,
                        'bars': bars_held,
                        'entry_cts': entry_cts,
                        'entry_bt': entry_bt,
                        'exit_reason': exit_reason,
                    })
                    in_trade = False

    if (ti + 1) % 50 == 0:
        print(f"  processed {ti+1}/{len(tickers)}")

print(f"\n{'='*80}")
print(f"CTS crosses BT from below — Oversold threshold sweep")
print(f"{'='*80}\n")

for thresh in cts_thresholds:
    trades = all_trades[thresh]
    if not trades:
        print(f"Threshold <= {thresh:.2f}: 0 trades")
        continue
    tdf = pd.DataFrame(trades)
    n = len(tdf)
    winners = tdf[tdf['pnl_pct'] > 0]
    losers = tdf[tdf['pnl_pct'] <= 0]
    win_pct = len(winners) / n * 100
    avg_pnl = tdf['pnl_pct'].mean()
    avg_win = winners['pnl_pct'].mean() if len(winners) > 0 else 0
    avg_loss = losers['pnl_pct'].mean() if len(losers) > 0 else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    avg_bars = tdf['bars'].mean()

    print(f"Threshold CTS <= {thresh:.2f}: N={n:4d} | Win%={win_pct:5.1f}% | AvgPnL={avg_pnl:+6.2f}% | "
          f"AvgWin={avg_win:+6.2f}% | AvgLoss={avg_loss:+6.2f}% | Payoff={payoff:.2f}x | AvgBars={avg_bars:.1f}")

# Show detail for each threshold
for thresh in cts_thresholds:
    trades = all_trades[thresh]
    if not trades:
        continue
    tdf = pd.DataFrame(trades)

    print(f"\n--- Exit breakdown for CTS <= {thresh} ---")
    for reason, grp in tdf.groupby('exit_reason'):
        print(f"  {reason:20s}: N={len(grp):3d} | Win%={len(grp[grp['pnl_pct']>0])/len(grp)*100:5.1f}% | AvgPnL={grp['pnl_pct'].mean():+6.2f}%")

    # Show recent trades (2026)
    recent = tdf[tdf['entry_date'] >= '2026-01-01'].sort_values('entry_date')
    if len(recent) > 0:
        print(f"\n--- Recent trades (2026+) for CTS <= {thresh} ---")
        for _, t in recent.iterrows():
            print(f"  {t['ticker']:15s} {t['entry_date'].strftime('%Y-%m-%d')} -> {t['exit_date'].strftime('%Y-%m-%d')} "
                  f"PnL={t['pnl_pct']:+6.2f}% MFE={t['mfe_pct']:+6.2f}% bars={t['bars']:3.0f} "
                  f"cts@sig={t['entry_cts']:.3f} bt@sig={t['entry_bt']:.3f} exit={t['exit_reason']}")

# Also compare with existing PSZ crossover signal (baseline)
print(f"\n{'='*80}")
print(f"Comparison: existing PSZ crossover (CTS=-1 + BT=-1 + PSZ cross -0.25)")
print(f"{'='*80}")
from src.trading.signals.savgol_cts import SavgolCTSSignal, SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.base import Trade

signal = SavgolCTSSignal()
entry_cfg = SavgolCTSEntryConfig()
exit_cfg = SavgolCTSExitConfig()
baseline_trades = []

for ti, ticker in enumerate(tickers):
    try:
        engine = DivergenceEngine(ticker)
        result = engine.run()
        df = result.ledger.copy()
        df['date'] = pd.to_datetime(df['date'])
        if len(df) < 120:
            continue
    except Exception:
        continue

    records = df.to_dict('records')
    in_trade = False
    trade_obj = None
    cts_rose = 0
    peak_close = 0.0

    for i in range(1, len(records)):
        row = records[i]
        prev = records[i - 1]

        if not in_trade:
            passed, intensity, meta = signal.check_entry(row, prev, entry_cfg, records, i)
            if passed and i + 1 < len(records):
                exec_bar = records[i + 1]
                in_trade = True
                entry_bar = i + 1
                entry_price = exec_bar['close']
                entry_date = exec_bar['date']
                trade_obj = Trade(
                    ticker=ticker, entry_date=entry_date,
                    entry_price=entry_price, entry_idx=i + 1,
                    intensity=intensity, reason=meta['reason']
                )
                cts_rose = 0
                peak_close = entry_price
        else:
            close = row.get('close', np.nan)
            peak_close = max(peak_close, close)
            bars_held = i - entry_bar
            exit_reason, cts_rose = signal.check_exit(
                row, prev, trade_obj, peak_close, bars_held,
                cts_rose, [], exit_cfg, records, i
            )
            if exit_reason:
                pnl_pct = (close - entry_price) / entry_price * 100
                baseline_trades.append({
                    'ticker': ticker,
                    'entry_date': entry_date,
                    'exit_date': row['date'],
                    'pnl_pct': pnl_pct,
                    'bars': bars_held,
                    'exit_reason': exit_reason,
                })
                in_trade = False

if baseline_trades:
    bdf = pd.DataFrame(baseline_trades)
    n = len(bdf)
    winners = bdf[bdf['pnl_pct'] > 0]
    losers = bdf[bdf['pnl_pct'] <= 0]
    win_pct = len(winners) / n * 100
    avg_pnl = bdf['pnl_pct'].mean()
    avg_win = winners['pnl_pct'].mean() if len(winners) > 0 else 0
    avg_loss = losers['pnl_pct'].mean() if len(losers) > 0 else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    avg_bars = bdf['bars'].mean()
    print(f"Baseline PSZ crossover : N={n:4d} | Win%={win_pct:5.1f}% | AvgPnL={avg_pnl:+6.2f}% | "
          f"AvgWin={avg_win:+6.2f}% | AvgLoss={avg_loss:+6.2f}% | Payoff={payoff:.2f}x | AvgBars={avg_bars:.1f}")
