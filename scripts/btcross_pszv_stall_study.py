"""
Study: BT-cross entries — how many have psz_v < 0 at entry,
and of those, how many are stopped by the PSZ stall gate?
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3
import numpy as np
import pandas as pd
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts import SavgolCTSSignal, SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.base import Trade

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
print(f"BT-cross psz_v / stall study — {watchlist} ({len(tickers)} tickers)\n")

signal = SavgolCTSSignal()
entry_cfg = SavgolCTSEntryConfig()
exit_cfg = SavgolCTSExitConfig()

trades = []

for ti, ticker in enumerate(tickers):
    try:
        engine = DivergenceEngine(ticker)
        result = engine.run()
        df = result.ledger.copy()
        df['date'] = pd.to_datetime(df['date'])
        if len(df) < 120:
            continue
    except Exception as e:
        print(f"  SKIP {ticker}: {e}")
        continue

    records = df.to_dict('records')

    in_trade = False
    trade_obj = None
    state = 0
    peak_close = 0.0
    entry_bar = 0
    entry_price = 0.0
    signal_bar_psz_v = np.nan  # psz_v at the signal bar (bar i)

    for i in range(1, len(records)):
        row = records[i]
        prev = records[i - 1]

        if not in_trade:
            passed, intensity, meta = signal.check_entry(row, prev, entry_cfg, records, i)
            if passed and meta.get("entry_tag") == "BT-cross" and i + 1 < len(records):
                exec_bar = records[i + 1]
                in_trade = True
                entry_bar = i + 1
                entry_price = exec_bar['close']
                entry_date = exec_bar['date']
                signal_bar_psz_v = row.get('psz_v', np.nan)  # psz_v at signal bar
                state = 0
                peak_close = entry_price
                trade_obj = Trade(
                    symbol=ticker,
                    entry_date=str(entry_date)[:10],
                    entry_price=entry_price,
                    entry_idx=i + 1,
                    atr_at_entry=records[i + 1].get('atr_20', 0) or 0,
                    soft_filters_passed=intensity,
                    entry_tag="BT-cross",
                )
        else:
            close = row.get('close', np.nan)
            if not np.isnan(close):
                peak_close = max(peak_close, close)
            bars_held = i - entry_bar

            exit_reason, state = signal.check_exit(
                row, prev, trade_obj, peak_close, bars_held,
                state, [], exit_cfg, records, i,
            )

            if exit_reason:
                pnl_pct = (close - entry_price) / entry_price * 100
                trades.append({
                    'ticker': ticker,
                    'entry_date': entry_date,
                    'exit_date': row['date'],
                    'pnl_pct': pnl_pct,
                    'bars': bars_held,
                    'exit_reason': exit_reason,
                    'psz_v': signal_bar_psz_v,
                    'psz_v_neg': signal_bar_psz_v < 0 if not np.isnan(signal_bar_psz_v) else False,
                })
                in_trade = False

    if (ti + 1) % 25 == 0:
        print(f"  processed {ti+1}/{len(tickers)}")

if not trades:
    print("No BT-cross trades found.")
    sys.exit(0)

df = pd.DataFrame(trades)
total = len(df)
print(f"\n{'='*70}")
print(f"BT-cross entries total: {total}")
print(f"{'='*70}\n")

# --- psz_v breakdown ---
neg = df[df['psz_v_neg'] == True]
pos = df[df['psz_v_neg'] == False]

print(f"psz_v < 0 at entry : {len(neg):4d}  ({len(neg)/total*100:.1f}%)")
print(f"psz_v >= 0 at entry: {len(pos):4d}  ({len(pos)/total*100:.1f}%)\n")

# Performance comparison
def stats(grp):
    if len(grp) == 0:
        return "N=0"
    w = grp[grp['pnl_pct'] > 0]
    l = grp[grp['pnl_pct'] <= 0]
    win_pct = len(w) / len(grp) * 100
    avg = grp['pnl_pct'].mean()
    aw = w['pnl_pct'].mean() if len(w) else 0
    al = l['pnl_pct'].mean() if len(l) else 0
    payoff = abs(aw / al) if al else 0
    return f"N={len(grp):4d} | Win%={win_pct:5.1f}% | AvgPnL={avg:+6.2f}% | Payoff={payoff:.2f}x"

print(f"psz_v <  0 : {stats(neg)}")
print(f"psz_v >= 0 : {stats(pos)}")
print(f"All        : {stats(df)}\n")

# --- Stall gate breakdown ---
print(f"{'='*70}")
print(f"Exit reason breakdown — ALL BT-cross entries")
print(f"{'='*70}")
for reason, grp in df.groupby('exit_reason'):
    is_stall = 'stall' in reason.lower()
    w = grp[grp['pnl_pct'] > 0]
    print(f"  {'[STALL]' if is_stall else '       '} {reason:35s}: N={len(grp):3d} | "
          f"Win%={len(w)/len(grp)*100:5.1f}% | AvgPnL={grp['pnl_pct'].mean():+6.2f}% | "
          f"AvgBars={grp['bars'].mean():.1f}")

# --- Stall gate: psz_v < 0 vs >= 0 ---
stall = df[df['exit_reason'].str.contains('stall', case=False, na=False)]
stall_neg = stall[stall['psz_v_neg'] == True]
stall_pos = stall[stall['psz_v_neg'] == False]

print(f"\n{'='*70}")
print(f"Stall gate breakdown by psz_v sign")
print(f"{'='*70}")
print(f"Stall exits total         : {len(stall):4d}")
print(f"  of which psz_v <  0    : {len(stall_neg):4d}  ({len(stall_neg)/max(len(stall),1)*100:.1f}%)")
print(f"  of which psz_v >= 0    : {len(stall_pos):4d}  ({len(stall_pos)/max(len(stall),1)*100:.1f}%)\n")

if len(neg) > 0:
    stall_rate_neg = len(stall_neg) / len(neg) * 100
    print(f"Stall rate among psz_v <  0 entries: {len(stall_neg)}/{len(neg)} = {stall_rate_neg:.1f}%")
if len(pos) > 0:
    stall_rate_pos = len(stall_pos) / len(pos) * 100
    print(f"Stall rate among psz_v >= 0 entries: {len(stall_pos)}/{len(pos)} = {stall_rate_pos:.1f}%")

# --- Non-stall outcomes for psz_v < 0 entries ---
print(f"\n{'='*70}")
print(f"Exit reasons for psz_v < 0 BT-cross entries")
print(f"{'='*70}")
if len(neg) > 0:
    for reason, grp in neg.groupby('exit_reason'):
        is_stall = 'stall' in reason.lower()
        w = grp[grp['pnl_pct'] > 0]
        print(f"  {'[STALL]' if is_stall else '       '} {reason:35s}: N={len(grp):3d} | "
              f"Win%={len(w)/len(grp)*100:5.1f}% | AvgPnL={grp['pnl_pct'].mean():+6.2f}%")

# --- psz_v distribution among stall exits ---
print(f"\n{'='*70}")
print(f"psz_v distribution at entry (all BT-cross)")
print(f"{'='*70}")
pszv = df['psz_v'].dropna()
print(f"  min={pszv.min():+.4f} | P10={pszv.quantile(0.10):+.4f} | "
      f"median={pszv.median():+.4f} | P90={pszv.quantile(0.90):+.4f} | max={pszv.max():+.4f}")
print(f"  psz_v < 0  count: {(pszv < 0).sum()} ({(pszv < 0).mean()*100:.1f}%)")
print(f"  psz_v >= 0 count: {(pszv >= 0).sum()} ({(pszv >= 0).mean()*100:.1f}%)")

if len(stall) > 0:
    stall_pszv = stall['psz_v'].dropna()
    print(f"\n  Stall exits — psz_v: min={stall_pszv.min():+.4f} | "
          f"median={stall_pszv.median():+.4f} | max={stall_pszv.max():+.4f}")

# --- Recent 2026 detail for psz_v < 0 stall exits ---
recent_stall_neg = stall_neg[stall_neg['entry_date'] >= '2025-01-01'].sort_values('entry_date')
if len(recent_stall_neg) > 0:
    print(f"\n{'='*70}")
    print(f"psz_v < 0 stall exits (2025+) — {len(recent_stall_neg)} trades")
    print(f"{'='*70}")
    for _, t in recent_stall_neg.iterrows():
        print(f"  {t['ticker']:15s} {t['entry_date'].strftime('%Y-%m-%d')}  "
              f"PnL={t['pnl_pct']:+6.2f}%  bars={t['bars']:2.0f}  psz_v={t['psz_v']:+.4f}  "
              f"exit={t['exit_reason']}")
