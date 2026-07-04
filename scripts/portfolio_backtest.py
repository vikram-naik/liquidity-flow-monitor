"""
Portfolio-level backtest with Kelly criterion position sizing.

Runs the signal system across a watchlist, then replays all trades
chronologically through a portfolio simulator that sizes positions
using the Kelly criterion.  Produces both Kelly-sized and equal-weight
results for comparison.

Usage:
    venv/bin/python3 scripts/portfolio_backtest.py
    venv/bin/python3 scripts/portfolio_backtest.py --watchlist "NIFTY 50" --capital 1000000
    venv/bin/python3 scripts/portfolio_backtest.py --start-date 2024-01-01 --kelly-fraction 0.25
"""

from __future__ import annotations

import argparse
import io
import math
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade, SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.signals.base import BaseEntryConfig, BaseExitConfig
from src.trading.signals.enums import ExitReason
from src.trading.charges import ZerodhaDeliveryCharges
from src.database import DB_PATH

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

SEP = "=" * 72
THIN_SEP = "-" * 72
INR = lambda v: "₹" + f"{v:,.0f}"


# ── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class PortfolioConfig:
    starting_capital: float = 1_000_000.0
    start_date: str = "2024-01-01"
    kelly_fraction: float = 0.25        # fractional Kelly multiplier
    max_positions: int = 8
    max_allocation: float = 0.30        # max 30% of equity in one position
    kelly_window: int = 50              # rolling window for Kelly stats
    min_trades_for_kelly: int = 20      # equal-weight until this many trades


@dataclass
class Position:
    symbol: str
    entry_date: str
    entry_price: float
    shares: int
    capital_deployed: float             # shares * entry_price
    entry_charges: float                # ₹ charges on buy leg
    trade: Trade                        # reference to original Trade


@dataclass
class PortfolioTradeRecord:
    trade: Trade
    shares: int
    capital_deployed: float
    gross_pnl_abs: float                # absolute ₹ P&L before charges
    total_charges: float                # entry + exit charges
    net_pnl_abs: float                  # gross_pnl_abs - total_charges
    kelly_f: float                      # Kelly fraction used at entry
    equity_at_entry: float
    sizing_method: str                  # "kelly" or "equal_weight"

    @property
    def pnl_abs(self) -> float:
        """Net P&L for backward compat with reporting code."""
        return self.net_pnl_abs


@dataclass
class EquityCurvePoint:
    date: str
    equity: float
    cash: float
    positions_open: int
    drawdown_pct: float
    kelly_f: float


# ── Reused from walk_forward.py (script, not importable) ───────────────────

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


def simulate_trades(
    ticker: str, df: pd.DataFrame,
    entry_cfg: BaseEntryConfig, exit_cfg: BaseExitConfig, signal,
) -> list[Trade]:
    """Walk through ledger bar-by-bar, enter and exit trades with EOD-lag."""
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

            reason, delivery_bad_count = signal.check_exit(
                row, prev, trade, peak_close, bars_held,
                delivery_bad_count, cwvap_values, exit_cfg,
                records, i,
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
                conviction_score=sig.get("details", {}).get("conv_score", 0),
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
                pending_signal = {"soft_count": soft_count, "details": fdetails}

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


# ── Phase A: Collect all trades ────────────────────────────────────────────

def collect_all_trades(
    symbols: list[str],
    start_date: str,
    entry_cfg: BaseEntryConfig,
    exit_cfg: BaseExitConfig,
    signal,
) -> list[Trade]:
    """Run DivergenceEngine + simulate_trades for every symbol, filter by start_date."""
    all_trades: list[Trade] = []
    failed = []
    total = len(symbols)

    for idx, sym in enumerate(symbols, 1):
        print(f"  [{idx}/{total}] {sym}...", end=" ", flush=True)
        try:
            engine = DivergenceEngine(sym, start_date=None, end_date=None)
            result = engine.run()
            from src.trading.signals.savgol_cts import get_symbol_entry_config, get_symbol_exit_config
            sym_entry_cfg = get_symbol_entry_config(sym, entry_cfg)
            sym_exit_cfg = get_symbol_exit_config(sym, exit_cfg)
            trades = simulate_trades(sym, result.ledger, sym_entry_cfg, sym_exit_cfg, signal)
            period_trades = [t for t in trades if str(t.entry_date) >= start_date]
            all_trades.extend(period_trades)
            print(f"{len(period_trades)} trades")
        except Exception as e:
            failed.append((sym, str(e)))
            print(f"FAILED ({e})")

    traded = len(set(t.symbol for t in all_trades))
    print(f"\n  Total: {len(all_trades)} trades across {traded}/{total} symbols "
          f"| {len(failed)} failed\n")
    return all_trades


# ── Kelly Criterion ────────────────────────────────────────────────────────

def compute_kelly(
    completed_trades: list[PortfolioTradeRecord],
    fraction: float = 0.25,
    max_alloc: float = 0.30,
    min_trades: int = 20,
) -> float:
    """Compute fractional Kelly from recent completed trade records.

    f* = (b*p - q) / b
    where b = avg_win/avg_loss, p = win_rate, q = 1-p.

    Returns fraction * f*, clamped to [0, max_alloc].
    Falls back to 0 if insufficient data.
    """
    if len(completed_trades) < min_trades:
        return 0.0  # caller should use equal-weight fallback

    pnls = [r.trade.pnl_pct for r in completed_trades]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]

    if not winners or not losers:
        return 0.0  # can't compute b without both sides

    p = len(winners) / len(pnls)
    q = 1.0 - p
    avg_win = np.mean(winners)
    avg_loss = abs(np.mean(losers))

    if avg_loss == 0:
        return 0.0

    b = avg_win / avg_loss
    f_star = (b * p - q) / b

    if f_star <= 0:
        return 0.0  # no edge

    actual_f = fraction * f_star
    return min(actual_f, max_alloc)


