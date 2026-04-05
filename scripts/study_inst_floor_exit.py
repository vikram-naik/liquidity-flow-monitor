"""Study script: Institutional Floor exit optimization.

Captures bar-by-bar metrics during each Institutional Floor trade,
then replays multiple exit strategies offline to compare them.

Exit strategies tested:
  A. Baseline  — current PSZ glide (CWVAP reclaim → PSZ peak → PSZ drops)
  B. Slope Cycle — CTS slope zero-cross (like Slope-Bottom exit)
  C. CTS Recovery — exit when CTS crosses above buy threshold
  D. Time Decay + PSZ — exit after N bars if PSZ < threshold
  E. Trailing MFE — lock fraction of peak PnL once MFE > activation
  F. Hybrid — Slope cycle with hard stop + time decay safety net

Usage:
    venv/bin/python3 scripts/study_inst_floor_exit.py
    venv/bin/python3 scripts/study_inst_floor_exit.py --period test
    venv/bin/python3 scripts/study_inst_floor_exit.py --watchlist "NIFTY 50" --period both
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import SignalFactory, Trade
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag, ExitReason

DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

TRAIN_START, TRAIN_END = "2019-01-01", "2023-12-31"
TEST_START = "2024-01-01"

SEP = "=" * 72
THIN_SEP = "-" * 72

# Bar-level features to capture during each trade
BAR_FEATURES = [
    "close", "open", "cwvap", "price_slope_z", "psz_v",
    "cts", "cts_slope", "cts_accel", "cts_buy_threshold", "cts_sell_threshold",
    "cwc", "cwc_slope", "rdv_slope_z", "pdd_120", "regime", "atr_20",
]


def get_watchlist_symbols(name: str) -> list[str]:
    db = sqlite3.connect(str(DB_PATH))
    row = db.execute("SELECT id FROM watchlists WHERE name = ?", (name,)).fetchone()
    if not row:
        db.close()
        sys.exit(f"Watchlist '{name}' not found.")
    symbols = [
        r[0] for r in db.execute(
            "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY display_order",
            (row[0],),
        ).fetchall()
    ]
    db.close()
    return symbols


MAX_EXTENDED_BARS = 60  # capture bars beyond live exit so replay strategies can hold longer


def capture_trades(
    ticker: str, df: pd.DataFrame,
    entry_cfg: SavgolCTSEntryConfig, exit_cfg: SavgolCTSExitConfig, signal,
) -> list[dict]:
    """Walk bar-by-bar, capture bar-level data for every Institutional Floor trade.

    Bars are captured beyond the live exit (up to MAX_EXTENDED_BARS from entry)
    so that replay strategies which hold longer have data to evaluate.

    Returns list of trade dicts, each with:
      - Trade metadata (symbol, entry_date, entry_price, pnl_pct, exit_reason, etc.)
      - 'bars': list of per-bar dicts with BAR_FEATURES + pnl_pct at each bar
    """
    records = df.to_dict("records")
    n = len(records)
    results = []
    in_trade = False
    trade = None
    peak_close = 0.0
    delivery_bad_count = 0
    cwvap_values: list[float] = []
    pending_signal: dict | None = None
    pending_exit_reason: ExitReason | str | None = None
    trade_bars: list[dict] = []

    # Extended capture state: after live exit, keep recording bars
    extending: dict | None = None  # holds the result dict being extended

    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue

        cw = row.get("cwvap", np.nan)
        cwvap_values.append(cw)

        # Continue capturing bars after live exit
        if extending is not None:
            entry_price = extending["entry_price"]
            bars_from_entry = len(extending["bars"])
            if bars_from_entry >= MAX_EXTENDED_BARS:
                # Done extending, finalize
                results.append(extending)
                extending = None
            else:
                if close > peak_close:
                    peak_close = close
                bar_data = {"bar_idx": bars_from_entry}
                for feat in BAR_FEATURES:
                    bar_data[feat] = row.get(feat, np.nan)
                bar_data["pnl_pct"] = round((close / entry_price - 1) * 100, 2)
                bar_data["running_mfe"] = round((peak_close / entry_price - 1) * 100, 2)
                bar_data["date"] = str(row.get("date", ""))[:10]
                extending["bars"].append(bar_data)
                continue

        # Execute pending exit
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

            if trade.entry_tag == EntryTag.INSTITUTIONAL_FLOOR.value:
                trade_result = {
                    "symbol": ticker,
                    "entry_date": trade.entry_date,
                    "exit_date": trade.exit_date,
                    "entry_price": trade.entry_price,
                    "pnl_pct": trade.pnl_pct,
                    "mfe_pct": trade.mfe_pct,
                    "mae_pct": trade.mae_pct,
                    "duration": trade.duration,
                    "exit_reason": trade.exit_reason.value if hasattr(trade.exit_reason, "value") else str(trade.exit_reason),
                    "bars": trade_bars,
                }
                # Start extended capture
                extending = trade_result

            in_trade = False
            trade = None
            delivery_bad_count = 0
            pending_exit_reason = None
            trade_bars = []
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

            # Capture bar data for this trade
            bar_data = {"bar_idx": bars_held}
            for feat in BAR_FEATURES:
                bar_data[feat] = row.get(feat, np.nan)
            bar_data["pnl_pct"] = round((close / trade.entry_price - 1) * 100, 2)
            bar_data["running_mfe"] = round((peak_close / trade.entry_price - 1) * 100, 2)
            bar_data["date"] = str(row.get("date", ""))[:10]
            trade_bars.append(bar_data)

            reason, delivery_bad_count = signal.check_exit(
                row, prev, trade, peak_close, bars_held,
                delivery_bad_count, cwvap_values, exit_cfg, records, i,
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
                soft_filters_passed=sig.get("soft_count", 0),
                conviction_score=sig.get("details", {}).get("conv_score", 0),
                rdv_pass=sig.get("details", {}).get("rdv", False),
                mcs_pass=sig.get("details", {}).get("mcs", False),
                cwc_pass=sig.get("details", {}).get("cwc", False),
                grad_pass=sig.get("details", {}).get("grad", False),
                regime_at_entry=sig.get("details", {}).get("regime", "-"),
                entry_tag=sig.get("details", {}).get("entry_tag", ""),
                psz_at_entry=psz_now if not np.isnan(psz_now) else 0.0,
                psz_peak=psz_now if not np.isnan(psz_now) else 0.0,
            )
            peak_close = close
            delivery_bad_count = 0
            in_trade = True
            trade_bars = []

        else:
            qualifies, soft_count, fdetails = signal.check_entry(row, prev, entry_cfg, records, i)
            if qualifies:
                pending_signal = {"soft_count": soft_count, "details": fdetails}

    # Flush any trade still being extended
    if extending is not None:
        results.append(extending)

    # Handle open trade at end of data
    if in_trade and trade and trade.entry_tag == EntryTag.INSTITUTIONAL_FLOOR.value:
        last = records[-1]
        trade.exit_date = str(last.get("date", ""))[:10]
        trade.exit_price = last.get("close", trade.entry_price)
        trade.exit_reason = ExitReason.END_OF_DATA
        trade.pnl_pct = round((trade.exit_price / trade.entry_price - 1) * 100, 2)
        trade.duration = n - 1 - trade.entry_idx
        trade.mfe_pct = round(trade.mfe_pct, 2)
        trade.mae_pct = round(trade.mae_pct, 2)
        results.append({
            "symbol": ticker,
            "entry_date": trade.entry_date,
            "exit_date": trade.exit_date,
            "entry_price": trade.entry_price,
            "pnl_pct": trade.pnl_pct,
            "mfe_pct": trade.mfe_pct,
            "mae_pct": trade.mae_pct,
            "duration": trade.duration,
            "exit_reason": ExitReason.END_OF_DATA.value,
            "bars": trade_bars,
        })

    return results


# -------------------------------------------------------------------------
# Exit strategy replay functions
# Each takes a trade dict (with 'bars' list) and returns (exit_bar_idx, exit_reason, pnl_pct)
# exit_bar_idx is the index into the bars list where exit fires; pnl uses NEXT bar open.
# -------------------------------------------------------------------------

def _exit_pnl(bars: list[dict], bar_idx: int) -> float:
    """PnL at exit: use next bar's open if available, else current close."""
    if bar_idx + 1 < len(bars):
        open_next = bars[bar_idx + 1].get("open", np.nan)
        entry_price_implied = bars[0]["close"] / (1 + bars[0]["pnl_pct"] / 100)
        if not np.isnan(open_next) and entry_price_implied > 0:
            return round((open_next / entry_price_implied - 1) * 100, 2)
    return bars[bar_idx]["pnl_pct"]


