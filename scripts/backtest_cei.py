#!/usr/bin/env python3
"""
backtest_cei.py — CEI Signal Backtester (Long Only)
──────────────────────────────────────────────────────────────────────────
Walk-forward backtest of CEI Demand signals on a single stock using the
Divergence Engine pipeline.

Execution model (EOD system):
  Entry  — Limit order at signal-day close, checked against next bar's range.
  Exit   — Trailing CWVAP stop: trigger order updated every EOD, checked
           intraday against next bar's OHLC.

Entry rules:
  Above CWVAP — only primary Demand signals can open.
  Below CWVAP — Demand or Demand_Assister, whichever comes first.

Usage:
    python scripts/backtest_cei.py RELIANCE --start 2024-01-01
    python scripts/backtest_cei.py --watchlist "NIFTY 50" --start 2024-01-01
    python scripts/backtest_cei.py INFY --start 2024-01-01 --capital 200000 --export
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from tabulate import tabulate

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import sqlite3
from src.divergence_engine.engine import DivergenceEngine
from src.database import DB_PATH


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BacktestConfig:
    symbol: str
    start_date: str
    end_date: str | None = None
    capital: float = 100_000.0

    # Trailing CWVAP stop (sole exit mechanism)
    stop_cwvap_pct: float = -2.0  # exit when price <= cwvap * (1 + pct/100)
    stop_entry_pct: float = -3.0  # floor: never stop tighter than entry * (1 + pct/100)

    # Execution
    chase_if_limit_misses: bool = False  # fill at open if limit order misses
    exclude_va: bool = False             # if True, no entry within VA (va_low <= price <= va_high)
    agg_mode: str = "daily"              # "daily", "weekly", or "monthly"


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    """A single long entry."""
    entry_date: str
    entry_price: float
    quantity: int
    capital_used: float  # qty * entry_price
    signal: str  # "Demand" or "Demand_Assister"


@dataclass
class ClosedTrade:
    """A completed round-trip long trade."""
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
    """A limit buy order waiting to fill on the next bar."""
    limit_price: float
    signal: str
    capital_to_use: float


# ─────────────────────────────────────────────────────────────────────────────
# Backtester
# ─────────────────────────────────────────────────────────────────────────────

class Backtester:
    def __init__(self, config: BacktestConfig):
        self.cfg = config

        # Capital tracking
        self.cash: float = config.capital
        self.initial_capital: float = config.capital

        # Position state
        self.in_position: bool = False
        self.active_trade: Trade | None = None

        # Pending orders (execute on next bar)
        self._pending_entry: PendingOrder | None = None

        # Active CWVAP trailing stop (trigger order — checked intraday)
        # Computed at EOD, active for the NEXT trading day.
        self._stop_level: float | None = None

        # Results
        self.closed_trades: list[ClosedTrade] = []
        self.equity_curve: list[dict] = []
        self.daily_log: list[dict] = []

    # ── Public API ───────────────────────────────────────────────────────

    def run(self) -> dict:
        """Run the full backtest. Returns summary dict."""
        print(f"  Running Divergence Engine for {self.cfg.symbol} ({self.cfg.agg_mode})...")
        engine = DivergenceEngine(ticker=self.cfg.symbol, agg_mode=self.cfg.agg_mode)
        result = engine.run()
        ledger = result.ledger.copy()

        # Ensure date column is string for comparisons
        ledger["_date_str"] = ledger["date"].astype(str).str[:10]

        # Find the start index
        start_mask = ledger["_date_str"] >= self.cfg.start_date
        if not start_mask.any():
            print(f"    ERROR: No data on or after {self.cfg.start_date}")
            return {}
        start_idx = start_mask.idxmax()

        end_idx = len(ledger)
        if self.cfg.end_date:
            end_mask = ledger["_date_str"] <= self.cfg.end_date
            if end_mask.any():
                end_idx = end_mask[::-1].idxmax() + 1

        n = end_idx
        # print(f"    Walking forward: {start_idx} → {n-1} ({n-start_idx} trading days)")

        # ── Walk-forward loop ────────────────────────────────────────────
        for i in range(start_idx, n):
            bar = ledger.iloc[i]
            date = bar["_date_str"]
            o = float(bar["open"])
            h = float(bar["high"])
            l = float(bar["low"])
            c = float(bar["close"])
            cwvap_val = float(bar["cwvap"]) if not _isnan(bar.get("cwvap")) else None
            va_high_val = float(bar["va_high"]) if not _isnan(bar.get("va_high")) else None
            signal = bar.get("cei_signal")

            # Daily log entry — built up through each phase
            day = {
                "date": date,
                "close": c,
                "cwvap": cwvap_val,
                "signal": signal or "",
                "action": "",
                "entry_price": None,
                "exit_price": None,
                "exit_reason": "",
                "realized_pnl": None,
                "realized_pnl_pct": None,
                "stop_eod": None,
                "in_trade": False,
                "unrl_pnl": None,
            }

            # ── Phase 1: Check CWVAP trailing stop (intraday via OHLC) ──
            if self.in_position and self._stop_level is not None:
                stop_hit, stop_price = self._check_stop_trigger(o, l)
                if stop_hit:
                    closed = self._execute_exit(date, stop_price, "CWVAP_Stop")
                    day["action"] = "EXIT"
                    day["exit_price"] = stop_price
                    day["exit_reason"] = "CWVAP_Stop"
                    if closed:
                        day["realized_pnl"] = closed.pnl
                        day["realized_pnl_pct"] = closed.pnl_pct

            # ── Phase 2: Try filling pending entry order ──
            if self._pending_entry is not None and not self.in_position:
                filled_price = self._check_fill(self._pending_entry, o, h, l)
                if filled_price is not None:
                    self._execute_entry(self._pending_entry, date, filled_price)
                    day["action"] = ("EXIT+ENTRY" if day["action"] == "EXIT"
                                     else "ENTRY")
                    day["entry_price"] = filled_price
                    if va_high_val is not None and c >= va_high_val:
                        if cwvap_val and cwvap_val > 0:
                            self._stop_level = self._compute_stop_level(cwvap_val, c)
                    else:
                        self._stop_level = None
                self._pending_entry = None

            # ── Phase 3: EOD — Update Exit Strategy for next session ──
            if self.in_position:
                if va_high_val is not None and c >= va_high_val:
                    if cwvap_val and cwvap_val > 0:
                        self._stop_level = self._compute_stop_level(cwvap_val, c)
                else:
                    self._stop_level = None

            # ── Phase 4: EOD — Exit Signals / Entry Signals ──
            if self.in_position:
                if va_high_val is not None and c < va_high_val:
                    if signal in ["Supply", "Supply_Assister"]:
                        closed = self._execute_exit(date, c, "Signal_Exit")
                        day["action"] = "EXIT" if not day["action"] else f"{day['action']}+EXIT"
                        day["exit_price"] = c
                        day["exit_reason"] = "Signal_Exit"
                        if closed:
                            day["realized_pnl"] = closed.pnl
                            day["realized_pnl_pct"] = closed.pnl_pct

            if not self.in_position and self._pending_entry is None:
                va_low_val = float(bar.get("va_low")) if not _isnan(bar.get("va_low")) else None
                self._check_signal_for_entry(signal, c, cwvap_val, va_high_val, va_low_val)
                if self._pending_entry is not None:
                    if not day["action"]:
                        day["action"] = "ORDER"

            # ── Phase 5: Record equity + daily log ──
            day["in_trade"] = self.in_position
            day["stop_eod"] = self._stop_level
            if self.in_position and self.active_trade is not None:
                day["unrl_pnl"] = round(
                    (c - self.active_trade.entry_price) * self.active_trade.quantity, 2
                )
            equity = self._mark_to_market(c)
            self.equity_curve.append({"date": date, "equity": equity, "close": c})
            self.daily_log.append(day)

        # ── Force-close any open position at last bar's close ──
        if self.in_position and self.active_trade:
            c = float(ledger["close"].iloc[-1])
            date = ledger["_date_str"].iloc[-1]
            closed = self._execute_exit(date, c, "End_of_Period")
            if self.daily_log:
                self.daily_log[-1]["exit_price"] = c
                self.daily_log[-1]["exit_reason"] = "End_of_Period"
                if closed:
                    self.daily_log[-1]["realized_pnl"] = closed.pnl
                    self.daily_log[-1]["realized_pnl_pct"] = closed.pnl_pct
            equity = self._mark_to_market(c)
            self.equity_curve[-1]["equity"] = equity

        return self._build_summary()

    # ── Order fill check ─────────────────────────────────────────────────

    def _check_fill(
        self, po: PendingOrder, open_: float, high: float, low: float,
    ) -> float | None:
        """Check if a pending limit buy fills on this bar."""
        if low <= po.limit_price:
            return po.limit_price
        if self.cfg.chase_if_limit_misses:
            return open_
        return None

    # ── Entry execution ──────────────────────────────────────────────────

    def _execute_entry(self, po: PendingOrder, date: str, price: float) -> None:
        """Fill a long entry — 100% of available capital."""
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
            entry_date=date,
            entry_price=price,
            quantity=qty,
            capital_used=actual_capital,
            signal=po.signal,
        )

    # ── Exit execution ───────────────────────────────────────────────────

    def _execute_exit(self, date: str, price: float, reason: str) -> ClosedTrade | None:
        """Close the long position at the given price."""
        if not self.in_position or self.active_trade is None:
            return None

        closed = ClosedTrade(
            trade=self.active_trade,
            exit_date=date,
            exit_price=price,
            exit_reason=reason,
        )

        # Return proceeds to cash
        self.cash += self.active_trade.quantity * price
        self.closed_trades.append(closed)

        self.in_position = False
        self.active_trade = None
        self._stop_level = None

        return closed

    # ── CWVAP trailing stop — bracket-order style ────────────────────────

    def _compute_stop_level(self, cwvap: float, close: float) -> float:
        """Compute the trailing stop trigger price."""
        cwvap_stop = cwvap * (1.0 + self.cfg.stop_cwvap_pct / 100.0)

        if self.active_trade is not None and cwvap_stop >= close:
            entry_floor = self.active_trade.entry_price * (1.0 + self.cfg.stop_entry_pct / 100.0)
            return entry_floor

        return cwvap_stop

    def _check_stop_trigger(
        self, open_: float, low: float,
    ) -> tuple[bool, float]:
        """Check if today's OHLC triggers the active stop order."""
        level = self._stop_level
        if level is None:
            return False, 0.0

        if open_ <= level:
            return True, open_
        if low <= level:
            return True, level

        return False, 0.0

    # ── Entry signal checks (EOD) ────────────────────────────────────────

    def _check_signal_for_entry(
        self, signal: str | None, close: float, cwvap: float | None,
        va_high: float | None = None, va_low: float | None = None,
    ) -> None:
        """Check if a Demand signal warrants a long entry.

        Price-zone rules:
          Above CWVAP — only primary Demand can open.
          Below CWVAP — Demand or Demand_Assister, whichever comes first.
          Exclude VA — if cfg.exclude_va is True, price must be OUTSIDE [va_low, va_high].
        """
        if not signal:
            return

        # 1. VA exclusion check
        if self.cfg.exclude_va:
            if va_high is not None and va_low is not None:
                if close >= va_low and close <= va_high:
                    return

        above_cwvap = cwvap is not None and cwvap > 0 and close >= cwvap

        if signal == "Demand":
            self._pending_entry = PendingOrder(
                limit_price=close,
                signal=signal,
                capital_to_use=float("inf"),
            )
        elif signal == "Demand_Assister" and not above_cwvap:
            self._pending_entry = PendingOrder(
                limit_price=close,
                signal=signal,
                capital_to_use=float("inf"),
            )

    # ── Mark to market ───────────────────────────────────────────────────

    def _mark_to_market(self, current_close: float) -> float:
        """Compute total equity (cash + unrealised position value)."""
        if self.in_position and self.active_trade is not None:
            return self.cash + self.active_trade.quantity * current_close
        return self.cash

    # ── Reporting ────────────────────────────────────────────────────────

    def _build_summary(self) -> dict:
        """Compute and return summary statistics."""
        trades = self.closed_trades
        pnls = [t.pnl for t in trades]
        winners = [t for t in trades if t.pnl > 0]
        losers = [t for t in trades if t.pnl <= 0]
        gross_profit = sum(t.pnl for t in winners) if winners else 0.0
        gross_loss = abs(sum(t.pnl for t in losers)) if losers else 0.0

        equity_vals = [e["equity"] for e in self.equity_curve]
        peak = 0.0
        max_dd = 0.0
        max_dd_pct = 0.0
        for eq in equity_vals:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd
            if peak > 0:
                dd_pct = (dd / peak) * 100.0
                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct

        return {
            "symbol": self.cfg.symbol,
            "initial_capital": self.initial_capital,
            "final_equity": equity_vals[-1] if equity_vals else self.initial_capital,
            "net_pnl": equity_vals[-1] - self.initial_capital if equity_vals else 0,
            "net_pnl_pct": (equity_vals[-1] / self.initial_capital - 1) * 100 if equity_vals else 0,
            "total_trades": len(trades),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": (len(winners) / len(trades) * 100) if trades else 0,
            "avg_win": (gross_profit / len(winners)) if winners else 0,
            "avg_win_pct": (np.mean([t.pnl_pct for t in winners])) if winners else 0,
            "avg_loss": (-gross_loss / len(losers)) if losers else 0,
            "avg_loss_pct": (np.mean([t.pnl_pct for t in losers])) if losers else 0,
            "largest_win": max(pnls) if pnls else 0,
            "largest_loss": min(pnls) if pnls else 0,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
            "expectancy": np.mean(pnls) if pnls else 0,
            "max_drawdown": max_dd,
            "max_drawdown_pct": max_dd_pct,
            "avg_holding_days": np.mean([t.holding_days for t in trades]) if trades else 0,
            "start_date": self.equity_curve[0]["date"] if self.equity_curve else "",
            "end_date": self.equity_curve[-1]["date"] if self.equity_curve else "",
            "trading_days": len(self.equity_curve)
        }