# ── Phase B: Portfolio Simulation ──────────────────────────────────────────

def run_portfolio_simulation(
    all_trades: list[Trade],
    config: PortfolioConfig,
    sizing_method: str = "kelly",
) -> tuple[list[PortfolioTradeRecord], list[EquityCurvePoint], dict]:
    """Walk through all trades chronologically, apply sizing, track equity.

    Returns:
        (trade_records, equity_curve, skip_stats)
    """
    charges_calc = ZerodhaDeliveryCharges()

    # Build event list: exits before entries on the same date
    events = []
    for t in all_trades:
        events.append((t.entry_date, 1, "entry", t))
        events.append((t.exit_date, 0, "exit", t))
    events.sort(key=lambda e: (e[0], e[1]))  # date asc, exits (0) before entries (1)

    cash = config.starting_capital
    open_positions: dict[str, Position] = {}
    completed: list[PortfolioTradeRecord] = []
    equity_curve: list[EquityCurvePoint] = []
    peak_equity = config.starting_capital
    current_kelly_f = 1.0 / config.max_positions  # start with equal-weight
    total_charges_paid = 0.0

    skipped_capital = 0
    skipped_kelly = 0
    skipped_capacity = 0

    for _, _, event_type, trade in events:
        if event_type == "exit":
            key = f"{trade.symbol}_{trade.entry_date}"
            if key not in open_positions:
                continue  # trade was skipped at entry
            pos = open_positions.pop(key)

            # Compute exit charges
            exit_charges = charges_calc.compute("SELL", pos.shares, trade.exit_price)
            total_trade_charges = pos.entry_charges + exit_charges.total

            proceeds = pos.shares * trade.exit_price - exit_charges.total
            gross_pnl = pos.shares * trade.exit_price - pos.capital_deployed
            net_pnl = gross_pnl - total_trade_charges
            cash += proceeds
            total_charges_paid += exit_charges.total

            completed.append(PortfolioTradeRecord(
                trade=trade,
                shares=pos.shares,
                capital_deployed=pos.capital_deployed,
                gross_pnl_abs=round(gross_pnl, 2),
                total_charges=round(total_trade_charges, 2),
                net_pnl_abs=round(net_pnl, 2),
                kelly_f=current_kelly_f,
                equity_at_entry=0.0,
                sizing_method=sizing_method,
            ))

        elif event_type == "entry":
            if len(open_positions) >= config.max_positions:
                skipped_capacity += 1
                continue

            # Current equity = cash + cost basis of open positions
            deployed = sum(p.capital_deployed for p in open_positions.values())
            equity = cash + deployed

            if sizing_method == "kelly":
                # Rolling Kelly from recent completed trades
                window = completed[-config.kelly_window:]
                kelly_f = compute_kelly(window, config.kelly_fraction,
                                        config.max_allocation, config.min_trades_for_kelly)
                if kelly_f <= 0:
                    # Fall back to equal-weight during warm-up
                    if len(completed) < config.min_trades_for_kelly:
                        kelly_f = 1.0 / config.max_positions
                    else:
                        skipped_kelly += 1
                        continue  # Kelly says no edge
                current_kelly_f = kelly_f
                allocation = equity * kelly_f
            else:
                kelly_f = 1.0 / config.max_positions
                current_kelly_f = kelly_f
                allocation = equity / config.max_positions

            allocation = min(allocation, cash)
            if allocation < trade.entry_price:
                skipped_capital += 1
                continue

            shares = int(allocation / trade.entry_price)
            if shares <= 0:
                skipped_capital += 1
                continue

            # Compute entry charges
            entry_charges = charges_calc.compute("BUY", shares, trade.entry_price)
            capital_deployed = shares * trade.entry_price
            cash -= (capital_deployed + entry_charges.total)
            total_charges_paid += entry_charges.total

            open_positions[f"{trade.symbol}_{trade.entry_date}"] = Position(
                symbol=trade.symbol,
                entry_date=trade.entry_date,
                entry_price=trade.entry_price,
                shares=shares,
                capital_deployed=capital_deployed,
                entry_charges=entry_charges.total,
                trade=trade,
            )

        # Record equity curve point
        deployed = sum(p.capital_deployed for p in open_positions.values())
        equity = cash + deployed
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
        equity_curve.append(EquityCurvePoint(
            date=trade.entry_date if event_type == "entry" else trade.exit_date,
            equity=round(equity, 2),
            cash=round(cash, 2),
            positions_open=len(open_positions),
            drawdown_pct=round(dd, 2),
            kelly_f=round(current_kelly_f, 4),
        ))

    # Close any remaining open positions at cost basis for final equity
    deployed = sum(p.capital_deployed for p in open_positions.values())
    final_equity = cash + deployed

    skip_stats = {
        "skipped_capital": skipped_capital,
        "skipped_kelly": skipped_kelly,
        "skipped_capacity": skipped_capacity,
        "total_available": len(all_trades),
        "total_taken": len(completed),
        "total_charges_paid": round(total_charges_paid, 2),
    }

    return completed, equity_curve, skip_stats