def replay_baseline(trade: dict, hard_stop: float = 8.0, pnl_cap: float = 8.0,
                     psz_peak_thresh: float = 0.25, psz_exit_thresh: float = 0.0) -> dict:
    """Strategy A: Current PSZ glide exit."""
    bars = trade["bars"]
    if not bars:
        return {"exit_bar": 0, "reason": "NO_BARS", "pnl_pct": 0.0}

    price_above_cwvap = False
    psz_was_above = False

    for i, b in enumerate(bars):
        pnl = b["pnl_pct"]

        # Hard stop
        if pnl <= -hard_stop:
            return {"exit_bar": i, "reason": "HARD_STOP", "pnl_pct": _exit_pnl(bars, i)}

        # PnL cap
        if pnl >= pnl_cap:
            return {"exit_bar": i, "reason": "PNL_CAP", "pnl_pct": _exit_pnl(bars, i)}

        close = b.get("close", np.nan)
        cwvap = b.get("cwvap", np.nan)
        psz = b.get("price_slope_z", np.nan)

        if not np.isnan(close) and not np.isnan(cwvap) and close > cwvap:
            price_above_cwvap = True

        if price_above_cwvap and not np.isnan(psz):
            if not psz_was_above:
                if psz >= psz_peak_thresh:
                    psz_was_above = True
            else:
                if psz < psz_exit_thresh:
                    return {"exit_bar": i, "reason": "PSZ_GLIDE", "pnl_pct": _exit_pnl(bars, i)}

    last = bars[-1]
    return {"exit_bar": len(bars) - 1, "reason": "END_OF_DATA", "pnl_pct": last["pnl_pct"]}


