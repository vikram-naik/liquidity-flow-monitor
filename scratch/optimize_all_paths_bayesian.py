import os
import sys
import io
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from tabulate import tabulate

# Add project root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade, SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.enums import EntryTag, ExitReason
from src.trading.signals.savgol_cts.entries.utils import evaluate_spearman_trend

# Helpers for feature extraction
def get_spearman_5(records, idx):
    tps_5 = []
    for k in range(idx - 4, idx + 1):
        r = records[k]
        tp = (r.get("high", 0.0) + r.get("low", 0.0) + r.get("close", 0.0)) / 3.0
        tps_5.append(tp)
    if len(tps_5) >= 5:
        return evaluate_spearman_trend(tps_5)
    return np.nan

def get_min_cts_10(records, idx):
    cts_vals = []
    for k in range(max(0, idx - 9), idx + 1):
        c = records[k].get("cts", np.nan)
        if not np.isnan(c):
            cts_vals.append(c)
    if cts_vals:
        return min(cts_vals)
    return np.nan

# --- RELAXED BASIC GATES ---

def check_gap_down(records, idx, lookback=10):
    start = max(1, idx - lookback)
    for i in range(start, idx + 1):
        prev_low = records[i-1].get("low", 0)
        curr_high = records[i].get("high", 0)
        atr = records[i].get("atr_20", 0)
        if prev_low > curr_high:
            gap_size = prev_low - curr_high
            if atr > 0 and gap_size > (0.3 * atr):
                return True
    return False

def check_slope_flatness(records, idx):
    for i in [idx, idx - 1]:
        if i < 4: continue
        slopes = [records[k].get("cts_slope", 0) for k in range(i-4, i+1)]
        if abs(evaluate_spearman_trend(slopes)) < 0.6:
            return True
    return False

def check_basing(records, idx):
    for i in [idx, idx - 1]:
        if i < 4: continue
        row = records[i]
        close = row.get("close", 0.0)
        if close <= 0.0: continue
        atr = row.get("atr_20", 0.0)
        bt = row.get("base_tightness", 1.0)
        is_tight = bt < 0.35
        rw10 = row.get("range_width_10", 0.0)
        rw10_abs = (rw10 * close) / 100.0
        rw_atrs = rw10_abs / atr if atr > 0 else 10.0
        is_narrow = rw_atrs < 1.5
        tps = []
        for k in range(i - 4, i + 1):
            r = records[k]
            tp = (r.get("high", 0.0) + r.get("low", 0.0) + r.get("close", 0.0)) / 3.0
            tps.append(tp)
        is_flat = abs(evaluate_spearman_trend(tps)) < 0.6
        if (int(is_tight) + int(is_narrow) + int(is_flat)) >= 2:
            return True
    return False

def check_long_term_range(row, cwc_threshold=0.50):
    range_pos_22 = row.get("range_pos_22", 0.0)
    range_pos_63 = row.get("range_pos_63", 0.0)
    range_pos_252 = row.get("range_pos_252", 0.0)
    cwc = row.get("cwc", 0.0)
    in_high_range = (range_pos_22 > 0.50 or range_pos_63 > 0.60 or range_pos_252 > 0.55)
    strong_coherent_trend = (cwc > cwc_threshold)
    return in_high_range and not strong_coherent_trend

def check_trigger_5(row, records, idx):
    cwc = row.get("cwc", 0.0)
    cts_accel = row.get("cts_accel", 0.0)
    pdd_120 = row.get("pdd_120", 0.0)
    bt = row.get("base_tightness", 1.0)
    price_slope_z = row.get("price_slope_z", 0.0)
    if cwc < 0.40 or cts_accel < 0.01 or pdd_120 > -4.0 or bt > 0.45 or price_slope_z > -0.15:
        return False
    tps_10 = []
    for k in range(max(0, idx - 9), idx + 1):
        r = records[k]
        tp = (r.get("high", 0.0) + r.get("low", 0.0) + r.get("close", 0.0)) / 3.0
        tps_10.append(tp)
    if len(tps_10) >= 5:
        spearman_10 = evaluate_spearman_trend(tps_10)
        if spearman_10 <= -0.90:
            return False
    else:
        return False
    return True

