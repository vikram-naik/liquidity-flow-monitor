#!/usr/bin/env python3
"""
test_exit_filters.py — Test exit filter strategies to reduce whipsaw
────────────────────────────────────────────────────────────────────
Compares baseline backtester against two exit filter strategies:

A) Minimum hold period — ignore Signal_Exit for N bars after entry
B) VA zone exit filter — inside VA, only primary Supply can exit (no assisters)

Runs both independently, then combined, across multiple symbols.

Usage:
    python scripts/test_exit_filters.py --start 2024-01-01
    python scripts/test_exit_filters.py --start 2024-01-01 --symbols RELIANCE HDFCBANK
    python scripts/test_exit_filters.py --start 2024-01-01 --watchlist "NIFTY 50"
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass, field
from copy import deepcopy

import numpy as np
import pandas as pd
from tabulate import tabulate

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import sqlite3
from src.divergence_engine.engine import DivergenceEngine
from src.database import DB_PATH


# ─────────────────────────────────────────────────────────────────────────────
# Modified Backtester with exit filter support
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ExitFilterConfig:
    """Exit filter parameters (layered on top of base BacktestConfig)."""
    min_hold_bars: int = 0          # A: suppress Signal_Exit for N bars after entry
    va_primary_only: bool = False   # B: inside VA, only primary Supply exits (no assisters)


@dataclass
class BacktestConfig:
    symbol: str
    start_date: str
    end_date: str | None = None
    capital: float = 100_000.0
    stop_cwvap_pct: float = -2.0
    stop_entry_pct: float = -3.0
    chase_if_limit_misses: bool = True
    exclude_va: bool = False
    agg_mode: str = "daily"
    exit_filter: ExitFilterConfig = field(default_factory=ExitFilterConfig)


@dataclass
class Trade:
    entry_date: str
    entry_price: float
    quantity: int
    capital_used: float
    signal: str
    entry_bar_idx: int = 0  # track bar index for min hold


@dataclass
class ClosedTrade:
    trade: Trade
    exit_date: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.trade.entry_price) * self.trade.quantity

    @property
    def pnl_pct(self) -> float:
        return (self.pnl / self.trade.capital_used * 100) if self.trade.capital_used > 0 else 0.0

    @property
    def holding_days(self) -> int:
        if not self.exit_date:
            return 0
        return max(1, (pd.Timestamp(self.exit_date) - pd.Timestamp(self.trade.entry_date)).days)


@dataclass
class PendingOrder:
    limit_price: float
    signal: str
    capital_to_use: float


def _isnan(val) -> bool:
    if val is None:
        return True
    try:
        return math.isnan(float(val))
    except (TypeError, ValueError):
        return True


class FilterBacktester:
    def __init__(self, config: BacktestConfig):
        self.cfg = config
        self.cash: float = config.capital
        self.initial_capital: float = config.capital
        self.in_position: bool = False
        self.active_trade: Trade | None = None
        self._pending_entry: PendingOrder | None = None
        self._stop_level: float | None = None
        self.closed_trades: list[ClosedTrade] = []
        self.equity_curve: list[dict] = []

    def run(self) -> dict:
        engine = DivergenceEngine(ticker=self.cfg.symbol, agg_mode=self.cfg.agg_mode)
        result = engine.run()
        ledger = result.ledger.copy()
        ledger["_date_str"] = ledger["date"].astype(str).str[:10]

        start_mask = ledger["_date_str"] >= self.cfg.start_date
        if not start_mask.any():
            return {}
        start_idx = start_mask.idxmax()

        end_idx = len(ledger)
        if self.cfg.end_date:
            end_mask = ledger["_date_str"] <= self.cfg.end_date
            if end_mask.any():
                end_idx = end_mask[::-1].idxmax() + 1

        ef = self.cfg.exit_filter
        n = end_idx
        bar_counter = 0  # running bar index within backtest window

        for i in range(start_idx, n):
            bar = ledger.iloc[i]
            date = bar["_date_str"]
            o = float(bar["open"])
            h = float(bar["high"])
            l = float(bar["low"])
            c = float(bar["close"])
            cwvap_val = float(bar["cwvap"]) if not _isnan(bar.get("cwvap")) else None
            va_high_val = float(bar["va_high"]) if not _isnan(bar.get("va_high")) else None
            va_low_val = float(bar.get("va_low")) if not _isnan(bar.get("va_low")) else None
            signal = bar.get("cei_signal")

            # Phase 1: CWVAP trailing stop (always active, ignores min_hold)
            if self.in_position and self._stop_level is not None:
                stop_hit, stop_price = self._check_stop_trigger(o, l)
                if stop_hit:
                    self._execute_exit(date, stop_price, "CWVAP_Stop")

            # Phase 2: Fill pending entry
            if self._pending_entry is not None and not self.in_position:
                filled_price = self._check_fill(self._pending_entry, o, h, l)
                if filled_price is not None:
                    self._execute_entry(self._pending_entry, date, filled_price, bar_counter)
                    if va_high_val is not None and c >= va_high_val:
                        if cwvap_val and cwvap_val > 0:
                            self._stop_level = self._compute_stop_level(cwvap_val, c)
                    else:
                        self._stop_level = None
                self._pending_entry = None

            # Phase 3: EOD update stop
            if self.in_position:
                if va_high_val is not None and c >= va_high_val:
                    if cwvap_val and cwvap_val > 0:
                        self._stop_level = self._compute_stop_level(cwvap_val, c)
                else:
                    self._stop_level = None

            # Phase 4: Signal-based exit + entry
            if self.in_position and signal in ("Supply", "Supply_Assister"):
                if va_high_val is not None and c < va_high_val:
                    # --- Exit filter logic ---
                    allow_exit = True

                    # Filter A: min hold period
                    if ef.min_hold_bars > 0 and self.active_trade is not None:
                        bars_held = bar_counter - self.active_trade.entry_bar_idx
                        if bars_held < ef.min_hold_bars:
                            allow_exit = False

                    # Filter B: VA primary-only exit
                    if allow_exit and ef.va_primary_only:
                        # Inside VA = between va_low and va_high
                        if va_low_val is not None and va_high_val is not None:
                            inside_va = va_low_val <= c <= va_high_val
                            if inside_va and signal == "Supply_Assister":
                                allow_exit = False

                    if allow_exit:
                        self._execute_exit(date, c, "Signal_Exit")

            if not self.in_position and self._pending_entry is None:
                self._check_signal_for_entry(signal, c, cwvap_val, va_high_val, va_low_val)

            # Phase 5: Equity
            equity = self._mark_to_market(c)
            self.equity_curve.append({"date": date, "equity": equity, "close": c})
            bar_counter += 1

        # Force close
        if self.in_position and self.active_trade:
            c = float(ledger["close"].iloc[-1])
            date = ledger["_date_str"].iloc[-1]
            self._execute_exit(date, c, "End_of_Period")
            equity = self._mark_to_market(c)
            self.equity_curve[-1]["equity"] = equity

        return self._build_summary()

    def _check_fill(self, po, open_, high, low):
        if low <= po.limit_price:
            return po.limit_price
        if self.cfg.chase_if_limit_misses:
            return open_
        return None

    def _execute_entry(self, po, date, price, bar_idx):
        if self.in_position:
            return
        capital = min(po.capital_to_use, self.cash)
        if capital < price or price <= 0:
            return
        qty = int(capital // price)
        if qty <= 0:
            return
        actual_capital = qty * price
        self.cash -= actual_capital
        self.in_position = True
        self.active_trade = Trade(
            entry_date=date, entry_price=price, quantity=qty,
            capital_used=actual_capital, signal=po.signal, entry_bar_idx=bar_idx,
        )

    def _execute_exit(self, date, price, reason):
        if not self.in_position or self.active_trade is None:
            return
        closed = ClosedTrade(
            trade=self.active_trade, exit_date=date,
            exit_price=price, exit_reason=reason,
        )
        self.cash += self.active_trade.quantity * price
        self.closed_trades.append(closed)
        self.in_position = False
        self.active_trade = None
        self._stop_level = None

    def _compute_stop_level(self, cwvap, close):
        cwvap_stop = cwvap * (1.0 + self.cfg.stop_cwvap_pct / 100.0)
        if self.active_trade is not None and cwvap_stop >= close:
            return self.active_trade.entry_price * (1.0 + self.cfg.stop_entry_pct / 100.0)
        return cwvap_stop

    def _check_stop_trigger(self, open_, low):
        level = self._stop_level
        if level is None:
            return False, 0.0
        if open_ <= level:
            return True, open_
        if low <= level:
            return True, level
        return False, 0.0

    def _check_signal_for_entry(self, signal, close, cwvap, va_high=None, va_low=None):
        if not signal:
            return
        if self.cfg.exclude_va:
            if va_high is not None and va_low is not None:
                if close >= va_low and close <= va_high:
                    return
        above_cwvap = cwvap is not None and cwvap > 0 and close >= cwvap
        if signal == "Demand":
            self._pending_entry = PendingOrder(limit_price=close, signal=signal, capital_to_use=float("inf"))
        elif signal == "Demand_Assister" and not above_cwvap:
            self._pending_entry = PendingOrder(limit_price=close, signal=signal, capital_to_use=float("inf"))

    def _mark_to_market(self, close):
        if self.in_position and self.active_trade is not None:
            return self.cash + self.active_trade.quantity * close
        return self.cash

    def _build_summary(self):
        trades = self.closed_trades
        pnls = [t.pnl for t in trades]
        winners = [t for t in trades if t.pnl > 0]
        losers = [t for t in trades if t.pnl <= 0]
        gross_profit = sum(t.pnl for t in winners) if winners else 0.0
        gross_loss = abs(sum(t.pnl for t in losers)) if losers else 0.0

        equity_vals = [e["equity"] for e in self.equity_curve]
        peak = max_dd_pct = 0.0
        for eq in equity_vals:
            if eq > peak:
                peak = eq
            if peak > 0:
                dd_pct = (peak - eq) / peak * 100.0
                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct

        short_holds = len([t for t in trades if t.holding_days <= 5])

        return {
            "symbol": self.cfg.symbol,
            "final_equity": equity_vals[-1] if equity_vals else self.initial_capital,
            "net_pnl": equity_vals[-1] - self.initial_capital if equity_vals else 0,
            "net_pnl_pct": (equity_vals[-1] / self.initial_capital - 1) * 100 if equity_vals else 0,
            "total_trades": len(trades),
            "winners": len(winners),
            "win_rate": (len(winners) / len(trades) * 100) if trades else 0,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
            "max_dd_pct": max_dd_pct,
            "avg_hold": np.mean([t.holding_days for t in trades]) if trades else 0,
            "short_holds": short_holds,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_watchlist_symbols(name: str) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT i.symbol FROM watchlist_items i "
            "JOIN watchlists w ON i.watchlist_id = w.id "
            "WHERE w.name = ? ORDER BY i.display_order, i.symbol",
            (name,),
        )
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def run_variant(symbols: list[str], start: str, end: str | None,
                exit_filter: ExitFilterConfig, label: str) -> list[dict]:
    """Run backtester across symbols with given exit filter config."""
    results = []
    for sym in symbols:
        cfg = BacktestConfig(
            symbol=sym, start_date=start, end_date=end,
            chase_if_limit_misses=True, exit_filter=exit_filter,
        )
        bt = FilterBacktester(cfg)
        try:
            summary = bt.run()
            if summary:
                summary["label"] = label
                results.append(summary)
        except Exception as e:
            print(f"    ERROR {sym}: {e}")
    return results


def aggregate_results(results: list[dict]) -> dict:
    """Aggregate per-symbol results into portfolio-level stats."""
    if not results:
        return {}
    total_pnl = sum(r["net_pnl"] for r in results)
    total_trades = sum(r["total_trades"] for r in results)
    total_winners = sum(r["winners"] for r in results)
    total_short = sum(r["short_holds"] for r in results)
    avg_dd = np.mean([r["max_dd_pct"] for r in results])
    avg_hold = np.mean([r["avg_hold"] for r in results if r["avg_hold"] > 0])

    # Portfolio PF: sum of gross profits / sum of gross losses
    # Approximate from per-symbol P&L
    winners_pnl = sum(r["net_pnl"] for r in results if r["net_pnl"] > 0)
    losers_pnl = abs(sum(r["net_pnl"] for r in results if r["net_pnl"] <= 0))
    pf = winners_pnl / losers_pnl if losers_pnl > 0 else float("inf")

    return {
        "n_symbols": len(results),
        "total_pnl": total_pnl,
        "total_trades": total_trades,
        "total_winners": total_winners,
        "win_rate": total_winners / total_trades * 100 if total_trades > 0 else 0,
        "avg_dd_pct": avg_dd,
        "avg_hold": avg_hold,
        "short_holds": total_short,
        "portfolio_pf": pf,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Exit Filter Strategy Test")
    parser.add_argument("--symbols", nargs="+", default=["RELIANCE", "COALINDIA", "LT"])
    parser.add_argument("--watchlist", help="Use watchlist symbols instead")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", default=None)
    args = parser.parse_args()

    symbols = args.symbols
    if args.watchlist:
        symbols = get_watchlist_symbols(args.watchlist)
        if not symbols:
            print(f"  ERROR: Watchlist '{args.watchlist}' not found.")
            sys.exit(1)

    print(f"\n  Exit Filter Test — {len(symbols)} symbols from {args.start}")
    print(f"  Symbols: {', '.join(symbols[:10])}{'...' if len(symbols) > 10 else ''}\n")

    # Define variants to test
    variants = [
        (ExitFilterConfig(), "Baseline"),
        # Filter A: min hold period sweep
        (ExitFilterConfig(min_hold_bars=3), "A: MinHold 3"),
        (ExitFilterConfig(min_hold_bars=5), "A: MinHold 5"),
        (ExitFilterConfig(min_hold_bars=7), "A: MinHold 7"),
        (ExitFilterConfig(min_hold_bars=10), "A: MinHold 10"),
        # Filter B: VA primary-only exit
        (ExitFilterConfig(va_primary_only=True), "B: VA Primary"),
        # Combined A+B
        (ExitFilterConfig(min_hold_bars=5, va_primary_only=True), "A+B: MH5+VAPri"),
        (ExitFilterConfig(min_hold_bars=7, va_primary_only=True), "A+B: MH7+VAPri"),
    ]

    all_results = {}

    for ef, label in variants:
        print(f"  Running: {label}...", end="", flush=True)
        results = run_variant(symbols, args.start, args.end, ef, label)
        all_results[label] = results
        agg = aggregate_results(results)
        print(f" done ({agg.get('total_trades', 0)} trades)")

    # ── Per-symbol comparison table ──
    print(f"\n{'=' * 130}")
    print(f"  PER-SYMBOL RESULTS")
    print(f"{'=' * 130}")

    for sym in symbols:
        rows = []
        for ef, label in variants:
            sym_results = [r for r in all_results[label] if r["symbol"] == sym]
            if not sym_results:
                continue
            r = sym_results[0]
            pf = r["profit_factor"]
            pf_str = f"{pf:.2f}" if pf < 1000 else "inf"
            rows.append([
                label,
                f"{r['net_pnl_pct']:+.1f}%",
                r["total_trades"],
                f"{r['win_rate']:.0f}%",
                pf_str,
                f"{r['max_dd_pct']:.1f}%",
                f"{r['avg_hold']:.0f}d",
                r["short_holds"],
            ])

        print(f"\n  {sym}")
        print(tabulate(rows,
            headers=["Variant", "Net P&L", "Trades", "WR", "PF", "MaxDD", "AvgHold", "Short≤5d"],
            tablefmt="simple", stralign="right",
        ))

    # ── Aggregate comparison ──
    print(f"\n{'=' * 130}")
    print(f"  AGGREGATE ({len(symbols)} symbols)")
    print(f"{'=' * 130}")

    agg_rows = []
    for ef, label in variants:
        agg = aggregate_results(all_results[label])
        if not agg:
            continue
        pf = agg["portfolio_pf"]
        pf_str = f"{pf:.2f}" if pf < 1000 else "inf"
        agg_rows.append([
            label,
            f"{agg['total_pnl']:+,.0f}",
            agg["total_trades"],
            f"{agg['win_rate']:.0f}%",
            pf_str,
            f"{agg['avg_dd_pct']:.1f}%",
            f"{agg['avg_hold']:.0f}d",
            agg["short_holds"],
        ])

    print()
    print(tabulate(agg_rows,
        headers=["Variant", "Total P&L", "Trades", "WR", "PF", "AvgDD", "AvgHold", "Short≤5d"],
        tablefmt="simple", stralign="right",
    ))
    print()


if __name__ == "__main__":
    main()