def replay_slope_cycle(trade: dict, hard_stop: float = 8.0, pnl_cap: float = 8.0) -> dict:
    """Strategy B: CTS slope zero-cross cycle (same as Slope-Bottom exit)."""
    bars = trade["bars"]
    if not bars:
        return {"exit_bar": 0, "reason": "NO_BARS", "pnl_pct": 0.0}

    slope_crossed_zero = False

    for i, b in enumerate(bars):
        pnl = b["pnl_pct"]
        if pnl <= -hard_stop:
            return {"exit_bar": i, "reason": "HARD_STOP", "pnl_pct": _exit_pnl(bars, i)}
        if pnl >= pnl_cap:
            return {"exit_bar": i, "reason": "PNL_CAP", "pnl_pct": _exit_pnl(bars, i)}

        cs = b.get("cts_slope", np.nan)
        if np.isnan(cs):
            continue

        if not slope_crossed_zero:
            if cs > 0:
                slope_crossed_zero = True
        else:
            if cs < 0:
                return {"exit_bar": i, "reason": "SLOPE_CYCLE", "pnl_pct": _exit_pnl(bars, i)}

    last = bars[-1]
    return {"exit_bar": len(bars) - 1, "reason": "END_OF_DATA", "pnl_pct": last["pnl_pct"]}


def replay_cts_recovery(trade: dict, hard_stop: float = 8.0, pnl_cap: float = 8.0) -> dict:
    """Strategy C: Exit when CTS crosses above buy threshold (recovery complete)."""
    bars = trade["bars"]
    if not bars:
        return {"exit_bar": 0, "reason": "NO_BARS", "pnl_pct": 0.0}

    for i, b in enumerate(bars):
        pnl = b["pnl_pct"]
        if pnl <= -hard_stop:
            return {"exit_bar": i, "reason": "HARD_STOP", "pnl_pct": _exit_pnl(bars, i)}
        if pnl >= pnl_cap:
            return {"exit_bar": i, "reason": "PNL_CAP", "pnl_pct": _exit_pnl(bars, i)}

        cts = b.get("cts", np.nan)
        bt = b.get("cts_buy_threshold", np.nan)
        if not np.isnan(cts) and not np.isnan(bt):
            if cts > bt:
                return {"exit_bar": i, "reason": "CTS_RECOVERY", "pnl_pct": _exit_pnl(bars, i)}

    last = bars[-1]
    return {"exit_bar": len(bars) - 1, "reason": "END_OF_DATA", "pnl_pct": last["pnl_pct"]}


