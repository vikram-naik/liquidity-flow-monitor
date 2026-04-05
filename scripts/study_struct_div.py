"""Study script: Analyze failed vs successful Structural Divergence entries.

Captures ledger-row features at entry time for every Struct-Div trade,
then splits winners/losers and computes statistics on candidate guard
features (psz_v, cwc_slope, etc.) to inform fine-tuning.

Usage:
    venv/bin/python3 scripts/study_struct_div.py
    venv/bin/python3 scripts/study_struct_div.py --period test
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

# Features to capture at entry bar (signal bar = i-1 due to EOD-lag)
ENTRY_FEATURES = [
    "price_slope_z", "rdv_slope_z", "cts", "cts_slope", "cts_accel",
    "cts_accel_threshold",
    "accum_div", "cwc", "cwvap", "close", "psz_v", "cwc_slope",
    "pdd_120", "regime", "open",
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


def simulate_and_capture(
    ticker: str, df: pd.DataFrame,
    entry_cfg: SavgolCTSEntryConfig, exit_cfg: SavgolCTSExitConfig, signal,
) -> list[dict]:
    """Walk bar-by-bar, capture entry-bar features for Struct-Div trades."""
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
    signal_bar_data: dict | None = None  # ledger row on signal day

    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue

        cw = row.get("cwvap", np.nan)
        cwvap_values.append(cw)

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

            # Only keep Struct-Div trades
            if trade.entry_tag == EntryTag.STRUCTURAL_DIVERGENCE.value:
                row_data = {
                    "symbol": ticker,
                    "entry_date": trade.entry_date,
                    "exit_date": trade.exit_date,
                    "pnl_pct": trade.pnl_pct,
                    "mfe_pct": trade.mfe_pct,
                    "mae_pct": trade.mae_pct,
                    "duration": trade.duration,
                    "exit_reason": trade.exit_reason.value if hasattr(trade.exit_reason, "value") else str(trade.exit_reason),
                }
                # Attach signal-bar features
                if signal_bar_data:
                    for feat in ENTRY_FEATURES:
                        row_data[f"sig_{feat}"] = signal_bar_data.get(feat, np.nan)
                    # Derived: psz velocity direction, cwc_slope direction
                    psz = signal_bar_data.get("price_slope_z", np.nan)
                    cwvap_val = signal_bar_data.get("cwvap", np.nan)
                    close_val = signal_bar_data.get("close", np.nan)
                    row_data["sig_cwvap_dist"] = (
                        (close_val - cwvap_val) / cwvap_val * 100.0
                        if not np.isnan(cwvap_val) and cwvap_val > 0 else np.nan
                    )
                    rsz = signal_bar_data.get("rdv_slope_z", np.nan)
                    row_data["sig_spread"] = rsz - psz if not any(np.isnan(v) for v in [rsz, psz]) else np.nan

                    # Prev bar features for delta computation
                    if signal_bar_data.get("_prev_row"):
                        prev_r = signal_bar_data["_prev_row"]
                        prev_psz = prev_r.get("price_slope_z", np.nan)
                        prev_cwc = prev_r.get("cwc", np.nan)
                        prev_cwc_slope = prev_r.get("cwc_slope", np.nan)
                        psz_v_sig = signal_bar_data.get("psz_v", np.nan)
                        cwc_slope_sig = signal_bar_data.get("cwc_slope", np.nan)

                        row_data["sig_psz_delta"] = psz - prev_psz if not any(np.isnan(v) for v in [psz, prev_psz]) else np.nan
                        row_data["sig_psz_rising"] = int(psz > prev_psz) if not any(np.isnan(v) for v in [psz, prev_psz]) else np.nan
                        row_data["sig_cwc_delta"] = signal_bar_data.get("cwc", np.nan) - prev_cwc if not np.isnan(prev_cwc) else np.nan
                        row_data["sig_cwc_rising"] = int(signal_bar_data.get("cwc", np.nan) > prev_cwc) if not np.isnan(prev_cwc) else np.nan
                        row_data["sig_cwc_slope_rising"] = int(cwc_slope_sig > prev_cwc_slope) if not any(np.isnan(v) for v in [cwc_slope_sig, prev_cwc_slope]) else np.nan

                results.append(row_data)

            in_trade = False
            trade = None
            delivery_bad_count = 0
            pending_exit_reason = None
            signal_bar_data = None
            continue

        if in_trade:
            if close > peak_close:
                peak_close = close
            mfe = max(trade.mfe_pct, (close / trade.entry_price - 1) * 100)
            mae_val = (close / trade.entry_price - 1) * 100
            mae = min(-trade.mae_pct, mae_val)
            trade.mfe_pct = mfe
            if -mae > trade.mae_pct:
                trade.mae_pct = -mae

            reason, delivery_bad_count = signal.check_exit(
                row, prev, trade, peak_close, i - trade.entry_idx,
                delivery_bad_count, cwvap_values, exit_cfg, records, i,
            )
            if reason:
                pending_exit_reason = reason

        elif pending_signal is not None:
            sig = pending_signal
            pending_signal = None
            atr = row.get("atr_20", 0)
            if atr <= 0 or np.isnan(atr):
                signal_bar_data = None
                continue
            psz_now = row.get("price_slope_z", np.nan)
            trade = Trade(
                symbol=ticker,
                entry_date=str(row.get("date", ""))[:10],
                entry_price=close,
                entry_idx=i,
                atr_at_entry=atr,
                soft_filters_passed=sig.get("soft_count", 0),
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

        else:
            qualifies, soft_count, fdetails = signal.check_entry(row, prev, entry_cfg, records, i)
            if qualifies:
                tag = fdetails.get("entry_tag", "")
                if tag == EntryTag.STRUCTURAL_DIVERGENCE:
                    # Capture signal-bar data (this is the signal bar; trade opens next bar)
                    sig_data = {feat: row.get(feat, np.nan) for feat in ENTRY_FEATURES}
                    sig_data["_prev_row"] = prev
                    signal_bar_data = sig_data
                else:
                    signal_bar_data = None
                pending_signal = {"soft_count": soft_count, "details": fdetails}

    # Handle open trade at end of data
    if in_trade and trade and trade.entry_tag == EntryTag.STRUCTURAL_DIVERGENCE.value:
        last = records[-1]
        trade.exit_date = str(last.get("date", ""))[:10]
        trade.exit_price = last.get("close", trade.entry_price)
        trade.exit_reason = ExitReason.END_OF_DATA
        trade.pnl_pct = round((trade.exit_price / trade.entry_price - 1) * 100, 2)
        trade.duration = n - 1 - trade.entry_idx
        trade.mfe_pct = round(trade.mfe_pct, 2)
        trade.mae_pct = round(trade.mae_pct, 2)
        row_data = {
            "symbol": ticker, "entry_date": trade.entry_date,
            "exit_date": trade.exit_date, "pnl_pct": trade.pnl_pct,
            "mfe_pct": trade.mfe_pct, "mae_pct": trade.mae_pct,
            "duration": trade.duration,
            "exit_reason": ExitReason.END_OF_DATA.value,
        }
        if signal_bar_data:
            for feat in ENTRY_FEATURES:
                row_data[f"sig_{feat}"] = signal_bar_data.get(feat, np.nan)
        results.append(row_data)

    return results


def analyze(df: pd.DataFrame, label: str) -> str:
    """Produce analysis report as string."""
    lines = []
    w = lines.append

    w(f"\n{'=' * 72}")
    w(f"  STRUCTURAL DIVERGENCE ENTRY STUDY — {label}")
    w(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w(f"{'=' * 72}")

    total = len(df)
    winners = df[df["pnl_pct"] > 0]
    losers = df[df["pnl_pct"] <= 0]
    w(f"\n  Total trades: {total}  |  Winners: {len(winners)} ({len(winners)/total*100:.1f}%)  |  Losers: {len(losers)} ({len(losers)/total*100:.1f}%)")
    w(f"  Avg PnL: {df['pnl_pct'].mean():+.2f}%  |  Avg Winner: {winners['pnl_pct'].mean():+.2f}%  |  Avg Loser: {losers['pnl_pct'].mean():+.2f}%")

    # === Exit reason breakdown ===
    w(f"\n  --- Exit Reason Breakdown ---")
    exit_agg = (
        df.groupby("exit_reason")
        .agg(count=("pnl_pct", "size"), avg_pnl=("pnl_pct", "mean"),
             win_rate=("pnl_pct", lambda x: round((x > 0).mean() * 100, 1)))
        .reset_index().sort_values("count", ascending=False).round(2)
    )
    w(tabulate(exit_agg, headers=["Exit Reason", "Count", "Avg P&L%", "Win%"],
               tablefmt="simple", floatfmt=".2f", showindex=False))

    # === Feature comparison: Winners vs Losers ===
    numeric_feats = [c for c in df.columns if c.startswith("sig_") and df[c].dtype in ["float64", "int64", "float32"]]

    w(f"\n  --- Feature Comparison: Winners vs Losers ---")
    comparison = []
    for feat in sorted(numeric_feats):
        w_mean = winners[feat].mean()
        l_mean = losers[feat].mean()
        w_med = winners[feat].median()
        l_med = losers[feat].median()
        comparison.append({
            "Feature": feat.replace("sig_", ""),
            "Win Mean": round(w_mean, 4) if not np.isnan(w_mean) else "—",
            "Win Med": round(w_med, 4) if not np.isnan(w_med) else "—",
            "Lose Mean": round(l_mean, 4) if not np.isnan(l_mean) else "—",
            "Lose Med": round(l_med, 4) if not np.isnan(l_med) else "—",
            "Delta Mean": round(w_mean - l_mean, 4) if not any(np.isnan(v) for v in [w_mean, l_mean]) else "—",
        })
    w(tabulate(comparison, headers="keys", tablefmt="simple", showindex=False))

    # === KEY INVESTIGATION 1: PSZ velocity (psz_v) — is PSZ bending up? ===
    w(f"\n  --- Investigation 1: PSZ Velocity (psz_v) at Signal Bar ---")
    w(f"  Hypothesis: Winners have psz_v > 0 (PSZ bending up), losers have psz_v <= 0")
    if "sig_psz_v" in df.columns:
        for threshold in [0.0, -0.001, -0.002, 0.001, 0.002]:
            above = df[df["sig_psz_v"] > threshold]
            below = df[df["sig_psz_v"] <= threshold]
            if len(above) > 0 and len(below) > 0:
                w(f"\n  psz_v > {threshold}:  N={len(above)}, WR={above['pnl_pct'].gt(0).mean()*100:.1f}%, "
                  f"Avg PnL={above['pnl_pct'].mean():+.2f}%, Avg MAE={above['mae_pct'].mean():.2f}%")
                w(f"  psz_v <= {threshold}: N={len(below)}, WR={below['pnl_pct'].gt(0).mean()*100:.1f}%, "
                  f"Avg PnL={below['pnl_pct'].mean():+.2f}%, Avg MAE={below['mae_pct'].mean():.2f}%")

    # === KEY INVESTIGATION 1b: PSZ rising (discrete) ===
    w(f"\n  --- Investigation 1b: PSZ Rising (psz > prev_psz) ---")
    if "sig_psz_rising" in df.columns:
        rising = df[df["sig_psz_rising"] == 1]
        falling = df[df["sig_psz_rising"] == 0]
        if len(rising) > 0 and len(falling) > 0:
            w(f"  PSZ rising:  N={len(rising)}, WR={rising['pnl_pct'].gt(0).mean()*100:.1f}%, "
              f"Avg PnL={rising['pnl_pct'].mean():+.2f}%, Avg MAE={rising['mae_pct'].mean():.2f}%")
            w(f"  PSZ falling: N={len(falling)}, WR={falling['pnl_pct'].gt(0).mean()*100:.1f}%, "
              f"Avg PnL={falling['pnl_pct'].mean():+.2f}%, Avg MAE={falling['mae_pct'].mean():.2f}%")

    # === KEY INVESTIGATION 2: CWC slope direction ===
    w(f"\n  --- Investigation 2: CWC Slope at Signal Bar ---")
    w(f"  Hypothesis: Negative cwc with falling cwc_slope = bad (distribution intensifying)")
    if "sig_cwc_slope" in df.columns and "sig_cwc" in df.columns:
        # CWC negative + cwc_slope falling vs rising
        neg_cwc = df[df["sig_cwc"] < 0]
        if len(neg_cwc) > 0:
            w(f"\n  When CWC < 0 (N={len(neg_cwc)}):")
            for slope_thresh in [0.0, -0.01, -0.02]:
                slope_fall = neg_cwc[neg_cwc["sig_cwc_slope"] <= slope_thresh]
                slope_rise = neg_cwc[neg_cwc["sig_cwc_slope"] > slope_thresh]
                if len(slope_fall) > 0 and len(slope_rise) > 0:
                    w(f"    cwc_slope <= {slope_thresh}: N={len(slope_fall)}, WR={slope_fall['pnl_pct'].gt(0).mean()*100:.1f}%, "
                      f"Avg PnL={slope_fall['pnl_pct'].mean():+.2f}%")
                    w(f"    cwc_slope >  {slope_thresh}: N={len(slope_rise)}, WR={slope_rise['pnl_pct'].gt(0).mean()*100:.1f}%, "
                      f"Avg PnL={slope_rise['pnl_pct'].mean():+.2f}%")

        # CWC positive
        pos_cwc = df[df["sig_cwc"] >= 0]
        if len(pos_cwc) > 0:
            w(f"\n  When CWC >= 0 (N={len(pos_cwc)}):")
            for slope_thresh in [0.0, 0.01, 0.02]:
                slope_rise = pos_cwc[pos_cwc["sig_cwc_slope"] >= slope_thresh]
                slope_fall = pos_cwc[pos_cwc["sig_cwc_slope"] < slope_thresh]
                if len(slope_rise) > 0 and len(slope_fall) > 0:
                    w(f"    cwc_slope >= {slope_thresh}: N={len(slope_rise)}, WR={slope_rise['pnl_pct'].gt(0).mean()*100:.1f}%, "
                      f"Avg PnL={slope_rise['pnl_pct'].mean():+.2f}%")
                    w(f"    cwc_slope <  {slope_thresh}: N={len(slope_fall)}, WR={slope_fall['pnl_pct'].gt(0).mean()*100:.1f}%, "
                      f"Avg PnL={slope_fall['pnl_pct'].mean():+.2f}%")

    # === INVESTIGATION 3: CWC range buckets ===
    w(f"\n  --- Investigation 3: CWC Range Buckets ---")
    if "sig_cwc" in df.columns:
        bins = [(-1.1, -0.3), (-0.3, 0.0), (0.0, 0.3), (0.3, 0.51)]
        for lo, hi in bins:
            bucket = df[(df["sig_cwc"] > lo) & (df["sig_cwc"] <= hi)]
            if len(bucket) > 0:
                w(f"  CWC ({lo:.1f}, {hi:.1f}]: N={len(bucket)}, WR={bucket['pnl_pct'].gt(0).mean()*100:.1f}%, "
                  f"Avg PnL={bucket['pnl_pct'].mean():+.2f}%, Avg MAE={bucket['mae_pct'].mean():.2f}%")

    # === INVESTIGATION 4: CTS accel magnitude ===
    w(f"\n  --- Investigation 4: CTS Accel Magnitude ---")
    if "sig_cts_accel" in df.columns:
        for thresh in [0.0, 0.005, 0.01, 0.02, 0.03]:
            above = df[df["sig_cts_accel"] > thresh]
            if len(above) > 0:
                w(f"  cts_accel > {thresh}: N={len(above)}, WR={above['pnl_pct'].gt(0).mean()*100:.1f}%, "
                  f"Avg PnL={above['pnl_pct'].mean():+.2f}%")

    # === INVESTIGATION 5: Spread magnitude ===
    w(f"\n  --- Investigation 5: Spread (rsz - psz) Buckets ---")
    if "sig_spread" in df.columns:
        for lo, hi in [(0.35, 0.50), (0.50, 0.75), (0.75, 1.0), (1.0, 3.0)]:
            bucket = df[(df["sig_spread"] >= lo) & (df["sig_spread"] < hi)]
            if len(bucket) > 0:
                w(f"  Spread [{lo:.2f}, {hi:.2f}): N={len(bucket)}, WR={bucket['pnl_pct'].gt(0).mean()*100:.1f}%, "
                  f"Avg PnL={bucket['pnl_pct'].mean():+.2f}%")

    # === INVESTIGATION 6: Combined PSZ_V + CWC_SLOPE guard ===
    w(f"\n  --- Investigation 6: Combined Guards (PSZ bending + CWC slope) ---")
    if "sig_psz_v" in df.columns and "sig_cwc_slope" in df.columns:
        for pv_thresh in [0.0, -0.001]:
            for cs_thresh in [0.0, -0.01]:
                good = df[(df["sig_psz_v"] > pv_thresh) | (df["sig_cwc_slope"] > cs_thresh)]
                bad = df[(df["sig_psz_v"] <= pv_thresh) & (df["sig_cwc_slope"] <= cs_thresh)]
                if len(good) > 0 and len(bad) > 0:
                    w(f"\n  psz_v > {pv_thresh} OR cwc_slope > {cs_thresh}:")
                    w(f"    PASS: N={len(good)}, WR={good['pnl_pct'].gt(0).mean()*100:.1f}%, "
                      f"Avg PnL={good['pnl_pct'].mean():+.2f}%, Avg MAE={good['mae_pct'].mean():.2f}%")
                    w(f"    FAIL: N={len(bad)}, WR={bad['pnl_pct'].gt(0).mean()*100:.1f}%, "
                      f"Avg PnL={bad['pnl_pct'].mean():+.2f}%, Avg MAE={bad['mae_pct'].mean():.2f}%")

    # === INVESTIGATION 7: PDD_120 effect ===
    w(f"\n  --- Investigation 7: PDD_120 at Signal Bar ---")
    if "sig_pdd_120" in df.columns:
        for thresh in [0.0, -2.0, -5.0, 2.0]:
            above = df[df["sig_pdd_120"] > thresh]
            below = df[df["sig_pdd_120"] <= thresh]
            if len(above) > 0 and len(below) > 0:
                w(f"  pdd_120 > {thresh}: N={len(above)}, WR={above['pnl_pct'].gt(0).mean()*100:.1f}%, "
                  f"Avg PnL={above['pnl_pct'].mean():+.2f}%")
                w(f"  pdd_120 <= {thresh}: N={len(below)}, WR={below['pnl_pct'].gt(0).mean()*100:.1f}%, "
                  f"Avg PnL={below['pnl_pct'].mean():+.2f}%")

    # === INVESTIGATION 8: CTS Accel vs Dynamic Threshold ===
    w(f"\n  --- Investigation 8: cts_accel > cts_accel_threshold (Dynamic Gate) ---")
    if "sig_cts_accel" in df.columns and "sig_cts_accel_threshold" in df.columns:
        valid = df[df["sig_cts_accel_threshold"].notna() & df["sig_cts_accel"].notna()]
        above = valid[valid["sig_cts_accel"] > valid["sig_cts_accel_threshold"]]
        below = valid[valid["sig_cts_accel"] <= valid["sig_cts_accel_threshold"]]
        w(f"  Valid rows: {len(valid)} (of {total})")
        if len(above) > 0:
            w(f"  PASS (accel > threshold): N={len(above)}, WR={above['pnl_pct'].gt(0).mean()*100:.1f}%, "
              f"Avg PnL={above['pnl_pct'].mean():+.2f}%, Avg MAE={above['mae_pct'].mean():.2f}%")
        if len(below) > 0:
            w(f"  FAIL (accel <= threshold): N={len(below)}, WR={below['pnl_pct'].gt(0).mean()*100:.1f}%, "
              f"Avg PnL={below['pnl_pct'].mean():+.2f}%, Avg MAE={below['mae_pct'].mean():.2f}%")
        # Exit reason breakdown for FAIL group
        if len(below) > 2:
            fail_exits = below.groupby("exit_reason").agg(
                count=("pnl_pct", "size"), avg_pnl=("pnl_pct", "mean")
            ).reset_index().sort_values("count", ascending=False).round(2)
            w(f"\n  FAIL group exit breakdown:")
            w(tabulate(fail_exits, headers=["Exit Reason", "Count", "Avg P&L%"],
                       tablefmt="simple", floatfmt=".2f", showindex=False))

    # === Worst trades detail ===
    w(f"\n  --- 15 Worst Trades (by PnL%) ---")
    worst = df.nsmallest(15, "pnl_pct")
    detail_cols = ["symbol", "entry_date", "pnl_pct", "mae_pct", "exit_reason"]
    feat_cols = ["sig_psz_v", "sig_psz_rising", "sig_cwc", "sig_cwc_slope", "sig_spread", "sig_cts_accel", "sig_cts_accel_threshold", "sig_pdd_120"]
    show_cols = detail_cols + [c for c in feat_cols if c in df.columns]
    w(tabulate(worst[show_cols].round(4), headers="keys", tablefmt="simple", showindex=False))

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Study Struct-Div entry features")
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--period", default="both", choices=["train", "test", "both"])
    args = parser.parse_args()

    symbols = get_watchlist_symbols(args.watchlist)
    entry_cfg = SavgolCTSEntryConfig()
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")

    all_results = []
    periods = []
    if args.period in ("train", "both"):
        periods.append(("TRAIN", TRAIN_START, TRAIN_END))
    if args.period in ("test", "both"):
        periods.append(("TEST", TEST_START, datetime.now().strftime("%Y-%m-%d")))

    full_report = []

    for label, start, end in periods:
        print(f"Running {label} ({start} to {end})...", flush=True)
        period_results = []
        failed = []
        for sym in symbols:
            try:
                engine = DivergenceEngine(sym, start_date=start, end_date=end)
                result = engine.run()
                # Need fresh signal instance per symbol to reset cooldown state
                sig = SignalFactory.get_signal("savgol_cts")
                trades = simulate_and_capture(sym, result.ledger, entry_cfg, exit_cfg, sig)
                period_results.extend(trades)
            except Exception as e:
                failed.append((sym, str(e)))

        print(f"  {label}: {len(period_results)} Struct-Div trades | {len(failed)} symbols failed")

        if period_results:
            df = pd.DataFrame(period_results)
            report = analyze(df, label)
            full_report.append(report)
            print(report)

            # Save CSV for further analysis
            csv_path = OUTPUT_DIR / f"struct_div_study_{label.lower()}.csv"
            df.to_csv(csv_path, index=False)
            print(f"  CSV saved: {csv_path}")

    # Save full report
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%d-%b-%Y_%H:%M")
    report_path = OUTPUT_DIR / f"struct_div_study_{ts}.txt"
    report_path.write_text("\n".join(full_report))
    print(f"\nFull report saved: {report_path}")


if __name__ == "__main__":
    main()
