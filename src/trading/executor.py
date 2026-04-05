"""
Order Executor — resolves prices and places orders for approved signals.

Separated from the scanner to allow independent scheduling:
- Scanner runs post-market: detects signals, marks exits.
- Executor runs next day (market hours or via cron): places BUY/SELL orders.

Usage:
    venv/bin/python3 -m src.trading.executor              # execute all pending
    venv/bin/python3 -m src.trading.executor --dry-run     # resolve prices, log only
    venv/bin/python3 -m src.trading.executor --entries-only # BUY orders only
    venv/bin/python3 -m src.trading.executor --exits-only  # SELL orders only
    venv/bin/python3 -m src.trading.executor --status      # print pending orders
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.broker import PaperBroker
from src.trading.charges import get_charges_calculator
from src.trading.price_resolver import PriceContext, get_price_resolver
from src.trading.repository import TradingRepository
from src.trading.sizing import SizingContext, get_sizing_strategy
from src.trading.signals import SignalFactory

logger = logging.getLogger(__name__)


def run_engine(symbol: str):
    """Run DivergenceEngine for a symbol, return (result, records) or (None, None) on failure."""
    try:
        engine = DivergenceEngine(symbol)
        result = engine.run()
        records = result.ledger.to_dict("records")
        return result, records
    except Exception as e:
        logger.warning("Engine failed for %s: %s", symbol, e)
        return None, None


class OrderExecutor:
    """Resolves prices and places orders for pending entries and exits.

    Reads config from trading_config table for pluggable components:
    - price_resolver: determines LIMIT price (historical / live)
    - sizing_strategy: determines position size
    - brokerage_model: computes charges
    - execution_mode: paper / live
    """

    def __init__(self, dry_run: bool = False):
        self.repo = TradingRepository()
        self.dry_run = dry_run

        cfg = self.repo.get_config()
        self.capital = float(cfg.get("capital", "1000000"))
        self.max_positions = int(cfg.get("max_concurrent_positions", "8"))
        self.execution_mode = cfg.get("execution_mode", "paper")

        # Pluggable components
        resolver_name = cfg.get("price_resolver", "historical")
        self.price_resolver = get_price_resolver(resolver_name)
        self.broker = PaperBroker()

        sizing_name = cfg.get("sizing_strategy", "equal_weight")
        self.sizing = get_sizing_strategy(
            sizing_name,
            kelly_fraction=float(cfg.get("kelly_fraction", "0.25")),
        )

        brokerage_model = cfg.get("brokerage_model", "zerodha")
        self.charges_calc = get_charges_calculator(brokerage_model)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """Execute all pending orders. Returns summary dict."""
        today = datetime.now().strftime("%Y-%m-%d")
        mode_label = "DRY-RUN" if self.dry_run else self.execution_mode
        print(f"=== Executor run: {today} (mode={mode_label}) ===")

        entry_results = self.execute_entries()
        exit_results = self.execute_exits()

        print(f"=== Executor complete: {len(entry_results)} entries, {len(exit_results)} exits ===")
        return {"entries": entry_results, "exits": exit_results}

    # ------------------------------------------------------------------
    # Entry execution
    # ------------------------------------------------------------------

    def execute_entries(self) -> list[dict]:
        """Process all pending_entry positions: resolve price → size → place BUY."""
        pending = self.repo.get_pending_entries()
        if not pending:
            print("  Entries: nothing pending.")
            return []

        print(f"  Entries: processing {len(pending)} pending...")

        results = []
        trade_history = self.repo.get_closed_trades(limit=100)
        open_count = len(self.repo.get_open_positions())

        for pos in pending:
            symbol = pos["symbol"]
            result, records = run_engine(symbol)
            if not records:
                results.append({"symbol": symbol, "status": "skipped", "reason": "engine_failed"})
                print(f"    {symbol}: engine failed, skipping")
                continue

            last = records[-1]
            close = last.get("close", 0)
            open_price = last.get("open", 0)
            atr = last.get("atr_20", 0)

            if close <= 0 or atr <= 0 or np.isnan(close) or np.isnan(atr):
                results.append({"symbol": symbol, "status": "skipped", "reason": "invalid_data"})
                print(f"    {symbol}: invalid price/ATR, skipping")
                continue

            # Resolve price
            price_ctx = PriceContext(
                side="BUY",
                symbol=symbol,
                entry_tag=pos.get("entry_tag", ""),
                close=close,
                open=open_price,
                high=last.get("high", 0),
                low=last.get("low", 0),
                cwvap=last.get("cwvap", 0),
                atr=atr,
            )
            order_price = self.price_resolver.resolve(price_ctx)

            # Size the position
            funds = self.repo.get_funds()
            ctx = SizingContext(
                equity=funds["net_worth"],
                cash_available=funds["available_capital"],
                max_positions=self.max_positions,
                open_position_count=open_count,
                entry_price=order_price.limit_price,
                atr=atr,
                trade_history=trade_history,
            )
            qty = self.sizing.calculate(ctx)
            if qty <= 0:
                results.append({"symbol": symbol, "status": "skipped", "reason": "zero_quantity"})
                print(f"    {symbol}: quantity=0, skipping")
                continue

            charges = self.charges_calc.compute("BUY", qty, order_price.limit_price)
            capital_deployed = qty * order_price.limit_price

            if not self.dry_run:
                order_result = self.broker.place_order(
                    symbol, qty, "BUY", order_price.limit_price,
                )

                self.repo.create_order(
                    position_id=pos["id"],
                    symbol=symbol,
                    side="BUY",
                    order_type="LIMIT",
                    quantity=qty,
                    price=order_price.limit_price,
                    turnover=round(capital_deployed, 2),
                    brokerage=charges.brokerage,
                    stt=charges.stt,
                    exchange_txn=charges.exchange_txn,
                    gst=charges.gst,
                    sebi_fee=charges.sebi_fee,
                    stamp_duty=charges.stamp_duty,
                    total_charges=charges.total,
                    net_amount=round(capital_deployed + charges.total, 2),
                    broker_order_id=order_result["order_id"],
                    mode=self.execution_mode,
                    price_rationale=order_price.rationale,
                )

                entry_date = str(last.get("date", ""))[:10]
                psz = last.get("price_slope_z", 0)
                sizing_info = self.sizing.get_info()

                self.repo.update_position(
                    pos["id"],
                    status="open",
                    entry_date=entry_date,
                    entry_price=order_price.limit_price,
                    atr_at_entry=atr,
                    quantity=qty,
                    capital_deployed=round(capital_deployed, 2),
                    peak_close=close,
                    psz_at_entry=psz if not np.isnan(psz) else 0,
                    psz_peak=psz if not np.isnan(psz) else 0,
                    broker_order_id=order_result["order_id"],
                    sizing_method=sizing_info["sizing_method"],
                    kelly_f=sizing_info.get("kelly_f"),
                    entry_charges=charges.total,
                )
                open_count += 1

            print(f"    {symbol}: BUY {qty} @ ₹{order_price.limit_price:.2f} "
                  f"({order_price.rationale}), charges=₹{charges.total:.2f}")

            results.append({
                "symbol": symbol,
                "status": "executed" if not self.dry_run else "dry_run",
                "price": order_price.limit_price,
                "rationale": order_price.rationale,
                "quantity": qty,
                "charges": charges.total,
            })

        return results

    # ------------------------------------------------------------------
    # Exit execution
    # ------------------------------------------------------------------

    def execute_exits(self) -> list[dict]:
        """Process all pending_exit positions: resolve price → place SELL → close."""
        pending_exits = self.repo.get_pending_exits()
        if not pending_exits:
            print("  Exits: nothing pending.")
            return []

        print(f"  Exits: processing {len(pending_exits)} pending...")

        results = []
        for pos in pending_exits:
            symbol = pos["symbol"]
            result, records = run_engine(symbol)
            if not records:
                results.append({"symbol": symbol, "status": "skipped", "reason": "engine_failed"})
                print(f"    {symbol}: engine failed, skipping")
                continue

            last = records[-1]
            close = last.get("close", 0)
            open_price = last.get("open", 0)

            if close <= 0 or np.isnan(close):
                results.append({"symbol": symbol, "status": "skipped", "reason": "invalid_data"})
                print(f"    {symbol}: invalid price, skipping")
                continue

            # Resolve exit price
            price_ctx = PriceContext(
                side="SELL",
                symbol=symbol,
                exit_reason=pos.get("exit_reason", ""),
                close=close,
                open=open_price,
                high=last.get("high", 0),
                low=last.get("low", 0),
                cwvap=last.get("cwvap", 0),
                atr=last.get("atr_20", 0) or 0,
                entry_price=pos["entry_price"],
                capital_deployed=pos.get("capital_deployed", 0) or 0,
            )
            order_price = self.price_resolver.resolve(price_ctx)

            qty = pos["quantity"]
            entry_price = pos["entry_price"]
            exit_charges = self.charges_calc.compute("SELL", qty, order_price.limit_price)

            # P&L calculations
            entry_charges_val = pos.get("entry_charges", 0) or 0
            total_charges = entry_charges_val + exit_charges.total
            capital_deployed = pos.get("capital_deployed", 0) or (entry_price * qty)
            gross_pnl_abs = (order_price.limit_price - entry_price) * qty
            net_pnl_abs = gross_pnl_abs - total_charges
            pnl_pct = round((order_price.limit_price / entry_price - 1) * 100, 2)
            net_pnl_pct = round((net_pnl_abs / capital_deployed) * 100, 2) if capital_deployed > 0 else 0

            if not self.dry_run:
                order_result = self.broker.place_order(
                    symbol, qty, "SELL", order_price.limit_price,
                )

                turnover = qty * order_price.limit_price
                self.repo.create_order(
                    position_id=pos["id"],
                    symbol=symbol,
                    side="SELL",
                    order_type="LIMIT",
                    quantity=qty,
                    price=order_price.limit_price,
                    turnover=round(turnover, 2),
                    brokerage=exit_charges.brokerage,
                    stt=exit_charges.stt,
                    exchange_txn=exit_charges.exchange_txn,
                    gst=exit_charges.gst,
                    sebi_fee=exit_charges.sebi_fee,
                    stamp_duty=exit_charges.stamp_duty,
                    total_charges=exit_charges.total,
                    net_amount=round(turnover - exit_charges.total, 2),
                    broker_order_id=order_result["order_id"],
                    mode=self.execution_mode,
                    price_rationale=order_price.rationale,
                )

                exit_date = str(last.get("date", ""))[:10]
                self.repo.close_position(
                    pos["id"],
                    exit_date=exit_date,
                    exit_price=order_price.limit_price,
                    exit_reason=pos.get("exit_reason", "unknown"),
                    final_pnl_pct=pnl_pct,
                    exit_charges=exit_charges.total,
                    total_charges=round(total_charges, 2),
                    net_pnl_pct=net_pnl_pct,
                    net_pnl_abs=round(net_pnl_abs, 2),
                )

            print(f"    {symbol}: SELL {qty} @ ₹{order_price.limit_price:.2f} "
                  f"({order_price.rationale}), P&L={pnl_pct:+.2f}%")

            results.append({
                "symbol": symbol,
                "status": "executed" if not self.dry_run else "dry_run",
                "price": order_price.limit_price,
                "rationale": order_price.rationale,
                "pnl_pct": pnl_pct,
                "net_pnl_pct": net_pnl_pct,
            })

        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_status():
    """Print pending entries and exits."""
    repo = TradingRepository()

    pending_entries = repo.get_pending_entries()
    pending_exits = repo.get_pending_exits()

    print("=== Executor Status ===")
    print(f"Pending entries: {len(pending_entries)}")
    for p in pending_entries:
        print(f"  {p['symbol']:12s}  signal_date={p.get('signal_date', 'N/A')}  "
              f"tag={p.get('entry_tag', '')}")

    print(f"Pending exits:  {len(pending_exits)}")
    for p in pending_exits:
        print(f"  {p['symbol']:12s}  entry=₹{p.get('entry_price', 0):.2f}  "
              f"reason={p.get('exit_reason', '')}")


def main():
    parser = argparse.ArgumentParser(description="Order executor — resolve prices and place orders")
    parser.add_argument("--dry-run", action="store_true", help="Resolve prices and log without placing orders")
    parser.add_argument("--entries-only", action="store_true", help="Execute BUY orders only")
    parser.add_argument("--exits-only", action="store_true", help="Execute SELL orders only")
    parser.add_argument("--status", action="store_true", help="Print pending orders and exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if args.status:
        print_status()
        return

    executor = OrderExecutor(dry_run=args.dry_run)

    if args.entries_only:
        executor.execute_entries()
    elif args.exits_only:
        executor.execute_exits()
    else:
        executor.run()


if __name__ == "__main__":
    main()