def replay_time_decay(trade: dict, hard_stop: float = 8.0, pnl_cap: float = 8.0,
                       max_bars: int = 10, min_pnl: float = 1.0) -> dict:
    """Strategy D: Time decay — exit after max_bars if PnL < min_pnl."""
    bars = trade["bars"]
    if not bars:
        return {"exit_bar": 0, "reason": "NO_BARS", "pnl_pct": 0.0}

    for i, b in enumerate(bars):
        pnl = b["pnl_pct"]
        if pnl <= -hard_stop:
            return {"exit_bar": i, "reason": "HARD_STOP", "pnl_pct": _exit_pnl(bars, i)}
        if pnl >= pnl_cap:
            return {"exit_bar": i, "reason": "PNL_CAP", "pnl_pct": _exit_pnl(bars, i)}

        if i >= max_bars and pnl < min_pnl:
            return {"exit_bar": i, "reason": "TIME_DECAY", "pnl_pct": _exit_pnl(bars, i)}

    last = bars[-1]
    return {"exit_bar": len(bars) - 1, "reason": "END_OF_DATA", "pnl_pct": last["pnl_pct"]}


def replay_trailing_mfe(trade: dict, hard_stop: float = 8.0, pnl_cap: float = 8.0,
                         activation_pct: float = 3.0, lock_ratio: float = 0.50) -> dict:
    """Strategy E: Trailing stop — lock fraction of MFE once activated."""
    bars = trade["bars"]
    if not bars:
        return {"exit_bar": 0, "reason": "NO_BARS", "pnl_pct": 0.0}

    for i, b in enumerate(bars):
        pnl = b["pnl_pct"]
        running_mfe = b.get("running_mfe", 0.0)

        if pnl <= -hard_stop:
            return {"exit_bar": i, "reason": "HARD_STOP", "pnl_pct": _exit_pnl(bars, i)}
        if pnl >= pnl_cap:
            return {"exit_bar": i, "reason": "PNL_CAP", "pnl_pct": _exit_pnl(bars, i)}

        if running_mfe >= activation_pct:
            trail_floor = running_mfe * lock_ratio
            if pnl < trail_floor:
                return {"exit_bar": i, "reason": "TRAIL_STOP", "pnl_pct": _exit_pnl(bars, i)}

    last = bars[-1]
    return {"exit_bar": len(bars) - 1, "reason": "END_OF_DATA", "pnl_pct": last["pnl_pct"]}


def replay_hybrid(trade: dict, hard_stop: float = 5.0, pnl_cap: float = 8.0,
                   time_decay_bars: int = 12, time_decay_min_pnl: float = 1.0) -> dict:
    """Strategy F: Slope cycle + hard stop + time decay safety."""
    bars = trade["bars"]
    if not bars:
        return {"exit_bar": 0, "reason": "NO_BARS", "pnl_pct": 0.0}

    slope_crossed_zero = False

    for i, b in enumerate(bars):
        pnl = b["pnl_pct"]
        if pnl <= -hard_stop:
            return {"exit_bar": i, "reason": "HARD_STOP", "pnl_pct": _exit_pnl(bars, i)}
        if pnl >= pnl_cap:
            return {"exit_bar": i, "reason": "PNL_CAP", "pnl_pct": _exit_pnl(bars, i)}

        # Time decay safety net
        if i >= time_decay_bars and pnl < time_decay_min_pnl:
            return {"exit_bar": i, "reason": "TIME_DECAY", "pnl_pct": _exit_pnl(bars, i)}

        cs = b.get("cts_slope", np.nan)
        if not np.isnan(cs):
            if not slope_crossed_zero:
                if cs > 0:
                    slope_crossed_zero = True
            else:
                if cs < 0:
                    return {"exit_bar": i, "reason": "SLOPE_CYCLE", "pnl_pct": _exit_pnl(bars, i)}

    last = bars[-1]
    return {"exit_bar": len(bars) - 1, "reason": "END_OF_DATA", "pnl_pct": last["pnl_pct"]}