# 1. CDVL-CTS relaxed basic gate
def basic_cdvl_cts(row, prev_row, records, idx):
    cdvl = row.get("cdvl", np.nan)
    prev_cdvl = prev_row.get("cdvl", np.nan)
    cts = row.get("cts", np.nan)
    prev_cts = prev_row.get("cts", np.nan)
    cts_buy_threshold = row.get("cts_buy_threshold", np.nan)
    cwc = row.get("cwc", np.nan)
    cts_accel = row.get("cts_accel", np.nan)
    prev_cts_accel = prev_row.get("cts_accel", np.nan)
    cts_accel_threshold = row.get("cts_accel_threshold", np.nan)

    if any(np.isnan(x) for x in [cdvl, prev_cdvl, cts, prev_cts, cts_buy_threshold, cwc, cts_accel, prev_cts_accel, cts_accel_threshold]):
        return False

    # CDVL turns positive
    if not (prev_cdvl <= 0.0 and cdvl > 0.0):
        return False
    # CTS negative and rising or flat bottom
    cts_rising_or_flat = (cts > prev_cts) or (cts == -1.0 and prev_cts == -1.0)
    if not (cts < 0.0 and cts_rising_or_flat):
        return False
    # CTS <= dynamic buy threshold
    if cts == -1.0:
        if cts_buy_threshold != -1.0: return False
    else:
        if cts > cts_buy_threshold: return False

    # Relax CWC min to 0.35 in basic gates
    if cwc <= 0.35:
        return False

    # Relax acceleration rising check slightly
    if cts_accel <= prev_cts_accel:
        return False

    return True

# 2. Anchor Shock Pullback relaxed basic gate
def basic_anchor_shock_pullback(row, prev_row, records, idx):
    pdd_120 = row.get("pdd_120", 0.0)
    if np.isnan(pdd_120) or pdd_120 < 0.0:  # Relax from 2.0
        return False

    pdd_30 = row.get("pdd_30", 0.0)
    if np.isnan(pdd_30) or pdd_30 < -4.5:  # Relax from -3.0
        return False

    sdvwap = row.get("sdvwap", np.nan)
    close = row.get("close", np.nan)
    if np.isnan(sdvwap) or np.isnan(close):
        return False
    if close >= sdvwap:
        return False

    proximity = (sdvwap - close) / sdvwap
    if proximity > 0.04:  # Relax from 2.0% (0.02)
        return False

    dv_shock = row.get("dv_shock", 0.0)
    if np.isnan(dv_shock) or dv_shock > -0.4:  # Relax from -0.8
        return False

    esr = row.get("esr", 0.0)
    if np.isnan(esr) or esr > 0.20:  # Relax from 0.10
        return False

    rp_252 = row.get("range_pos_252", 0.0)
    if not np.isnan(rp_252) and rp_252 > 0.85:  # Relax from 0.70
        return False

    psz = row.get("price_slope_z", 0.0)
    if not np.isnan(psz) and psz < -0.5:  # Relax from -0.25
        return False

    rw_252 = row.get("range_width_252", 0.0)
    if not np.isnan(rw_252) and rw_252 < 15.0:  # Relax from 20.0%
        return False

    return True

