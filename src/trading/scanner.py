"""
Daily trading scanner — detects entry signals and exit conditions.

The scanner is a *signal detector* only. It does NOT place orders.
Order execution is handled by the OrderExecutor (src/trading/executor.py).

Flow:
    1. Check exits on open positions → mark as pending_exit.
    2. Scan watchlist for new entry signals → create proposed positions.
    3. Daily P&L snapshot.

Usage:
    venv/bin/python3 -m src.trading.scanner              # normal daily run
    venv/bin/python3 -m src.trading.scanner --dry-run     # scan + log signals only
    venv/bin/python3 -m src.trading.scanner --status       # print current state
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals import Trade, SignalFactory
from src.trading.signals.savgol_cts import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.trading.repository import TradingRepository

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "liquidity_monitor.db"


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


def _resolve_signal_configs(signal_name: str):
    """Return (entry_cfg, exit_cfg) for the given signal strategy name."""
    if signal_name == "savgol_cts":
        return SavgolCTSEntryConfig(), SavgolCTSExitConfig()
    elif signal_name == "long_divergence":
        from src.trading.signals import LongDivergenceEntryConfig, LongDivergenceExitConfig
        return LongDivergenceEntryConfig(min_soft_filters=0), LongDivergenceExitConfig()
    elif signal_name == "nextgen":
        from src.trading.signals import NextGenEntryConfig, NextGenExitConfig
        return NextGenEntryConfig(), NextGenExitConfig()
    elif signal_name == "price_divergence":
        from src.trading.signals import PriceDivergenceEntryConfig, PriceDivergenceExitConfig
        return PriceDivergenceEntryConfig(), PriceDivergenceExitConfig()
    raise ValueError(f"Unknown signal strategy: {signal_name}")


class Scanner:
    def __init__(self, dry_run: bool = False):
        self.repo = TradingRepository()
        self.dry_run = dry_run

        # Load config
        cfg = self.repo.get_config()
        self.capital = float(cfg.get("capital", "1000000"))
        self.max_positions = int(cfg.get("max_concurrent_positions", "8"))
        self.watchlist_name = cfg.get("watchlist", "NIFTY 50")
        self.execution_mode = cfg.get("execution_mode", "paper")

        # Pluggable signal strategy from config
        self.signal_name = cfg.get("signal_strategy", "savgol_cts")
        self.entry_cfg, self.exit_cfg = _resolve_signal_configs(self.signal_name)
        self.signal = SignalFactory.get_signal(self.signal_name)

    def run(self):
        """Execute all phases of the daily scan."""
        today = datetime.now().strftime("%Y-%m-%d")
        print(f"=== Scanner run: {today} (mode={'DRY-RUN' if self.dry_run else self.execution_mode}) ===")
        print(f"    Signal: {self.signal_name}")

        self._phase1_check_exits()
        self._phase2_scan_new_signals(today)
        self._phase3_daily_pnl(today)

        if not self.dry_run:
            self.repo.sync_positions_watchlist()

        print("=== Scanner complete ===")

    def refresh_positions(self):
        """Quick refresh: update P&L for open positions and take a new snapshot.
        
        Skips scanning for new signals. Useful for real-time dashboard updates.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        print(f"=== Position Refresh: {today} ===")
        self._phase1_check_exits()
        self._phase3_daily_pnl(today)

        if getattr(self, 'dry_run', False) is False:
            self.repo.sync_positions_watchlist()

        print("=== Refresh complete ===")

    # ── Phase 1: Detect exit signals on open positions ──────────────────

    def _phase1_check_exits(self):
        """Check exit conditions on open positions and mark as pending_exit.

        Does NOT place sell orders — that is the executor's job.
        """
        open_positions = self.repo.get_open_positions()
        if not open_positions:
            print("Phase 1: No open positions.")
            return

        print(f"Phase 1: Checking exits on {len(open_positions)} open positions...")
        for pos in open_positions:
            symbol = pos["symbol"]
            result, records = run_engine(symbol)
            if not records or len(records) < 2:
                print(f"  {symbol}: engine failed, skipping exit check")
                continue

            last = records[-1]
            prev = records[-2]
            close = last.get("close", 0)
            if close <= 0 or np.isnan(close):
                continue

            entry_price = pos["entry_price"]
            peak_close = max(pos.get("peak_close", close) or close, close)
            bars_held = (pos.get("bars_held", 0) or 0) + 1
            delivery_bad_count = pos.get("delivery_bad_count", 0) or 0

            # Build Trade object from position state
            trade = Trade(
                symbol=symbol,
                entry_date=pos["entry_date"],
                entry_price=entry_price,
                entry_idx=0,
                atr_at_entry=pos["atr_at_entry"],
                soft_filters_passed=pos.get("soft_filters_passed", 0) or 0,
                psz_peak=pos.get("psz_peak", 0) or 0,
                entry_tag=pos.get("entry_tag", ""),
            )

            # Get CWVAP values from ledger tail
            cwvap_values = []
            tail_len = min(len(records), 15)
            for r in records[-tail_len:]:
                cwvap_values.append(r.get("cwvap", np.nan))

            reason, delivery_bad_count = self.signal.check_exit(
                last, prev, trade, peak_close, bars_held,
                delivery_bad_count, cwvap_values, self.exit_cfg,
                records, len(records) - 1,
            )

            # Calculate running stats
            pnl_pct = round((close / entry_price - 1) * 100, 2)
            mfe = max(pos.get("mfe_pct", 0) or 0, pnl_pct)
            mae_val = min(-(pos.get("mae_pct", 0) or 0), pnl_pct)
            mae = -mae_val if mae_val < 0 else pos.get("mae_pct", 0) or 0

            if reason:
                exit_reason_str = str(reason.value) if hasattr(reason, "value") else str(reason)
                if not self.dry_run:
                    # Mark as pending_exit — executor will place the SELL order
                    self.repo.update_position(
                        pos["id"],
                        status="pending_exit",
                        exit_reason=exit_reason_str,
                        peak_close=peak_close,
                        delivery_bad_count=delivery_bad_count,
                        bars_held=bars_held,
                        current_pnl_pct=pnl_pct,
                        mfe_pct=round(mfe, 2),
                        mae_pct=round(mae, 2),
                    )
                print(f"  {symbol}: EXIT SIGNAL ({exit_reason_str}), P&L={pnl_pct:+.2f}% → pending_exit")
            else:
                if not self.dry_run:
                    self.repo.update_position(
                        pos["id"],
                        peak_close=peak_close,
                        psz_peak=trade.psz_peak,
                        delivery_bad_count=delivery_bad_count,
                        bars_held=bars_held,
                        current_pnl_pct=pnl_pct,
                        mfe_pct=round(mfe, 2),
                        mae_pct=round(mae, 2),
                    )
                print(f"  {symbol}: HOLD, P&L={pnl_pct:+.2f}%, bars={bars_held}")

    # ── Phase 2: Scan for new signals ────────────────────────────────────

    def _phase2_scan_new_signals(self, today: str):
        symbols = get_watchlist_symbols(self.watchlist_name)
        current_count = self.repo.count_open_positions()
        slots_left = self.max_positions - current_count

        print(f"Phase 2: Scanning {len(symbols)} symbols ({slots_left} slots available)...")

        signals_found = 0
        for symbol in symbols:
            if slots_left <= 0:
                print("  Max positions reached, stopping scan.")
                break

            if self.repo.has_open_position(symbol):
                continue

            result, records = run_engine(symbol)
            if not records or len(records) < 2:
                continue

            last = records[-1]
            prev = records[-2]

            qualifies, soft_count, details = self.signal.check_entry(
                last, prev, self.entry_cfg, records, len(records) - 1,
            )

            psz = last.get("price_slope_z", np.nan)
            prev_psz = prev.get("price_slope_z", np.nan)

            if qualifies:
                signals_found += 1
                pdd_120 = last.get("pdd_120", np.nan)
                regime = details.get("regime", "")
                entry_tag = details.get("entry_tag", "")

                print(f"  {symbol}: SIGNAL (filters={soft_count}, regime={regime}, "
                      f"PSZ={psz:.3f}, PDD120={pdd_120:.2f})")

                if not self.dry_run:
                    # Log signal
                    self.repo.create_signal(
                        symbol=symbol,
                        signal_date=today,
                        signal_type="long_entry",
                        psz_at_signal=float(psz) if not np.isnan(psz) else None,
                        prev_psz=float(prev_psz) if not np.isnan(prev_psz) else None,
                        pdd_120=float(pdd_120) if not np.isnan(pdd_120) else None,
                        regime=regime,
                        soft_filters_passed=soft_count,
                        rdv_pass=int(details.get("rdv", 0)),
                        mcs_pass=int(details.get("mcs", 0)),
                        cwc_pass=int(details.get("cwc", 0)),
                        grad_pass=int(details.get("grad", 0)),
                        acted_upon=1,
                    )

                    # Create proposed position (awaiting human approval)
                    self.repo.create_position(
                        symbol=symbol,
                        mode=self.execution_mode,
                        status="proposed",
                        signal_date=today,
                        soft_filters_passed=soft_count,
                        rdv_pass=int(details.get("rdv", 0)),
                        mcs_pass=int(details.get("mcs", 0)),
                        cwc_pass=int(details.get("cwc", 0)),
                        grad_pass=int(details.get("grad", 0)),
                        regime_at_entry=regime,
                        signal_strategy=self.signal_name,
                        entry_tag=str(entry_tag.value) if hasattr(entry_tag, "value") else str(entry_tag),
                    )
                    slots_left -= 1

        print(f"  Signals found: {signals_found}")

    # ── Phase 3: Daily P&L + equity curve snapshot ──────────────────────

    def _phase3_daily_pnl(self, today: str):
        if self.dry_run:
            print("Phase 3: Skipped (dry-run).")
            return

        # Include both open and pending_exit positions for P&L
        open_positions = self.repo.get_open_positions()
        pending_exits = self.repo.get_pending_exits()
        all_active = open_positions + pending_exits
        active_count = len(all_active)

        total_invested = sum(
            p.get("capital_deployed", 0) or (p.get("entry_price", 0) or 0) * (p.get("quantity", 0) or 0)
            for p in all_active
        )

        unrealized = 0.0
        market_value = 0.0
        if all_active:
            pnls = [p.get("current_pnl_pct", 0) or 0 for p in all_active]
            unrealized = round(sum(pnls) / len(pnls), 2) if pnls else 0
            for p in all_active:
                deployed = p.get("capital_deployed", 0) or 0
                pnl_pct = p.get("current_pnl_pct", 0) or 0
                market_value += deployed * (1 + pnl_pct / 100)

        # Today's realized
        conn = sqlite3.connect(str(DB_PATH))
        realized_today_row = conn.execute(
            "SELECT COALESCE(SUM(final_pnl_pct), 0) FROM trading_positions "
            "WHERE status = 'closed' AND exit_date = ?",
            (today,),
        ).fetchone()
        cumulative_row = conn.execute(
            "SELECT COALESCE(SUM(final_pnl_pct), 0) FROM trading_positions WHERE status = 'closed'"
        ).fetchone()
        conn.close()

        realized_today = round(realized_today_row[0], 2)
        cumulative = round(cumulative_row[0], 2)

        self.repo.upsert_daily_pnl(
            date=today,
            open_positions=active_count,
            total_invested=round(total_invested, 2),
            unrealized_pnl_pct=unrealized,
            realized_pnl_today=realized_today,
            cumulative_realized_pnl=cumulative,
        )

        # Equity curve snapshot
        funds = self.repo.get_funds()
        equity = funds["net_worth"]
        cash = funds["available_capital"]

        # Get peak from previous curve entry
        prev_curve = self.repo.get_equity_curve(days=9999)
        if prev_curve:
            peak_equity = max(equity, max(p["peak_equity"] for p in prev_curve))
        else:
            peak_equity = max(equity, self.capital)

        drawdown = round((peak_equity - equity) / peak_equity * 100, 2) if peak_equity > 0 else 0

        self.repo.upsert_equity_curve(
            date=today,
            equity=round(equity, 2),
            cash=round(cash, 2),
            deployed=round(total_invested, 2),
            market_value=round(market_value, 2),
            peak_equity=round(peak_equity, 2),
            drawdown_pct=drawdown,
            open_positions=active_count,
        )

        print(f"Phase 3: P&L snapshot — active={active_count}, invested=₹{total_invested:,.0f}, "
              f"unrealized={unrealized:+.2f}%, realized_today={realized_today:+.2f}%")