def replay_cts_trail_cap(trade: dict, hard_stop: float = 8.0, pnl_cap: float = 8.0,
                          psz_peak_thresh: float = 0.25, psz_exit_thresh: float = 0.0) -> dict:
    """Strategy G: CTS Trail Cap — two-phase exit.

    Phase 1 (PSZ trail): CWVAP reclaim → PSZ peaks → PSZ drops below threshold.
        When PSZ trail fires, check CTS:
        - If CTS already >= sell threshold → exit immediately (PSZ_GLIDE).
        - If CTS still < sell threshold  → enter Phase 2 (CTS has room to run).
    Phase 2 (CTS trail): Hold until CTS crosses sell threshold → ST_CROSS.
    PnL cap and hard stop active throughout both phases.
    """
    bars = trade["bars"]
    if not bars:
        return {"exit_bar": 0, "reason": "NO_BARS", "pnl_pct": 0.0}

    # Phase 1: PSZ crosses zero (becomes positive), then drops back below zero → Phase 2
    psz_was_positive = False
    # Phase 2 state
    trailing_cts = False

    for i, b in enumerate(bars):
        pnl = b["pnl_pct"]
        if pnl <= -hard_stop:
            return {"exit_bar": i, "reason": "HARD_STOP", "pnl_pct": _exit_pnl(bars, i)}
        if pnl >= pnl_cap:
            return {"exit_bar": i, "reason": "PNL_CAP", "pnl_pct": _exit_pnl(bars, i)}

        psz = b.get("price_slope_z", np.nan)
        cts = b.get("cts", np.nan)
        st = b.get("cts_sell_threshold", np.nan)

        if trailing_cts:
            # Phase 2: CTS trail — exit when CTS reaches sell threshold
            if not np.isnan(cts) and not np.isnan(st) and cts >= st:
                return {"exit_bar": i, "reason": "ST_CROSS", "pnl_pct": _exit_pnl(bars, i)}
        else:
            # Phase 1: PSZ crosses zero, then drops back negative
            if not np.isnan(psz):
                if not psz_was_positive:
                    if psz > 0:
                        psz_was_positive = True
                else:
                    if psz < 0:
                        # PSZ cycle done — check CTS before exiting
                        if not np.isnan(cts) and not np.isnan(st) and cts < st:
                            trailing_cts = True
                        else:
                            return {"exit_bar": i, "reason": "PSZ_GLIDE", "pnl_pct": _exit_pnl(bars, i)}

    last = bars[-1]
    return {"exit_bar": len(bars) - 1, "reason": "END_OF_DATA", "pnl_pct": last["pnl_pct"]}


# -------------------------------------------------------------------------
# Analysis
# -------------------------------------------------------------------------

STRATEGIES = {
    "A_Baseline":       lambda t: replay_baseline(t),
    "B_SlopeCycle":     lambda t: replay_slope_cycle(t),
    "C_CTS_Recovery":   lambda t: replay_cts_recovery(t),
    "D_TimeDecay_10":   lambda t: replay_time_decay(t, max_bars=10, min_pnl=1.0),
    "D_TimeDecay_15":   lambda t: replay_time_decay(t, max_bars=15, min_pnl=1.0),
    "E_Trail_3_50":     lambda t: replay_trailing_mfe(t, activation_pct=3.0, lock_ratio=0.50),
    "E_Trail_4_50":     lambda t: replay_trailing_mfe(t, activation_pct=4.0, lock_ratio=0.50),
    "E_Trail_3_40":     lambda t: replay_trailing_mfe(t, activation_pct=3.0, lock_ratio=0.40),
    "F_Hybrid_5_12":    lambda t: replay_hybrid(t, hard_stop=5.0, time_decay_bars=12),
    "F_Hybrid_8_15":    lambda t: replay_hybrid(t, hard_stop=8.0, time_decay_bars=15),
    "G_CTS_Trail_Cap":  lambda t: replay_cts_trail_cap(t),
}