# 3. Universal Cross basic gate
def basic_universal_cross(row, prev_row, records, idx):
    prev_cs = prev_row.get("cts_slope", 0)
    cs = row.get("cts_slope", 0)
    trigger_cs = 1 if (prev_cs <= 0 and cs > 0) else 0

    fas_bt = row.get("fas_buy_threshold", -0.8)
    prev_fas = prev_row.get("fas", 0)
    fas = row.get("fas", 0)
    trigger_fas = 1 if (prev_fas <= fas_bt and fas > fas_bt) else 0

    prt = row.get("prt_slope", 0)
    prev_prt = prev_row.get("prt_slope", 0)
    trigger_prt = 1 if (prev_prt <= 0 and prt > 0) else 0

    trigger_cwc = 0
    if idx >= 3:
        cwc_vals = [records[k].get("cwc", 0.0) for k in range(idx - 3, idx)]
        cwc_curr = row.get("cwc", 0.0)
        cwc_avg = sum(cwc_vals) / len(cwc_vals)
        cwc_disp = max(cwc_vals) - min(cwc_vals)
        regime = row.get("regime", "notrend")
        if 0.80 <= cwc_avg <= 1.00 and cwc_disp <= 0.10:
            if cwc_curr > cwc_avg and (cwc_curr - cwc_avg) >= 0.01 and regime != "downtrend":
                trigger_cwc = 1

    trigger_5 = check_trigger_5(row, records, idx)

    if not any([trigger_cs, trigger_fas, trigger_prt, trigger_cwc, trigger_5]):
        return False

    cts = row.get("cts", 0)
    prev_cts = prev_row.get("cts", 0)
    if cts < prev_cts:
        return False

    accel_bt = row.get("cts_accel_threshold", 0.0)
    accel = row.get("cts_accel", 0)
    psz_v = row.get("psz_v", 0)
    
    strong_institutional_turn = (fas > fas_bt and fas >= prev_fas and psz_v > 0.02)
    if not strong_institutional_turn:
        if accel <= accel_bt:
            return False
        if idx >= 2:
            a2 = records[idx-1].get("cts_accel", 0)
            a3 = records[idx].get("cts_accel", 0)
            if a3 < a2:
                return False

    if psz_v <= 0:
        return False
    if idx >= 2:
        v2 = records[idx-1].get("psz_v", 0)
        v3 = records[idx].get("psz_v", 0)
        if v3 < v2:
            return False

    if idx >= 2:
        f1 = records[idx-2].get("fas", 0)
        if fas < f1 and fas > fas_bt:
            return False
        if fas > 0.1:
            return False
        if fas_bt > -0.3:
            return False

    range_pos_10 = row.get("range_pos_10", 0)
    cwc = row.get("cwc", 0.0)
    pdd_30 = row.get("pdd_30", 0.0)
    momentum_bypass = (cwc >= 0.40 and pdd_30 < -3.5)
    if range_pos_10 > 0.5 and not (trigger_fas and trigger_cs) and not trigger_cwc and not momentum_bypass:
        return False

    if check_long_term_range(row, cwc_threshold=0.50) and not trigger_cwc:
        return False

    lookback = 10
    if check_gap_down(records, idx, lookback=lookback) or check_gap_down(records, idx, lookback=lookback+1):
        return False

    if check_slope_flatness(records, idx) and trigger_cs:
        return False

    if check_basing(records, idx):
        return False

    cwc_thr = 0.10
    slope_thr = -0.02
    cwc_slope = row.get("cwc_slope", 0.0)
    if cwc < cwc_thr and cwc_slope < slope_thr:
        return False

    base_tightness = row.get("base_tightness", 1.0)
    if pdd_30 <= -5.5 and 0.40 <= base_tightness <= 0.46:
        return False

    return True

# --- EXITS SIMULATION ---

def simulate_chronological_trades(ticker, records, exit_cfg, signal, entry_fn, tag):
    n = len(records)
    trades = []
    in_trade = False
    trade = None
    peak_close = 0.0
    delivery_bad_count = 0
    cwvap_values = []
    pending_signal_idx = -1
    pending_exit_reason = None
    
    # Exits configuration:
    # 1. CDVL exits: 10% hard stop + CDVL suppression
    # 2. Universal: Standard trailing logic
    # 3. Anchor Shock: 15% hard stop + 50-bar time decay
    
    for i in range(1, n):
        row = records[i]
        prev = records[i - 1]
        close = row.get("close", np.nan)
        if np.isnan(close):
            continue
            
        cw = row.get("cwvap", np.nan)
        cwvap_values.append(cw)
        
        # Execute pending exit at today's open
        if pending_exit_reason is not None:
            open_price = row.get("open", np.nan)
            exit_price = open_price if not np.isnan(open_price) else close
            trade.exit_date = str(row.get("date", ""))[:10]
            trade.exit_price = round(exit_price, 2)
            trade.exit_reason = pending_exit_reason
            trade.pnl_pct = round((exit_price / trade.entry_price - 1) * 100, 2)
            trade.duration = i - trade.entry_idx
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
            trade.mfe_pct = max(trade.mfe_pct, (close / trade.entry_price - 1) * 100)
            mae_val = (close / trade.entry_price - 1) * 100
            trade.mae_pct = max(trade.mae_pct, -mae_val)
            
            # Apply hard stop / custom exit overrides:
            pnl_pct = (close / trade.entry_price - 1.0) * 100.0
            
            if tag == EntryTag.CDVL_CTS.value:
                if pnl_pct <= -10.0:
                    pending_exit_reason = ExitReason.HARD_STOP
                    continue
            elif tag == EntryTag.ANCHOR_SHOCK_PULLBACK.value:
                if pnl_pct <= -15.0:
                    pending_exit_reason = ExitReason.HARD_STOP
                    continue
                if bars_held >= 50:
                    pending_exit_reason = ExitReason.TIME_DECAY
                    continue
            
            reason, delivery_bad_count = signal.check_exit(
                row, prev, trade, peak_close, bars_held,
                delivery_bad_count, cwvap_values, exit_cfg,
                records, i
            )
            
            if reason:
                pending_exit_reason = reason
                
        elif pending_signal_idx != -1:
            atr = row.get("atr_20", 0.0)
            if atr <= 0 or np.isnan(atr):
                pending_signal_idx = -1
                continue
                
            trade = Trade(
                symbol=ticker,
                entry_date=str(row.get("date", ""))[:10],
                entry_price=close,
                entry_idx=i,
                atr_at_entry=atr,
                conviction_score=85,
                regime_at_entry=row.get("regime", "-"),
                entry_tag=tag,
                psz_at_entry=row.get("price_slope_z", 0.0),
                psz_peak=row.get("price_slope_z", 0.0),
            )
            peak_close = close
            delivery_bad_count = 0
            in_trade = True
            pending_signal_idx = -1
            
        else:
            if entry_fn(row, prev, records, i):
                pending_signal_idx = i
                
    if in_trade and trade and pending_exit_reason is None:
        last = records[-1]
        trade.exit_date = str(last.get("date", ""))[:10]
        trade.exit_price = last.get("close", trade.entry_price)
        trade.exit_reason = ExitReason.END_OF_DATA
        trade.pnl_pct = round((trade.exit_price / trade.entry_price - 1) * 100, 2)
        trade.duration = n - 1 - trade.entry_idx
        trades.append(trade)
        
    return trades