# ── Stats Computation ──────────────────────────────────────────────────────

def compute_portfolio_stats(
    records: list[PortfolioTradeRecord],
    equity_curve: list[EquityCurvePoint],
    config: PortfolioConfig,
) -> dict:
    if not records:
        return {}

    final_equity = equity_curve[-1].equity if equity_curve else config.starting_capital
    total_return = (final_equity / config.starting_capital - 1) * 100

    # CAGR
    first_date = datetime.strptime(records[0].trade.entry_date, "%Y-%m-%d")
    last_date = datetime.strptime(records[-1].trade.exit_date, "%Y-%m-%d")
    years = (last_date - first_date).days / 365.25
    if years > 0 and final_equity > 0:
        cagr = ((final_equity / config.starting_capital) ** (1 / years) - 1) * 100
    else:
        cagr = 0.0

    # Max drawdown
    max_dd = max((pt.drawdown_pct for pt in equity_curve), default=0.0)

    # Sharpe-like ratio (trade returns, annualised)
    trade_returns = [r.pnl_abs / r.capital_deployed for r in records if r.capital_deployed > 0]
    if trade_returns and len(trade_returns) > 1 and years > 0:
        avg_ret = np.mean(trade_returns)
        std_ret = np.std(trade_returns, ddof=1)
        trades_per_year = len(trade_returns) / years
        sharpe = (avg_ret / std_ret) * np.sqrt(trades_per_year) if std_ret > 0 else 0
    else:
        sharpe = 0.0

    # Profit factor (net ₹)
    net_profit = sum(r.net_pnl_abs for r in records if r.net_pnl_abs > 0)
    net_loss = abs(sum(r.net_pnl_abs for r in records if r.net_pnl_abs <= 0))
    profit_factor = net_profit / net_loss if net_loss > 0 else float("inf")

    # Win rate (net)
    winners = [r for r in records if r.net_pnl_abs > 0]
    win_rate = len(winners) / len(records) * 100
    losers = [r for r in records if r.net_pnl_abs <= 0]

    # P&L stats
    total_net_pnl = sum(r.net_pnl_abs for r in records)
    total_gross_pnl = sum(r.gross_pnl_abs for r in records)
    total_charges = sum(r.total_charges for r in records)
    avg_net_pnl = total_net_pnl / len(records)
    avg_pnl_pct = np.mean([r.trade.pnl_pct for r in records])
    avg_winner_abs = np.mean([r.net_pnl_abs for r in winners]) if winners else 0
    avg_loser_abs = np.mean([r.net_pnl_abs for r in losers]) if losers else 0

    return {
        "final_equity": round(final_equity, 2),
        "total_return": round(total_return, 2),
        "cagr": round(cagr, 2),
        "max_drawdown": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "profit_factor": round(profit_factor, 2),
        "win_rate": round(win_rate, 1),
        "total_trades": len(records),
        "winners": len(winners),
        "losers": len(losers),
        "total_gross_pnl": round(total_gross_pnl, 2),
        "total_charges": round(total_charges, 2),
        "total_pnl_abs": round(total_net_pnl, 2),
        "avg_pnl_abs": round(avg_net_pnl, 2),
        "avg_pnl_pct": round(avg_pnl_pct, 2),
        "avg_winner_abs": round(avg_winner_abs, 2),
        "avg_loser_abs": round(avg_loser_abs, 2),
    }