# Baseline parameter sweep
BASELINE_SWEEP = [
    {"psz_peak_thresh": 0.15, "psz_exit_thresh": -0.10},
    {"psz_peak_thresh": 0.15, "psz_exit_thresh": 0.0},
    {"psz_peak_thresh": 0.20, "psz_exit_thresh": -0.05},
    {"psz_peak_thresh": 0.20, "psz_exit_thresh": 0.0},
    {"psz_peak_thresh": 0.25, "psz_exit_thresh": 0.0},   # current default
    {"psz_peak_thresh": 0.25, "psz_exit_thresh": -0.10},
    {"psz_peak_thresh": 0.30, "psz_exit_thresh": 0.0},
    {"psz_peak_thresh": 0.30, "psz_exit_thresh": -0.10},
]


def compute_stats(results: list[dict]) -> dict:
    """Compute summary stats from a list of replay results."""
    if not results:
        return {}
    pnls = [r["pnl_pct"] for r in results]
    bars = [r["exit_bar"] for r in results]
    n = len(pnls)
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    avg_win = np.mean(winners) if winners else 0.0
    avg_loss = abs(np.mean(losers)) if losers else 0.0
    wr = len(winners) / n * 100
    payoff = avg_win / avg_loss if avg_loss > 0 else float("inf")
    gross_win = sum(winners)
    gross_loss = abs(sum(losers))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    expectancy = avg_win * (wr / 100) - avg_loss * (1 - wr / 100)
    return {
        "N": n, "WR%": round(wr, 1),
        "Avg PnL": round(np.mean(pnls), 2),
        "Med PnL": round(np.median(pnls), 2),
        "Avg Win": round(avg_win, 2),
        "Avg Loss": round(-abs(np.mean(losers)) if losers else 0, 2),
        "Payoff": round(payoff, 2),
        "PF": round(pf, 2),
        "Expect": round(expectancy, 2),
        "Avg Bars": round(np.mean(bars), 1),
    }