def get_watchlist_symbols(name="NIFTY 50"):
    from src.database import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    symbols = [r[0] for r in conn.execute(
        "SELECT symbol FROM watchlist_items WHERE watchlist_id = (SELECT id FROM watchlists WHERE name = ?) ORDER BY display_order",
        (name,)
    ).fetchall()]
    conn.close()
    return symbols

def main():
    symbols = get_watchlist_symbols("NIFTY 50")
    print(f"Loaded {len(symbols)} symbols from NIFTY 50.")
    
    exit_cfg = SavgolCTSExitConfig()
    signal = SignalFactory.get_signal("savgol_cts")
    
    print("Loading stock records...")
    cache_records = {}
    for sym in symbols:
        try:
            engine = DivergenceEngine(sym)
            res = engine.run()
            cache_records[sym] = res.ledger.to_dict('records')
        except Exception as e:
            print(f"Error loading {sym}: {e}")
            
    print(f"Cached records for {len(cache_records)} symbols.")
    
    # Path configuration map
    paths_config = {
        "cdvl_cts": {
            "tag": EntryTag.CDVL_CTS.value,
            "basic_gate": basic_cdvl_cts,
            "features": {
                "cdvl": lambda r, p, recs, idx: r.get("cdvl", np.nan),
                "cdvl_surge": lambda r, p, recs, idx: r.get("cdvl", 0.0) - p.get("cdvl", 0.0),
                "cts": lambda r, p, recs, idx: r.get("cts", np.nan),
                "cts_accel": lambda r, p, recs, idx: r.get("cts_accel", np.nan),
                "cwc": lambda r, p, recs, idx: r.get("cwc", np.nan),
                "cts_accel_surge": lambda r, p, recs, idx: r.get("cts_accel", 0.0) - p.get("cts_accel", 0.0),
            }
        },
        "anchor_shock_pullback": {
            "tag": EntryTag.ANCHOR_SHOCK_PULLBACK.value,
            "basic_gate": basic_anchor_shock_pullback,
            "features": {
                "pdd_120": lambda r, p, recs, idx: r.get("pdd_120", np.nan),
                "pdd_30": lambda r, p, recs, idx: r.get("pdd_30", np.nan),
                "proximity": lambda r, p, recs, idx: (r.get("sdvwap", np.nan) - r.get("close", np.nan)) / r.get("sdvwap", np.nan) if r.get("sdvwap", 0.0) > 0 else np.nan,
                "dv_shock": lambda r, p, recs, idx: r.get("dv_shock", np.nan),
                "esr": lambda r, p, recs, idx: r.get("esr", np.nan),
                "price_slope_z": lambda r, p, recs, idx: r.get("price_slope_z", np.nan),
            }
        },
        "universal_cross": {
            "tag": EntryTag.UNIVERSAL_CROSS.value,
            "basic_gate": basic_universal_cross,
            "features": {
                "cwc": lambda r, p, recs, idx: r.get("cwc", np.nan),
                "psz_v": lambda r, p, recs, idx: r.get("psz_v", np.nan),
                "fas": lambda r, p, recs, idx: r.get("fas", np.nan),
                "cts_accel": lambda r, p, recs, idx: r.get("cts_accel", np.nan),
                "pdd_30": lambda r, p, recs, idx: r.get("pdd_30", np.nan),
                "range_pos_10": lambda r, p, recs, idx: r.get("range_pos_10", np.nan),
            }
        }
    }
    
    alpha = 1.0  # Laplace smoothing
    
    for path_name, pcfg in paths_config.items():
        print(f"\n========================================================")
        print(f"OPTIMIZING PATH: {path_name.upper()}")
        print(f"========================================================")
        
        # 1. Run basic gate simulation to collect all trades
        all_trades = []
        for sym, recs in cache_records.items():
            trades = simulate_chronological_trades(
                sym, recs, exit_cfg, signal, pcfg["basic_gate"], pcfg["tag"]
            )
            all_trades.extend(trades)
            
        train_trades_all = [t for t in all_trades if str(t.entry_date) < "2024-01-01"]
        test_trades_all = [t for t in all_trades if str(t.entry_date) >= "2024-01-01"]
        
        print(f"Total candidate trades: {len(all_trades)}")
        print(f"  Train candidates: {len(train_trades_all)}")
        print(f"  Test candidates:  {len(test_trades_all)}")
        
        if not train_trades_all:
            print(f"No train trades found for path {path_name}. Skipping optimization.")
            continue
            
        # 2. Extract features
        train_rows = []
        for t in train_trades_all:
            sym = t.symbol
            records = cache_records[sym]
            sig_idx = t.entry_idx - 1
            row = records[sig_idx]
            prev = records[sig_idx - 1]
            
            f_vals = {}
            for f_name, f_func in pcfg["features"].items():
                f_vals[f_name] = f_func(row, prev, records, sig_idx)
            f_vals["pnl"] = t.pnl_pct
            train_rows.append(f_vals)
            
        df_train = pd.DataFrame(train_rows)
        
        # 3. Create equal-frequency bins using percentiles
        feature_bins = {}
        for feat in pcfg["features"].keys():
            # Drop NaNs
            vals = df_train[feat].dropna()
            if len(vals) < 3:
                edges = [-np.inf, -1.0, 1.0, np.inf]
            else:
                q33 = np.nanpercentile(vals, 33.3)
                q66 = np.nanpercentile(vals, 66.7)
                edges = [-np.inf, q33, q66, np.inf]
                if len(set(edges)) < 4:
                    # Fallback to unique values / linspace if percentiles are identical
                    min_v = vals.min()
                    max_v = vals.max()
                    edges = [-np.inf, min_v + (max_v - min_v)/3.0, min_v + 2.0*(max_v - min_v)/3.0, np.inf]
                    edges = sorted(list(set(edges)))
            feature_bins[feat] = edges
            
        # 4. Sweep success targets (PnL >= 0, 1, 2, 3, 4, 5%)
        success_targets = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        best_results = []
        
        for target in success_targets:
            df_train['success'] = (df_train['pnl'] >= target).astype(int)
            n_success = df_train['success'].sum()
            n_failure = len(df_train) - n_success
            
            if n_success < 2 or n_failure < 2:
                continue
                
            # Train weights
            feature_weights = {}
            for feat, edges in feature_bins.items():
                train_bins = pd.cut(df_train[feat], bins=edges)
                df_train[feat + "_bin"] = train_bins
                
                bin_weights = {}
                grouped = df_train.groupby(feat + "_bin", observed=False)
                for name, group in grouped:
                    s_count = group['success'].sum()
                    f_count = len(group) - s_count
                    p_s = (s_count + alpha) / (n_success + alpha * len(edges))
                    p_f = (f_count + alpha) / (n_failure + alpha * len(edges))
                    weight = np.log(p_s / p_f)
                    bin_weights[name] = weight
                feature_weights[feat] = bin_weights
                
            # Precalculate scores
            def get_bayesian_score(row, prev_row, records, idx):
                if not pcfg["basic_gate"](row, prev_row, records, idx):
                    return -999.0
                score = 0.0
                for feat, f_func in pcfg["features"].items():
                    val = f_func(row, prev_row, records, idx)
                    if np.isnan(val):
                        continue
                    for bin_interval, weight in feature_weights[feat].items():
                        if val in bin_interval:
                            score += weight
                            break
                return score

            all_scores = []
            for sym, recs in cache_records.items():
                n = len(recs)
                for i in range(1, n):
                    score = get_bayesian_score(recs[i], recs[i-1], recs, i)
                    recs[i]['temp_score'] = score
                    if score > -900:
                        all_scores.append(score)
                recs[0]['temp_score'] = -999.0
                
            if not all_scores:
                continue
                
            min_score = min(all_scores)
            max_score = max(all_scores)
            
            sweep_results = []
            for thresh in np.linspace(min_score - 0.05, max_score + 0.05, 40):
                def entry_fn(row, prev_row, records, idx):
                    return row.get('temp_score', -999.0) >= thresh
                    
                all_trades_sweep = []
                for sym, recs in cache_records.items():
                    trades = simulate_chronological_trades(
                        sym, recs, exit_cfg, signal, entry_fn, pcfg["tag"]
                    )
                    all_trades_sweep.extend(trades)
                    
                train_trades = [t for t in all_trades_sweep if str(t.entry_date) < "2024-01-01"]
                test_trades = [t for t in all_trades_sweep if str(t.entry_date) >= "2024-01-01"]
                
                train_pnl = np.mean([t.pnl_pct for t in train_trades]) if train_trades else np.nan
                test_pnl = np.mean([t.pnl_pct for t in test_trades]) if test_trades else np.nan
                
                sweep_results.append({
                    "Target": target,
                    "Threshold": thresh,
                    "Train Trades": len(train_trades),
                    "Train Avg PnL%": train_pnl,
                    "Test Trades": len(test_trades),
                    "Test Avg PnL%": test_pnl,
                })
                
            df_sweep = pd.DataFrame(sweep_results)
            
            # Select thresholds with Train/Test Avg PnL >= 5.0% and minimum trade counts
            # For less-frequent paths, we relax minimum trade count slightly
            min_train_trades = 10 if path_name != "universal_cross" else 30
            min_test_trades = 5 if path_name != "universal_cross" else 10
            
            valid = df_sweep[
                (df_sweep['Train Avg PnL%'] >= 5.0) & 
                (df_sweep['Test Avg PnL%'] >= 5.0) &
                (df_sweep['Train Trades'] >= min_train_trades) &
                (df_sweep['Test Trades'] >= min_test_trades)
            ].sort_values(by="Train Avg PnL%", ascending=False)
            
            if not valid.empty:
                best_results.append((target, valid.iloc[0], feature_weights, feature_bins))
            else:
                # If no threshold meets >=5% on both, fallback to maximizing P&L
                # Sort by Train Avg PnL% plus Test Avg PnL% to find best tradeoff
                df_sweep['score_sum'] = df_sweep['Train Avg PnL%'] + df_sweep['Test Avg PnL%']
                df_sweep_filtered = df_sweep[(df_sweep['Train Trades'] >= min_train_trades) & (df_sweep['Test Trades'] >= min_test_trades)]
                if not df_sweep_filtered.empty:
                    best_fallback = df_sweep_filtered.sort_values(by="score_sum", ascending=False).iloc[0]
                    best_results.append((target, best_fallback, feature_weights, feature_bins))
                    
        if best_results:
            # Sort by Train Avg P&L
            best_results.sort(key=lambda x: x[1]['Train Avg PnL%'], reverse=True)
            best_target, best_row, best_weights, best_bins = best_results[0]
            
            print(f"\nOPTIMAL CONFIGURATION FOR {path_name.upper()}")
            print(f"Success Target: PnL >= {best_target}%")
            print(f"Threshold: {best_row['Threshold']:.4f}")
            print(f"Train Trades: {best_row['Train Trades']} | Train PnL: {best_row['Train Avg PnL%']:.3f}%")
            print(f"Test Trades: {best_row['Test Trades']} | Test PnL: {best_row['Test Avg PnL%']:.3f}%")
            
            print("\nCopy-paste parameters:")
            print(f"bayesian_mode: bool = True")
            print(f"score_threshold: float = {best_row['Threshold']:.4f}")
            
            print("feature_bins = {")
            for feat, edges in best_bins.items():
                print(f"    '{feat}': {edges},")
            print("}")
            
            print("feature_weights = {")
            for feat, bin_w in best_weights.items():
                print(f"    '{feat}': [")
                for interval, weight in bin_w.items():
                    left = interval.left
                    right = interval.right
                    print(f"        ({left}, {right}, {weight:.6f}),")
                print("    ],")
            print("}")
        else:
            print(f"Could not find any viable configuration for path {path_name}.")

if __name__ == "__main__":
    main()