def get_index_symbol_for_watchlist(watchlist_name: str) -> str:
    wl_upper = watchlist_name.upper().strip()
    if "500" in wl_upper:
        return "NIFTY 500"
    elif "100" in wl_upper:
        return "NIFTY 100"
    elif "200" in wl_upper:
        return "NIFTY 200"
    elif "BANK" in wl_upper:
        return "NIFTY BANK"
    elif "IT" in wl_upper:
        return "NIFTY IT"
    return "NIFTY 50"


def compute_index_performance(index_symbol: str, start_date: str, end_date: str) -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    query = """
        SELECT record_date as date, price_close as close 
        FROM nse_delivery_log 
        WHERE symbol = ? AND record_date >= ? AND record_date <= ?
        ORDER BY record_date ASC
    """
    df = pd.read_sql_query(query, conn, params=[index_symbol, start_date, end_date])
    conn.close()
    
    if df.empty:
        return {}
        
    start_val = df.iloc[0]["close"]
    end_val = df.iloc[-1]["close"]
    total_return = (end_val / start_val - 1) * 100
    
    # CAGR
    first_dt = datetime.strptime(df.iloc[0]["date"], "%Y-%m-%d")
    last_dt = datetime.strptime(df.iloc[-1]["date"], "%Y-%m-%d")
    years = (last_dt - first_dt).days / 365.25
    cagr = ((end_val / start_val) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    
    # Max Drawdown
    df["peak"] = df["close"].cummax()
    df["drawdown"] = (df["peak"] - df["close"]) / df["peak"] * 100
    max_dd = df["drawdown"].max()
    
    return {
        "symbol": index_symbol,
        "total_return": round(total_return, 2),
        "cagr": round(cagr, 2),
        "max_drawdown": round(max_dd, 2),
    }


# ── Report Formatting ──────────────────────────────────────────────────────

def format_section_stats(
    label: str,
    records: list[PortfolioTradeRecord],
    stats: dict,
    skip_stats: dict,
    config: PortfolioConfig,
    out: io.StringIO,
):
    """Format one sizing method's results."""
    def w(line: str = ""):
        out.write(line + "\n")

    if not stats:
        w(f"\n{label}: No trades taken.")
        return

    w(f"\n{SEP}")
    w(f"  {label}")
    w(SEP)
    w()
    w(f"  {'Metric':<28} {'Value':>14}")
    w(f"  {THIN_SEP[:44]}")
    w(f"  {'Starting Capital':<28} {INR(config.starting_capital):>14}")
    w(f"  {'Final Equity':<28} {INR(stats['final_equity']):>14}")
    w(f"  {'Total Return':<28} {stats['total_return']:>+13.2f}%")
    w(f"  {'CAGR':<28} {stats['cagr']:>+13.2f}%")
    w(f"  {'Max Drawdown':<28} {stats['max_drawdown']:>13.2f}%")
    w(f"  {'Sharpe Ratio':<28} {stats['sharpe']:>14.2f}")
    w(f"  {'Profit Factor':<28} {stats['profit_factor']:>14.2f}")
    w()
    w(f"  {'Trades Taken':<28} {stats['total_trades']:>14} / {skip_stats['total_available']}")
    w(f"  {'Skipped (capacity)':<28} {skip_stats['skipped_capacity']:>14}")
    w(f"  {'Skipped (capital)':<28} {skip_stats['skipped_capital']:>14}")
    w(f"  {'Skipped (Kelly <= 0)':<28} {skip_stats['skipped_kelly']:>14}")
    w()
    wl_str = f"{stats['winners']} / {stats['losers']}"
    w(f"  {'Winners / Losers':<28} {wl_str:>14}")
    w(f"  {'Win Rate':<28} {stats['win_rate']:>13.1f}%")
    w(f"  {'Avg P&L %':<28} {stats['avg_pnl_pct']:>+13.2f}%")
    w(f"  {'Avg ₹ P&L / trade':<28} {INR(stats['avg_pnl_abs']):>14}")
    w(f"  {'Avg ₹ Winner':<28} {INR(stats['avg_winner_abs']):>14}")
    w(f"  {'Avg ₹ Loser':<28} {INR(stats['avg_loser_abs']):>14}")
    w(f"  {'Gross ₹ P&L':<28} {INR(stats['total_gross_pnl']):>14}")
    w(f"  {'Total Charges':<28} {INR(stats['total_charges']):>14}")
    w(f"  {'Net ₹ P&L':<28} {INR(stats['total_pnl_abs']):>14}")

    # Exit breakdown
    df = pd.DataFrame([{
        "reason": r.trade.exit_reason.value if hasattr(r.trade.exit_reason, "value") else str(r.trade.exit_reason),
        "pnl_pct": r.trade.pnl_pct,
        "pnl_abs": r.pnl_abs,
    } for r in records])

    if not df.empty:
        reason_agg = (
            df.groupby("reason")
            .agg(count=("pnl_pct", "size"),
                 avg_pnl=("pnl_pct", "mean"),
                 total_abs=("pnl_abs", "sum"),
                 win_rate=("pnl_pct", lambda x: round((x > 0).mean() * 100, 1)))
            .reset_index()
            .sort_values("count", ascending=False)
            .round(2)
        )
        w(f"\n  Exit Breakdown:")
        w(tabulate(reason_agg,
                   headers=["Exit Reason", "Count", "Avg P&L%", "Total ₹", "Win%"],
                   tablefmt="simple", floatfmt=".2f", showindex=False))

    # Entry tag breakdown
    df_entry = pd.DataFrame([{
        "entry_tag": r.trade.entry_tag.value if hasattr(r.trade.entry_tag, "value") else str(r.trade.entry_tag),
        "pnl_pct": r.trade.pnl_pct,
        "pnl_abs": r.pnl_abs,
        "mfe": r.trade.mfe_pct,
        "mae": r.trade.mae_pct,
    } for r in records])

    if not df_entry.empty:
        entry_agg = (
            df_entry.groupby("entry_tag")
            .agg(count=("pnl_pct", "size"),
                 avg_pnl=("pnl_pct", "mean"),
                 total_abs=("pnl_abs", "sum"),
                 win_rate=("pnl_pct", lambda x: round((x > 0).mean() * 100, 1)),
                 avg_mfe=("mfe", "mean"),
                 avg_mae=("mae", "mean"))
            .reset_index()
            .sort_values("count", ascending=False)
            .round(2)
        )
        w(f"\n  Entry Type Breakdown:")
        w(tabulate(entry_agg,
                   headers=["Entry Type", "Count", "Avg P&L%", "Total ₹", "Win%", "Avg MFE%", "Avg MAE%"],
                   tablefmt="simple", floatfmt=".2f", showindex=False))

    # Top / bottom symbols
    df_sym = pd.DataFrame([{
        "symbol": r.trade.symbol,
        "pnl_abs": r.pnl_abs,
        "pnl_pct": r.trade.pnl_pct,
    } for r in records])

    if not df_sym.empty:
        sym_agg = (
            df_sym.groupby("symbol")
            .agg(count=("pnl_pct", "size"),
                 total_abs=("pnl_abs", "sum"),
                 avg_pnl=("pnl_pct", "mean"),
                 win_rate=("pnl_pct", lambda x: round((x > 0).mean() * 100, 1)))
            .reset_index()
            .round(2)
        )
        top5 = sym_agg.nlargest(5, "total_abs")
        bot5 = sym_agg.nsmallest(5, "total_abs")
        w(f"\n  Top 5 Symbols (by total ₹ P&L):")
        w(tabulate(top5, headers=["Symbol", "Trades", "Total ₹", "Avg P&L%", "Win%"],
                   tablefmt="simple", floatfmt=".2f", showindex=False))
        w(f"\n  Bottom 5 Symbols (by total ₹ P&L):")
        w(tabulate(bot5, headers=["Symbol", "Trades", "Total ₹", "Avg P&L%", "Win%"],
                   tablefmt="simple", floatfmt=".2f", showindex=False))


def format_kelly_evolution(records: list[PortfolioTradeRecord], out: io.StringIO):
    """Show how Kelly fraction evolved over time."""
    def w(line: str = ""):
        out.write(line + "\n")

    if len(records) < 20:
        return

    w(f"\n  Kelly Fraction Evolution:")
    # Show in chunks of 25 trades
    chunk_size = max(len(records) // 8, 10)
    rows = []
    for start in range(0, len(records), chunk_size):
        chunk = records[start:start + chunk_size]
        dates = f"{chunk[0].trade.entry_date} — {chunk[-1].trade.exit_date}"
        avg_f = np.mean([r.kelly_f for r in chunk])
        pnls = [r.trade.pnl_pct for r in chunk]
        wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        rows.append([dates, f"{avg_f:.4f}", len(chunk), f"{wr:.1f}%"])

    w(tabulate(rows, headers=["Period", "Avg Kelly f", "Trades", "Win%"],
               tablefmt="simple"))


def format_comparison(kelly_stats: dict, ew_stats: dict, out: io.StringIO, index_stats: dict | None = None):
    """Side-by-side comparison of Kelly vs Equal-Weight (and optional Index benchmark)."""
    def w(line: str = ""):
        out.write(line + "\n")

    w(f"\n{SEP}")
    if index_stats:
        idx_sym = index_stats.get("symbol", "Index")
        w(f"  COMPARISON: PORTFOLIO vs BENCHMARK ({idx_sym})")
    else:
        w("  COMPARISON: KELLY vs EQUAL-WEIGHT")
    w(SEP)
    w()
    
    if index_stats:
        idx_label = f"{index_stats.get('symbol', 'Index')} (B&H)"
        w(f"  {'Metric':<22} {'Kelly':>14} {'Equal-Wt':>14} {idx_label:>22}")
        w(f"  {THIN_SEP[:76]}")
    else:
        w(f"  {'Metric':<22} {'Kelly':>14} {'Equal-Wt':>14} {'Delta':>14}")
        w(f"  {THIN_SEP[:66]}")

    metrics = [
        ("Final Equity", "final_equity", INR, ""),
        ("Total Return", "total_return", lambda v: f"{v:+.2f}", "%"),
        ("CAGR", "cagr", lambda v: f"{v:+.2f}", "%"),
        ("Max Drawdown", "max_drawdown", lambda v: f"{v:.2f}", "%"),
        ("Sharpe Ratio", "sharpe", lambda v: f"{v:.2f}", ""),
        ("Profit Factor", "profit_factor", lambda v: f"{v:.2f}", ""),
        ("Win Rate", "win_rate", lambda v: f"{v:.1f}", "%"),
        ("Trades Taken", "total_trades", lambda v: f"{v:.0f}", ""),
        ("Total ₹ P&L", "total_pnl_abs", INR, ""),
    ]

    for label, key, fmt, suffix in metrics:
        kv = kelly_stats.get(key, 0)
        ev = ew_stats.get(key, 0)
        
        if index_stats:
            if key in ["total_return", "cagr", "max_drawdown"]:
                idx_v = index_stats.get(key, 0)
                idx_str = f"{idx_v:+.2f}%" if key in ["total_return", "cagr"] else f"{idx_v:.2f}%"
            else:
                idx_str = "—"
            
            if fmt == INR:
                w(f"  {label:<22} {fmt(kv):>14} {fmt(ev):>14} {idx_str:>22}")
            else:
                w(f"  {label:<22} {fmt(kv) + suffix:>14} {fmt(ev) + suffix:>14} {idx_str:>22}")
        else:
            delta = kv - ev
            if fmt == INR:
                w(f"  {label:<22} {fmt(kv):>14} {fmt(ev):>14} {fmt(delta):>14}")
            else:
                w(f"  {label:<22} {fmt(kv) + suffix:>14} {fmt(ev) + suffix:>14} {fmt(delta) + suffix:>14}")


def format_trade_log(records: list[PortfolioTradeRecord], out: io.StringIO, limit: int = 50):
    """Print first N trades with sizing details."""
    def w(line: str = ""):
        out.write(line + "\n")

    w(f"\n{SEP}")
    w(f"  TRADE LOG (first {min(limit, len(records))} of {len(records)} trades)")
    w(SEP)

    rows = []
    for r in records[:limit]:
        tag = r.trade.entry_tag.value if hasattr(r.trade.entry_tag, "value") else str(r.trade.entry_tag)
        # Shorten tag for display
        tag_short = tag.replace("SavgolCTS ", "")
        rows.append([
            r.trade.symbol,
            r.trade.entry_date,
            r.trade.exit_date,
            r.shares,
            INR(r.capital_deployed),
            f"{r.trade.pnl_pct:+.2f}%",
            INR(r.total_charges),
            INR(r.net_pnl_abs),
            f"{r.kelly_f:.4f}",
            tag_short,
        ])

    w(tabulate(rows,
               headers=["Symbol", "Entry", "Exit", "Shares", "Deployed",
                         "P&L%", "Charges", "Net ₹", "Kelly f", "Entry Type"],
               tablefmt="simple"))


def format_config_snapshot(config: PortfolioConfig, entry_cfg, exit_cfg, out: io.StringIO):
    """Dump config for reproducibility."""
    def w(line: str = ""):
        out.write(line + "\n")

    w(f"\n{SEP}")
    w("  CONFIG SNAPSHOT")
    w(SEP)
    w()
    w(f"  Portfolio:")
    for field_name in sorted(vars(config)):
        w(f"    {field_name}: {getattr(config, field_name)}")
    w(f"\n  Entry: {entry_cfg.__class__.__name__}")
    for field_name in sorted(vars(entry_cfg)):
        val = getattr(entry_cfg, field_name)
        if hasattr(val, "__dataclass_fields__"):
            w(f"    {field_name}:")
            for sub in sorted(vars(val)):
                w(f"      {sub}: {getattr(val, sub)}")
        else:
            w(f"    {field_name}: {val}")
    w(f"  Exit: {exit_cfg.__class__.__name__}")
    for field_name in sorted(vars(exit_cfg)):
        val = getattr(exit_cfg, field_name)
        if hasattr(val, "__dataclass_fields__"):
            w(f"    {field_name}:")
            for sub in sorted(vars(val)):
                w(f"      {sub}: {getattr(val, sub)}")
        else:
            w(f"    {field_name}: {val}")

    w(f"\n{SEP}")


def format_report(
    kelly_records: list[PortfolioTradeRecord],
    kelly_curve: list[EquityCurvePoint],
    kelly_stats: dict,
    kelly_skip: dict,
    ew_records: list[PortfolioTradeRecord],
    ew_curve: list[EquityCurvePoint],
    ew_stats: dict,
    ew_skip: dict,
    config: PortfolioConfig,
    entry_cfg: BaseEntryConfig,
    exit_cfg: BaseExitConfig,
    watchlist_name: str,
    signal_name: str,
    num_symbols: int,
    index_stats: dict | None = None,
) -> str:
    out = io.StringIO()

    def w(line: str = ""):
        out.write(line + "\n")

    end_date = max(
        (r.trade.exit_date for r in kelly_records),
        default=config.start_date,
    )

    w(SEP)
    w(f"  PORTFOLIO BACKTEST REPORT")
    w(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w(SEP)
    w()
    w(f"  Watchlist:      {watchlist_name} ({num_symbols} symbols)")
    w(f"  Signal:         {signal_name}")
    w(f"  Period:         {config.start_date} to {end_date}")
    w(f"  Capital:        {INR(config.starting_capital)}")
    w(f"  Kelly fraction: {config.kelly_fraction} (quarter Kelly)")
    w(f"  Max positions:  {config.max_positions}")
    if index_stats:
        idx_sym = index_stats.get("symbol", "Index")
        w(f"  Benchmark:      {idx_sym} (+{index_stats.get('total_return', 0):+.2f}% Return, {index_stats.get('cagr', 0):+.2f}% CAGR)")
    w()
    w(f"  Note: Open positions valued at cost basis (no intra-trade mark-to-market)")

    # Kelly results
    format_section_stats("KELLY-SIZED PORTFOLIO RESULTS", kelly_records,
                         kelly_stats, kelly_skip, config, out)
    format_kelly_evolution(kelly_records, out)

    # Equal-weight results
    format_section_stats("EQUAL-WEIGHT PORTFOLIO RESULTS", ew_records,
                         ew_stats, ew_skip, config, out)

    # Comparison
    if kelly_stats and ew_stats:
        format_comparison(kelly_stats, ew_stats, out, index_stats=index_stats)

    # Trade log (Kelly)
    if kelly_records:
        format_trade_log(kelly_records, out)

    # Config
    # format_config_snapshot(config, entry_cfg, exit_cfg, out)

    return out.getvalue()


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Portfolio backtest with Kelly criterion sizing")
    parser.add_argument("--watchlist", default="NIFTY 50")
    parser.add_argument("--capital", type=float, default=1_000_000.0, help="Starting capital in ₹")
    parser.add_argument("--start-date", default="2024-01-01", help="Start trading from this date")
    parser.add_argument("--signal", default="savgol_cts",
                        choices=["price_divergence", "nextgen", "savgol_cts"],
                        help="Signal strategy (default: savgol_cts)")
    parser.add_argument("--kelly-fraction", type=float, default=0.25,
                        help="Fractional Kelly multiplier (default: 0.25 = quarter Kelly)")
    parser.add_argument("--max-positions", type=int, default=8,
                        help="Max concurrent positions (default: 8)")
    args = parser.parse_args()

    config = PortfolioConfig(
        starting_capital=args.capital,
        start_date=args.start_date,
        kelly_fraction=args.kelly_fraction,
        max_positions=args.max_positions,
    )

    # Signal setup
    if args.signal == "savgol_cts":
        entry_cfg = SavgolCTSEntryConfig()
        exit_cfg = SavgolCTSExitConfig()
    else:
        # Standard fallback if needed
        entry_cfg = SavgolCTSEntryConfig()
        exit_cfg = SavgolCTSExitConfig()

    signal = SignalFactory.get_signal(args.signal)

    print(SEP)
    print(f"  PORTFOLIO BACKTEST")
    print(f"  Watchlist: {args.watchlist} | Signal: {args.signal}")
    print(f"  Capital: {INR(config.starting_capital)} | Kelly: {config.kelly_fraction}")
    print(f"  Period: {config.start_date} onwards | Max positions: {config.max_positions}")
    print(SEP)

    # Phase A: collect all trades
    symbols = get_watchlist_symbols(args.watchlist)
    print(f"\nPhase A: Generating trades for {len(symbols)} symbols...")
    all_trades = collect_all_trades(symbols, config.start_date, entry_cfg, exit_cfg, signal)

    if not all_trades:
        print("No trades found in the specified period. Exiting.")
        return

    # Phase B: portfolio simulation — Kelly
    print("Phase B: Running Kelly-sized portfolio simulation...")
    kelly_records, kelly_curve, kelly_skip = run_portfolio_simulation(
        all_trades, config, sizing_method="kelly",
    )
    kelly_stats = compute_portfolio_stats(kelly_records, kelly_curve, config)
    print(f"  Kelly: {kelly_stats.get('total_trades', 0)} trades taken, "
          f"final equity {INR(kelly_stats.get('final_equity', 0))}")

    # Phase B': portfolio simulation — Equal-weight
    print("Phase B': Running equal-weight portfolio simulation...")
    ew_records, ew_curve, ew_skip = run_portfolio_simulation(
        all_trades, config, sizing_method="equal_weight",
    )
    ew_stats = compute_portfolio_stats(ew_records, ew_curve, config)
    print(f"  Equal-weight: {ew_stats.get('total_trades', 0)} trades taken, "
          f"final equity {INR(ew_stats.get('final_equity', 0))}")

    # Phase C: format report
    print("\nPhase C: Generating report...")
    
    # Calculate index performance benchmark
    index_stats = None
    if kelly_records:
        first_date = kelly_records[0].trade.entry_date
        last_date = kelly_records[-1].trade.exit_date
        index_symbol = get_index_symbol_for_watchlist(args.watchlist)
        print(f"  Computing benchmark index ({index_symbol}) performance...")
        try:
            index_stats = compute_index_performance(index_symbol, first_date, last_date)
        except Exception as e:
            print(f"  Warning: Could not compute index performance benchmark: {e}")

    report = format_report(
        kelly_records, kelly_curve, kelly_stats, kelly_skip,
        ew_records, ew_curve, ew_stats, ew_skip,
        config, entry_cfg, exit_cfg,
        args.watchlist, args.signal, len(symbols),
        index_stats=index_stats,
    )

    print(report)

    # Write to file
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%d-%b-%Y_%H:%M")
    sanitized_wl = args.watchlist.replace(" ", "_")
    filename = f"{sanitized_wl}_pbt_{ts}.txt"
    outpath = OUTPUT_DIR / filename
    outpath.write_text(report)
    

if __name__ == "__main__":
    main()