def analyze(trades: list[dict], label: str) -> str:
    """Run all exit strategies on captured trades, produce comparison report."""
    lines = []
    w = lines.append

    w(f"\n{SEP}")
    w(f"  INSTITUTIONAL FLOOR EXIT STUDY — {label}")
    w(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w(f"  Trades: {len(trades)}")
    w(SEP)

    if not trades:
        w("  No trades to analyze.")
        return "\n".join(lines)

    # --- 1. Current exit breakdown ---
    w(f"\n  --- Current Exit Breakdown (Live) ---")
    exit_reasons = pd.Series([t["exit_reason"] for t in trades])
    pnls = pd.Series([t["pnl_pct"] for t in trades])
    for reason in exit_reasons.unique():
        mask = exit_reasons == reason
        subset = pnls[mask]
        wr = (subset > 0).mean() * 100
        w(f"  {reason:<30} N={mask.sum():>3}  Avg={subset.mean():+.2f}%  WR={wr:.1f}%")

    # --- 2. Strategy comparison ---
    w(f"\n  --- Exit Strategy Comparison ---")
    comparison = []
    strategy_results = {}
    for name, replay_fn in STRATEGIES.items():
        results = [replay_fn(t) for t in trades]
        strategy_results[name] = results
        stats = compute_stats(results)
        stats["Strategy"] = name
        comparison.append(stats)

    comp_df = pd.DataFrame(comparison)
    col_order = ["Strategy", "N", "WR%", "Avg PnL", "Med PnL", "Avg Win", "Avg Loss",
                 "Payoff", "PF", "Expect", "Avg Bars"]
    comp_df = comp_df[[c for c in col_order if c in comp_df.columns]]
    w(tabulate(comp_df, headers="keys", tablefmt="simple", floatfmt=".2f", showindex=False))

    # --- 3. Exit reason breakdown per strategy ---
    for name in ["A_Baseline", "B_SlopeCycle", "F_Hybrid_5_12", "G_CTS_Trail_Cap"]:
        if name not in strategy_results:
            continue
        w(f"\n  --- {name} Exit Reason Breakdown ---")
        reasons = pd.Series([r["reason"] for r in strategy_results[name]])
        rpnls = pd.Series([r["pnl_pct"] for r in strategy_results[name]])
        for reason in reasons.unique():
            mask = reasons == reason
            subset = rpnls[mask]
            wr = (subset > 0).mean() * 100
            w(f"  {reason:<25} N={mask.sum():>3}  Avg={subset.mean():+.2f}%  WR={wr:.1f}%")

    # --- 3b. G_CTS_Trail_Cap full trade list ---
    if "G_CTS_Trail_Cap" in strategy_results:
        g_results = strategy_results["G_CTS_Trail_Cap"]
        w(f"\n  --- G_CTS_Trail_Cap: All Trades ({len(g_results)}) ---")
        g_rows = []
        for t, r in zip(trades, g_results):
            g_rows.append({
                "Symbol": t["symbol"], "Entry": t["entry_date"],
                "PnL%": r["pnl_pct"], "MFE%": t["mfe_pct"], "MAE%": t["mae_pct"],
                "Bars": r["exit_bar"], "Exit": r["reason"],
            })
        g_df = pd.DataFrame(g_rows).sort_values("PnL%", ascending=True)
        w(tabulate(g_df, headers="keys", tablefmt="simple", floatfmt=".2f", showindex=False))

    # --- 4. Baseline parameter sweep ---
    w(f"\n  --- Baseline (PSZ Glide) Parameter Sweep ---")
    sweep_rows = []
    for params in BASELINE_SWEEP:
        results = [replay_baseline(t, **params) for t in trades]
        stats = compute_stats(results)
        stats["PSZ_peak"] = params["psz_peak_thresh"]
        stats["PSZ_exit"] = params["psz_exit_thresh"]
        sweep_rows.append(stats)

    sweep_df = pd.DataFrame(sweep_rows)
    sweep_cols = ["PSZ_peak", "PSZ_exit", "N", "WR%", "Avg PnL", "Payoff", "PF", "Expect", "Avg Bars"]
    sweep_df = sweep_df[[c for c in sweep_cols if c in sweep_df.columns]]
    w(tabulate(sweep_df, headers="keys", tablefmt="simple", floatfmt=".2f", showindex=False))

    # --- 5. Bar-by-bar PnL trajectory stats ---
    w(f"\n  --- Average PnL Trajectory (bar-by-bar) ---")
    max_bars_to_show = 25
    bar_pnls = {}
    for t in trades:
        for b in t["bars"]:
            idx = b["bar_idx"]
            if idx <= max_bars_to_show:
                bar_pnls.setdefault(idx, []).append(b["pnl_pct"])

    traj_rows = []
    for idx in sorted(bar_pnls.keys()):
        vals = bar_pnls[idx]
        traj_rows.append({
            "Bar": idx, "N": len(vals),
            "Avg PnL": round(np.mean(vals), 2),
            "Med PnL": round(np.median(vals), 2),
            "P25": round(np.percentile(vals, 25), 2),
            "P75": round(np.percentile(vals, 75), 2),
            "%>0": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
        })
    w(tabulate(traj_rows, headers="keys", tablefmt="simple", floatfmt=".2f", showindex=False))

    # --- 6. Bar-by-bar indicator trajectory (CTS slope, PSZ) ---
    w(f"\n  --- Average Indicator Trajectory (bar-by-bar) ---")
    bar_indicators = {}
    for t in trades:
        for b in t["bars"]:
            idx = b["bar_idx"]
            if idx <= max_bars_to_show:
                bar_indicators.setdefault(idx, []).append(b)

    ind_rows = []
    for idx in sorted(bar_indicators.keys()):
        vals = bar_indicators[idx]
        cs_vals = [v["cts_slope"] for v in vals if not np.isnan(v.get("cts_slope", np.nan))]
        psz_vals = [v["price_slope_z"] for v in vals if not np.isnan(v.get("price_slope_z", np.nan))]
        cts_vals = [v["cts"] for v in vals if not np.isnan(v.get("cts", np.nan))]
        ind_rows.append({
            "Bar": idx,
            "Avg CTS_slope": round(np.mean(cs_vals), 4) if cs_vals else np.nan,
            "Avg PSZ": round(np.mean(psz_vals), 3) if psz_vals else np.nan,
            "Avg CTS": round(np.mean(cts_vals), 3) if cts_vals else np.nan,
            "%slope>0": round(sum(1 for v in cs_vals if v > 0) / len(cs_vals) * 100, 1) if cs_vals else np.nan,
        })
    w(tabulate(ind_rows, headers="keys", tablefmt="simple", floatfmt=".4f", showindex=False))

    # --- 7. MFE/MAE analysis ---
    w(f"\n  --- MFE/MAE Distribution ---")
    mfe_vals = [t["mfe_pct"] for t in trades]
    mae_vals = [t["mae_pct"] for t in trades]
    for pct_label, vals, name in [("MFE", mfe_vals, "MFE"), ("MAE", mae_vals, "MAE")]:
        w(f"  {name}: Mean={np.mean(vals):.2f}%  Med={np.median(vals):.2f}%  "
          f"P25={np.percentile(vals, 25):.2f}%  P75={np.percentile(vals, 75):.2f}%  "
          f"P90={np.percentile(vals, 90):.2f}%")

    # --- 8. Worst trades detail ---
    w(f"\n  --- 10 Worst Trades (by PnL%) ---")
    sorted_trades = sorted(trades, key=lambda t: t["pnl_pct"])[:10]
    worst_rows = []
    for t in sorted_trades:
        worst_rows.append({
            "Symbol": t["symbol"], "Entry": t["entry_date"],
            "PnL%": t["pnl_pct"], "MFE%": t["mfe_pct"], "MAE%": t["mae_pct"],
            "Bars": t["duration"], "Exit": t["exit_reason"],
        })
    w(tabulate(worst_rows, headers="keys", tablefmt="simple", floatfmt=".2f", showindex=False))

    # --- 9. Best trades detail ---
    w(f"\n  --- 10 Best Trades (by PnL%) ---")
    sorted_trades_best = sorted(trades, key=lambda t: t["pnl_pct"], reverse=True)[:10]
    best_rows = []
    for t in sorted_trades_best:
        best_rows.append({
            "Symbol": t["symbol"], "Entry": t["entry_date"],
            "PnL%": t["pnl_pct"], "MFE%": t["mfe_pct"], "MAE%": t["mae_pct"],
            "Bars": t["duration"], "Exit": t["exit_reason"],
        })
    w(tabulate(best_rows, headers="keys", tablefmt="simple", floatfmt=".2f", showindex=False))

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Study Institutional Floor exit strategies")
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--period", default="both", choices=["train", "test", "both"])
    args = parser.parse_args()

    symbols = get_watchlist_symbols(args.watchlist)
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()

    periods = []
    if args.period in ("train", "both"):
        periods.append(("TRAIN", TRAIN_START, TRAIN_END))
    if args.period in ("test", "both"):
        periods.append(("TEST", TEST_START, datetime.now().strftime("%Y-%m-%d")))

    full_report = []

    for label, start, end in periods:
        print(f"Running {label} ({start} to {end})...", flush=True)
        period_trades = []
        failed = []
        for sym in symbols:
            try:
                engine = DivergenceEngine(sym, start_date=None, end_date=None)
                result = engine.run()
                sig = SignalFactory.get_signal("savgol_cts")
                trades = capture_trades(sym, result.ledger, entry_cfg, exit_cfg, sig)
                # Filter to period
                period_trades.extend([
                    t for t in trades if start <= t["entry_date"] <= end
                ])
            except Exception as e:
                failed.append((sym, str(e)))

        print(f"  {label}: {len(period_trades)} Inst-Floor trades | {len(failed)} symbols failed")

        if period_trades:
            report = analyze(period_trades, label)
            full_report.append(report)
            print(report)

    # Save report
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%d-%b-%Y_%H:%M")
    report_path = OUTPUT_DIR / f"inst_floor_exit_study_{ts}.txt"
    report_path.write_text("\n".join(full_report))
    print(f"\nReport saved: {report_path}")


if __name__ == "__main__":
    main()