# ─────────────────────────────────────────────────────────────────────────────
# Watchlist Helper
# ─────────────────────────────────────────────────────────────────────────────

def get_watchlist_symbols(watchlist_name: str) -> list[str]:
    """Retrieve symbols for a given watchlist name from the database."""
    conn = sqlite3.connect(DB_PATH)
    try:
        query = """
        SELECT i.symbol
        FROM watchlist_items i
        JOIN watchlists w ON i.watchlist_id = w.id
        WHERE w.name = ?
        ORDER BY i.display_order ASC, i.symbol ASC
        """
        cursor = conn.execute(query, (watchlist_name,))
        symbols = [row[0] for row in cursor.fetchall()]
        return symbols
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _isnan(val) -> bool:
    """Check if a value is NaN (handles None and non-numeric)."""
    if val is None:
        return True
    try:
        if isinstance(val, (int, float)):
            return math.isnan(val)
        return math.isnan(float(val))
    except (TypeError, ValueError):
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Report printer
# ─────────────────────────────────────────────────────────────────────────────

def print_report(bt: Backtester, compact: bool = False) -> None:
    """Print backtest summary stats."""
    s = bt._build_summary()
    cfg = bt.cfg

    if compact:
        # Compact tabular format for watchlist mode
        net_sign = "+" if s["net_pnl"] >= 0 else ""
        table_data = [
            ["Starting Capital", f"{s['initial_capital']:,.2f}"],
            ["Final Equity", f"{s['final_equity']:,.2f}"],
            ["Net P&L", f"{net_sign}{s['net_pnl']:,.2f} ({net_sign}{s['net_pnl_pct']:.2f}%)"]
        ]
        print(f"\n📈  {s['symbol']} Summary:")
        print(tabulate(table_data, tablefmt="simple"))
        return

    w = 120
    print(f"\n{'=' * w}")
    print(f"  CEI BACKTEST SUMMARY — {s.get('symbol', cfg.symbol)}  ({cfg.agg_mode.capitalize()})")
    if s.get("trading_days"):
        print(f"  Period: {s['start_date']} -> {s['end_date']} ({s['trading_days']} bars)")
    print(f"  Entry: Demand (any) + Assister (below CWVAP) | Stop: {cfg.stop_cwvap_pct:+.1f}% CWVAP (floor: {cfg.stop_entry_pct:+.1f}% entry)")
    
    if s.get("total_trades", 0) >= 0:
        net_sign = "+" if s["net_pnl"] >= 0 else ""
        print(f"\n  Starting Capital     {s['initial_capital']:>14,.2f}")
        print(f"  Final Equity         {s['final_equity']:>14,.2f}")
        print(f"  Net P&L              {net_sign}{s['net_pnl']:>13,.2f}  ({net_sign}{s['net_pnl_pct']:.2f}%)")

        if s["total_trades"] > 0:
            print(f"\n  Total Trades         {s['total_trades']:>8d}")
            print(f"  Winners              {s['winners']:>8d}  ({s['win_rate']:.1f}%)")
            print(f"  Losers               {s['losers']:>8d}  ({100-s['win_rate']:.1f}%)")

            print(f"\n  Avg Win              {s['avg_win']:>+14,.2f}  ({s['avg_win_pct']:+.2f}%)")
            print(f"  Avg Loss             {s['avg_loss']:>+14,.2f}  ({s['avg_loss_pct']:+.2f}%)")
            print(f"  Largest Win          {s['largest_win']:>+14,.2f}")
            print(f"  Largest Loss         {s['largest_loss']:>+14,.2f}")

            pf = s["profit_factor"]
            pf_str = f"{pf:.2f}" if pf < 1000 else "inf"
            print(f"\n  Profit Factor        {pf_str:>8s}")
            print(f"  Expectancy           {s['expectancy']:>+14,.2f}")
            print(f"  Max Drawdown         {s['max_drawdown']:>14,.2f}  ({s['max_drawdown_pct']:.2f}%)")
            print(f"  Avg Holding Days     {s['avg_holding_days']:>8.1f}")
        else:
            print("\n  No trades generated in the backtest period.")

    print(f"\n{'=' * w}")