def print_status():
    """Print current open positions and pending signals."""
    repo = TradingRepository()
    cfg = repo.get_config()
    summary = repo.get_summary()

    print("=== Trading Status ===")
    print(f"Mode: {cfg.get('execution_mode', 'paper')}")
    print(f"Signal: {cfg.get('signal_strategy', 'savgol_cts')}")
    print(f"Capital: ₹{float(cfg.get('capital', 0)):,.0f}")
    print(f"Max positions: {cfg.get('max_concurrent_positions', 8)}")
    print(f"Watchlist: {cfg.get('watchlist', 'N/A')}")
    print()

    print(f"Open: {summary['open_positions']}, Pending entries: {summary['pending_entries']}, "
          f"Pending exits: {summary.get('pending_exits', 0)}, "
          f"Proposed: {summary['proposed']}, Closed: {summary['total_closed']}")
    if summary['total_closed'] > 0:
        print(f"Win rate: {summary['win_rate']}%, Avg P&L: {summary['avg_pnl']:+.2f}%, "
              f"Net P&L: {summary['total_net_pnl']:+.2f}%, "
              f"Charges: ₹{summary['total_charges']:,.2f}")

    open_positions = repo.get_open_positions()
    if open_positions:
        print("\nOpen Positions:")
        for p in open_positions:
            pnl = p.get("current_pnl_pct", 0) or 0
            print(f"  {p['symbol']:12s}  entry=₹{p['entry_price']:.2f}  "
                  f"P&L={pnl:+.2f}%  bars={p.get('bars_held', 0)}")

    pending_exits = repo.get_pending_exits()
    if pending_exits:
        print("\nPending Exits (awaiting executor):")
        for p in pending_exits:
            pnl = p.get("current_pnl_pct", 0) or 0
            print(f"  {p['symbol']:12s}  entry=₹{p.get('entry_price', 0):.2f}  "
                  f"reason={p.get('exit_reason', '')}  P&L={pnl:+.2f}%")

    pending = repo.get_pending_entries()
    if pending:
        print("\nPending Entries (approved, awaiting executor):")
        for p in pending:
            print(f"  {p['symbol']:12s}  signal_date={p.get('signal_date', 'N/A')}")

    proposed = repo.get_proposed_positions()
    if proposed:
        print("\nProposed (awaiting approval):")
        for p in proposed:
            print(f"  {p['symbol']:12s}  signal_date={p.get('signal_date', 'N/A')}  "
                  f"regime={p.get('regime_at_entry', '')}  filters={p.get('soft_filters_passed', 0)}")


def main():
    parser = argparse.ArgumentParser(description="Daily trading scanner")
    parser.add_argument("--dry-run", action="store_true", help="Scan and log signals without DB changes")
    parser.add_argument("--status", action="store_true", help="Print current state and exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if args.status:
        print_status()
        return

    scanner = Scanner(dry_run=args.dry_run)
    scanner.run()


if __name__ == "__main__":
    main()