def export_trades_csv(bt: Backtester, symbol: str, output_dir: str = ".") -> None:
    """Export trade log and equity curve to CSV files."""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    trades = bt.closed_trades
    base_path = os.path.join(output_dir, f"{symbol}_backtest")

    if trades:
        rows = []
        for i, t in enumerate(trades, 1):
            rows.append({
                "trade_no": i,
                "entry_date": t.trade.entry_date,
                "entry_price": round(t.trade.entry_price, 2),
                "quantity": t.trade.quantity,
                "capital_used": round(t.trade.capital_used, 2),
                "signal": t.trade.signal,
                "exit_date": t.exit_date,
                "exit_price": round(t.exit_price, 2),
                "exit_reason": t.exit_reason,
                "pnl": round(t.pnl, 2),
                "pnl_pct": round(t.pnl_pct, 2),
                "holding_days": t.holding_days,
            })
        df_trades = pd.DataFrame(rows)
        trades_path = f"{base_path}_trades.csv"
        df_trades.to_csv(trades_path, index=False)
        print(f"    Trades exported: {trades_path}")

    df_eq = pd.DataFrame(bt.equity_curve)
    eq_path = f"{base_path}_equity.csv"
    df_eq.to_csv(eq_path, index=False)
    print(f"    Equity curve exported: {eq_path}")

    df_daily = pd.DataFrame(bt.daily_log)
    daily_path = f"{base_path}_daily_log.csv"
    df_daily.to_csv(daily_path, index=False)
    print(f"    Daily log exported: {daily_path}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="CEI Signal Backtester (Long Only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/backtest_cei.py RELIANCE --start 2024-01-01
  python scripts/backtest_cei.py --watchlist "NIFTY 50" --start 2024-01-01
  python scripts/backtest_cei.py INFY --start 2024-01-01 --capital 200000 --export
        """,
    )
    parser.add_argument("symbol", nargs="?", help="NSE symbol (e.g. RELIANCE)")
    parser.add_argument("--watchlist", help="Process all symbols in a watchlist")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=100_000,
                        help="Starting capital (default: 100000)")

    # Stop-loss
    parser.add_argument("--stop-pct", type=float, default=-2.0,
                        help="Trailing stop: %% relative to CWVAP (default: -2.0)")
    parser.add_argument("--stop-entry-pct", type=float, default=-3.0,
                        help="Entry floor: min %% from entry price (default: -3.0)")

    # Execution
    parser.add_argument("--chase", action="store_true",
                        help="Fill at open if limit order misses")
    parser.add_argument("--exclude-va", action="store_true",
                        help="Skip entries if signal close is within VA")
    parser.add_argument("--agg", choices=["daily", "weekly", "monthly"], default="daily",
                        help="Aggregation mode (default: daily)")

    # Output
    parser.add_argument("--export", action="store_true",
                        help="Export trades and equity curve to CSV")

    args = parser.parse_args()

    if not args.symbol and not args.watchlist:
        parser.error("At least one of 'symbol' or '--watchlist' must be provided.")

    symbols = []
    if args.symbol:
        symbols.append(args.symbol.upper())
    
    if args.watchlist:
        wl_symbols = get_watchlist_symbols(args.watchlist)
        if not wl_symbols:
            print(f"  ERROR: Watchlist '{args.watchlist}' not found or empty.")
            sys.exit(1)
        for s in wl_symbols:
            if s not in symbols:
                symbols.append(s)

    export_dir = "data" if args.export else "."
    
    print(f"\n🚀 Starting batch backtest for {len(symbols)} symbols ({args.agg})...")
    
    total_net_pnl = 0.0
    
    for symbol in symbols:
        config = BacktestConfig(
            symbol=symbol,
            start_date=args.start,
            end_date=args.end,
            capital=args.capital,
            stop_cwvap_pct=args.stop_pct,
            stop_entry_pct=args.stop_entry_pct,
            chase_if_limit_misses=args.chase,
            exclude_va=args.exclude_va,
            agg_mode=args.agg,
        )

        bt = Backtester(config)
        try:
            summary = bt.run()
            total_net_pnl += summary.get("net_pnl", 0.0)
            
            print_report(bt, compact=bool(args.watchlist))

            if args.export:
                export_trades_csv(bt, symbol, output_dir=export_dir)
        except Exception as e:
            print(f"    ERROR processing {symbol}: {e}")
            import traceback
            traceback.print_exc()
            continue

    if args.watchlist:
        sign = "+" if total_net_pnl >= 0 else ""
        print(f"\n{'=' * 40}")
        print(f"  AGGREGATE PERFORMANCE")
        print(f"{'=' * 40}")
        print(f"  Total Net P&L:  {sign}{total_net_pnl:,.2f}")
        print(f"{'=' * 40}")

    print(f"\n✅ All backtests completed.")


if __name__ == "__main__":
    main()
