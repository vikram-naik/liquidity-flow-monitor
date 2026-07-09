"""
Repository pattern for trading module data access.

Centralises all SQL queries for signals, positions, orders, and P&L tracking.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

def _get_utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

from src.database import get_db_connection


class TradingRepository:
    """CRUD operations for trading tables."""

    # ── Config ───────────────────────────────────────────────────────────

    def get_config(self) -> dict:
        conn = get_db_connection()
        try:
            rows = conn.execute("SELECT key, value FROM trading_config").fetchall()
            return {r[0]: r[1] for r in rows}
        finally:
            conn.close()

    def update_config(self, updates: dict) -> None:
        conn = get_db_connection()
        try:
            now = _get_utc_now_str()
            for key, value in updates.items():
                conn.execute(
                    "INSERT INTO trading_config (key, value, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, str(value), now, now),
                )
            conn.commit()
        finally:
            conn.close()

    # ── Positions ────────────────────────────────────────────────────────

    def get_open_positions(self) -> list[dict]:
        return self._query_positions("status = 'open'")

    def get_pending_entries(self) -> list[dict]:
        return self._query_positions("status = 'pending_entry'")

    def get_proposed_positions(self) -> list[dict]:
        return self._query_positions("status = 'proposed'")

    def get_pending_exits(self) -> list[dict]:
        return self._query_positions("status = 'pending_exit'")

    def approve_position(self, position_id: int) -> bool:
        """Promote proposed -> pending_entry or proposed_exit -> pending_exit. Returns True if updated."""
        conn = get_db_connection()
        try:
            now = _get_utc_now_str()
            cursor = conn.execute(
                "UPDATE trading_positions "
                "SET status = CASE status "
                "  WHEN 'proposed' THEN 'pending_entry' "
                "  WHEN 'proposed_exit' THEN 'pending_exit' "
                "END, "
                "approved_at = ?, "
                "updated_at = ? "
                "WHERE id = ? AND status IN ('proposed', 'proposed_exit')",
                (now, now, position_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def reject_position(self, position_id: int, reason: str = "manual_reject") -> bool:
        """Mark proposed position as rejected, or restore proposed_exit to open. Returns True if updated."""
        conn = get_db_connection()
        try:
            now = _get_utc_now_str()
            cursor = conn.execute(
                "UPDATE trading_positions "
                "SET status = CASE status "
                "  WHEN 'proposed' THEN 'rejected' "
                "  WHEN 'proposed_exit' THEN 'open' "
                "END, "
                "exit_reason = CASE WHEN status = 'proposed' THEN ? ELSE exit_reason END, "
                "rejected_at = ?, "
                "updated_at = ? "
                "WHERE id = ? AND status IN ('proposed', 'proposed_exit')",
                (reason, now, now, position_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def approve_all_proposed(self) -> int:
        """Promote all proposed -> pending_entry and proposed_exit -> pending_exit. Returns count updated."""
        conn = get_db_connection()
        try:
            now = _get_utc_now_str()
            cursor = conn.execute(
                "UPDATE trading_positions "
                "SET status = CASE status "
                "  WHEN 'proposed' THEN 'pending_entry' "
                "  WHEN 'proposed_exit' THEN 'pending_exit' "
                "END, "
                "approved_at = ?, "
                "updated_at = ? "
                "WHERE status IN ('proposed', 'proposed_exit')",
                (now, now),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    def mark_manual_exit(self, position_id: int) -> bool:
        """Mark open position as pending_exit for manual reason. Returns True if updated."""
        conn = get_db_connection()
        try:
            now = _get_utc_now_str()
            cursor = conn.execute(
                "UPDATE trading_positions SET status = 'pending_exit', exit_reason = 'manual_exit', updated_at = ? "
                "WHERE id = ? AND status = 'open'",
                (now, position_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def count_open_positions(self) -> int:
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM trading_positions WHERE status IN ('open', 'pending_entry', 'proposed', 'pending_exit', 'proposed_exit')"
            ).fetchone()
            return row[0]
        finally:
            conn.close()

    def has_open_position(self, symbol: str) -> bool:
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM trading_positions WHERE symbol = ? "
                "AND status IN ('open', 'pending_entry', 'proposed', 'pending_exit', 'proposed_exit')",
                (symbol,),
            ).fetchone()
            return row[0] > 0
        finally:
            conn.close()

    def create_position(self, **fields) -> int:
        conn = get_db_connection()
        try:
            cols = ", ".join(fields.keys())
            placeholders = ", ".join(["?"] * len(fields))
            cursor = conn.execute(
                f"INSERT INTO trading_positions ({cols}) VALUES ({placeholders})",
                list(fields.values()),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def update_position(self, position_id: int, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = _get_utc_now_str()
        conn = get_db_connection()
        try:
            set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
            conn.execute(
                f"UPDATE trading_positions SET {set_clause} WHERE id = ?",
                [*fields.values(), position_id],
            )
            conn.commit()
        finally:
            conn.close()

    def close_position(
        self,
        position_id: int,
        exit_date: str,
        exit_price: float,
        exit_reason: str,
        final_pnl_pct: float,
        exit_charges: float = 0.0,
        total_charges: float = 0.0,
        net_pnl_pct: float | None = None,
        net_pnl_abs: float | None = None,
    ) -> None:
        self.update_position(
            position_id,
            status="closed",
            exit_date=exit_date,
            exit_price=exit_price,
            exit_reason=exit_reason,
            final_pnl_pct=final_pnl_pct,
            exit_charges=exit_charges,
            total_charges=total_charges,
            net_pnl_pct=net_pnl_pct,
            net_pnl_abs=net_pnl_abs,
        )

    def get_closed_trades(self, limit: int = 100, offset: int = 0) -> list[dict]:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM trading_positions WHERE status = 'closed' "
                "ORDER BY exit_date DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_position(self, position_id: int) -> Optional[dict]:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM trading_positions WHERE id = ?", (position_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_positions(self, status: Optional[str] = None) -> list[dict]:
        if status and status != "all":
            return self._query_positions(f"status = '{status}'")
        return self._query_positions("1=1")

    def _query_positions(self, where: str) -> list[dict]:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                f"SELECT * FROM trading_positions WHERE {where} ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Orders ──────────────────────────────────────────────────────────

    def create_order(self, **fields) -> int:
        conn = get_db_connection()
        try:
            cols = ", ".join(fields.keys())
            placeholders = ", ".join(["?"] * len(fields))
            cursor = conn.execute(
                f"INSERT INTO trading_orders ({cols}) VALUES ({placeholders})",
                list(fields.values()),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_orders_for_position(self, position_id: int) -> list[dict]:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM trading_orders WHERE position_id = ? ORDER BY executed_at",
                (position_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_recent_orders(self, limit: int = 50) -> list[dict]:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM trading_orders ORDER BY executed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Signals ──────────────────────────────────────────────────────────

    def create_signal(self, **fields) -> int:
        conn = get_db_connection()
        try:
            cols = ", ".join(fields.keys())
            placeholders = ", ".join(["?"] * len(fields))
            cursor = conn.execute(
                f"INSERT OR IGNORE INTO trading_signals ({cols}) VALUES ({placeholders})",
                list(fields.values()),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_signals(self, days: int = 30) -> list[dict]:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM trading_signals "
                "WHERE signal_date >= date('now', ? || ' days') "
                "ORDER BY signal_date DESC",
                (f"-{days}",),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Daily P&L ────────────────────────────────────────────────────────

    def upsert_daily_pnl(
        self,
        date: str,
        open_positions: int,
        total_invested: float,
        unrealized_pnl_pct: float,
        realized_pnl_today: float,
        cumulative_realized_pnl: float,
    ) -> None:
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO trading_daily_pnl "
                "(date, open_positions, total_invested, unrealized_pnl_pct, "
                "realized_pnl_today, cumulative_realized_pnl) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(date) DO UPDATE SET "
                "open_positions=excluded.open_positions, "
                "total_invested=excluded.total_invested, "
                "unrealized_pnl_pct=excluded.unrealized_pnl_pct, "
                "realized_pnl_today=excluded.realized_pnl_today, "
                "cumulative_realized_pnl=excluded.cumulative_realized_pnl",
                (date, open_positions, total_invested, unrealized_pnl_pct,
                 realized_pnl_today, cumulative_realized_pnl),
            )
            conn.commit()
        finally:
            conn.close()

    def get_daily_pnl(self, days: int = 90) -> list[dict]:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM trading_daily_pnl "
                "WHERE date >= date('now', ? || ' days') "
                "ORDER BY date ASC",
                (f"-{days}",),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Capital Events ──────────────────────────────────────────────────

    def create_capital_event(
        self, date: str, event_type: str, amount: float,
        balance_after: float, note: str = "",
    ) -> int:
        conn = get_db_connection()
        try:
            cursor = conn.execute(
                "INSERT INTO trading_capital_events (date, event_type, amount, balance_after, note) "
                "VALUES (?, ?, ?, ?, ?)",
                (date, event_type, amount, balance_after, note),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_capital_events(self) -> list[dict]:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM trading_capital_events ORDER BY date ASC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_total_capital(self) -> float:
        """Seed capital + injections - withdrawals."""
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT value FROM trading_config WHERE key = 'capital'"
            ).fetchone()
            seed = float(row[0]) if row else 1_000_000.0

            events = conn.execute(
                "SELECT event_type, COALESCE(SUM(amount), 0) "
                "FROM trading_capital_events GROUP BY event_type"
            ).fetchall()
            adjustments = {r[0]: r[1] for r in events}
            injections = adjustments.get("injection", 0)
            withdrawals = adjustments.get("withdrawal", 0)
            return seed + injections - withdrawals
        finally:
            conn.close()

    # ── Ledger ──────────────────────────────────────────────────────────

    def get_ledger(self) -> list[dict]:
        """Chronological cash-flow ledger: seed, orders, and capital events.

        Returns rows sorted by date with a running balance.
        """
        conn = get_db_connection()
        try:
            # Seed capital
            row = conn.execute(
                "SELECT value FROM trading_config WHERE key = 'capital'"
            ).fetchone()
            seed = float(row[0]) if row else 1_000_000.0

            entries: list[dict] = []

            # Capital events
            cap_rows = conn.execute(
                "SELECT date, event_type, amount, note FROM trading_capital_events ORDER BY date"
            ).fetchall()
            for r in cap_rows:
                is_inj = r[1] == "injection"
                entries.append({
                    "date": r[0],
                    "type": "injection" if is_inj else "withdrawal",
                    "symbol": "",
                    "description": r[3] or ("Capital injection" if is_inj else "Capital withdrawal"),
                    "cash_in": r[2] if is_inj else 0.0,
                    "cash_out": 0.0 if is_inj else r[2],
                    "charges": 0.0,
                })

            # Orders (BUY = cash out, SELL = cash in)
            ord_rows = conn.execute(
                "SELECT executed_at, symbol, side, quantity, price, turnover, "
                "total_charges, net_amount, position_id "
                "FROM trading_orders ORDER BY executed_at"
            ).fetchall()
            for r in ord_rows:
                date_str = r[0][:10] if r[0] else ""
                side = r[2]
                if side == "BUY":
                    cash_out = r[5] + r[6]  # turnover + charges
                    cash_in = 0.0
                else:
                    cash_in = r[5] - r[6]  # turnover - charges
                    cash_out = 0.0
                entries.append({
                    "date": date_str,
                    "type": side.lower(),
                    "symbol": r[1],
                    "description": f"{side} {r[3]} x {r[1]} @ ₹{r[4]:.2f}",
                    "cash_in": round(cash_in, 2),
                    "cash_out": round(cash_out, 2),
                    "charges": round(r[6], 2),
                })

            # Sort by date, then capital events before orders on same date
            type_order = {"injection": 0, "withdrawal": 1, "buy": 2, "sell": 3}
            entries.sort(key=lambda e: (e["date"], type_order.get(e["type"], 9)))

            # Compute running balance starting from seed
            balance = seed
            result = [{
                "date": "",
                "type": "seed",
                "symbol": "",
                "description": "Seed capital",
                "cash_in": seed,
                "cash_out": 0.0,
                "charges": 0.0,
                "balance": seed,
            }]
            for e in entries:
                balance = balance + e["cash_in"] - e["cash_out"]
                result.append({**e, "balance": round(balance, 2)})

            return result
        finally:
            conn.close()

    # ── Equity Curve ────────────────────────────────────────────────────

    def upsert_equity_curve(
        self, date: str, equity: float, cash: float, deployed: float,
        market_value: float, peak_equity: float, drawdown_pct: float,
        open_positions: int, sizing_method: str | None = None,
        kelly_f: float | None = None,
    ) -> None:
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO trading_equity_curve "
                "(date, equity, cash, deployed, market_value, peak_equity, "
                "drawdown_pct, open_positions, sizing_method, kelly_f) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(date) DO UPDATE SET "
                "equity=excluded.equity, cash=excluded.cash, "
                "deployed=excluded.deployed, market_value=excluded.market_value, "
                "peak_equity=excluded.peak_equity, drawdown_pct=excluded.drawdown_pct, "
                "open_positions=excluded.open_positions, sizing_method=excluded.sizing_method, "
                "kelly_f=excluded.kelly_f",
                (date, equity, cash, deployed, market_value, peak_equity,
                 drawdown_pct, open_positions, sizing_method, kelly_f),
            )
            conn.commit()
        finally:
            conn.close()

    def get_equity_curve(self, days: int = 365) -> list[dict]:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM trading_equity_curve "
                "WHERE date >= date('now', ? || ' days') "
                "ORDER BY date ASC",
                (f"-{days}",),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Funds ────────────────────────────────────────────────────────────

    def get_funds(self) -> dict:
        """Compute capital breakdown: total, deployed, available, realized P&L."""
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        try:
            total_capital = self.get_total_capital()

            # Capital deployed in open + pending_exit + proposed_exit positions
            deployed_row = conn.execute(
                "SELECT COALESCE(SUM(capital_deployed), 0) "
                "FROM trading_positions WHERE status IN ('open', 'pending_exit', 'proposed_exit')"
            ).fetchone()
            capital_deployed = deployed_row[0]

            # Current market value of open + pending_exit + proposed_exit positions
            open_positions = conn.execute(
                "SELECT capital_deployed, current_pnl_pct "
                "FROM trading_positions WHERE status IN ('open', 'pending_exit', 'proposed_exit')"
            ).fetchall()

            market_value = 0.0
            unrealized_pnl = 0.0
            for p in open_positions:
                deployed = p["capital_deployed"] or 0
                pnl_pct = p["current_pnl_pct"] or 0
                current_val = deployed * (1 + pnl_pct / 100)
                market_value += current_val
                unrealized_pnl += current_val - deployed

            # Realized P&L (net of charges)
            realized_row = conn.execute(
                "SELECT COALESCE(SUM(net_pnl_abs), 0) "
                "FROM trading_positions WHERE status = 'closed'"
            ).fetchone()
            realized_pnl = realized_row[0]

            # Total charges paid
            charges_row = conn.execute(
                "SELECT COALESCE(SUM(total_charges), 0) "
                "FROM trading_positions WHERE status = 'closed'"
            ).fetchone()
            total_charges_paid = charges_row[0]

            # Available = total capital + realized P&L - deployed
            available_capital = total_capital + realized_pnl - capital_deployed

            # Pending reserved
            pending_row = conn.execute(
                "SELECT COUNT(*) FROM trading_positions WHERE status = 'pending_entry'"
            ).fetchone()
            pending_count = pending_row[0]

            max_pos_row = conn.execute(
                "SELECT value FROM trading_config WHERE key = 'max_concurrent_positions'"
            ).fetchone()
            max_positions = int(max_pos_row[0]) if max_pos_row else 8
            per_position = total_capital / max_positions if max_positions > 0 else 0
            pending_reserved = pending_count * per_position

            return {
                "total_capital": round(total_capital, 2),
                "capital_deployed": round(capital_deployed, 2),
                "market_value": round(market_value, 2),
                "available_capital": round(available_capital, 2),
                "pending_reserved": round(pending_reserved, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "realized_pnl": round(realized_pnl, 2),
                "total_charges_paid": round(total_charges_paid, 2),
                "net_worth": round(total_capital + realized_pnl + unrealized_pnl, 2),
            }
        finally:
            conn.close()

    # ── Summary ──────────────────────────────────────────────────────────

    def get_summary(self) -> dict:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        try:
            total_capital = self.get_total_capital()

            open_count = conn.execute(
                "SELECT COUNT(*) FROM trading_positions WHERE status IN ('open', 'proposed_exit')"
            ).fetchone()[0]

            pending_count = conn.execute(
                "SELECT COUNT(*) FROM trading_positions WHERE status = 'pending_entry'"
            ).fetchone()[0]

            proposed_count = conn.execute(
                "SELECT COUNT(*) FROM trading_positions WHERE status IN ('proposed', 'proposed_exit')"
            ).fetchone()[0]

            pending_exit_count = conn.execute(
                "SELECT COUNT(*) FROM trading_positions WHERE status = 'pending_exit'"
            ).fetchone()[0]

            closed = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN final_pnl_pct > 0 THEN 1 ELSE 0 END) as wins, "
                "SUM(net_pnl_abs) as total_net_pnl_abs, "
                "SUM(net_pnl_abs + total_charges) as total_gross_pnl_abs, "
                "SUM(total_charges) as total_charges, "
                "AVG(bars_held) as avg_duration_bars "
                "FROM trading_positions WHERE status = 'closed'"
            ).fetchone()

            total_closed = closed[0] or 0
            wins = closed[1] or 0
            win_rate = round(wins / total_closed * 100, 1) if total_closed > 0 else 0

            total_net_pnl_abs = closed[2] or 0.0
            total_gross_pnl_abs = closed[3] or 0.0
            total_charges = closed[4] or 0.0
            avg_duration_bars = closed[5] or 0.0

            total_net_pnl_pct = (total_net_pnl_abs / total_capital * 100) if total_capital > 0 else 0.0
            total_gross_pnl_pct = (total_gross_pnl_abs / total_capital * 100) if total_capital > 0 else 0.0

            open_positions = conn.execute(
                "SELECT capital_deployed, current_pnl_pct "
                "FROM trading_positions WHERE status IN ('open', 'pending_exit', 'proposed_exit')"
            ).fetchall()

            unrealized_pnl_abs = 0.0
            for p in open_positions:
                deployed = p["capital_deployed"] or 0.0
                pnl_pct = p["current_pnl_pct"] or 0.0
                unrealized_pnl_abs += deployed * (pnl_pct / 100.0)

            unrealized_pnl_pct = (unrealized_pnl_abs / total_capital * 100) if total_capital > 0 else 0.0

            # CAGR calculation
            first_event_date = conn.execute("SELECT MIN(date) FROM trading_capital_events").fetchone()[0]
            first_trade_date = conn.execute("SELECT MIN(entry_date) FROM trading_positions WHERE status = 'closed'").fetchone()[0]
            
            start_date_str = None
            if first_event_date and first_trade_date:
                start_date_str = min(first_event_date, first_trade_date)
            elif first_event_date:
                start_date_str = first_event_date
            elif first_trade_date:
                start_date_str = first_trade_date
                
            cagr_pct = 0.0
            nifty_cagr_pct = None  # None means data not available
            nifty_start_date = None
            if start_date_str:
                try:
                    start_dt = datetime.strptime(start_date_str[:10], "%Y-%m-%d")
                    end_dt = datetime.strptime(datetime.now().strftime("%Y-%m-%d"), "%Y-%m-%d")
                    days = (end_dt - start_dt).days
                    if days > 0 and total_capital > 0:
                        ending_val = total_capital + total_net_pnl_abs
                        if ending_val > 0:
                            cagr = (ending_val / total_capital) ** (365.25 / days) - 1.0
                            cagr_pct = cagr * 100.0
                        else:
                            cagr_pct = -100.0

                    # ── Nifty 50 benchmark CAGR over the same period ──────────
                    # Find the closest Nifty 50 trading day on or after start_date
                    nifty_start_row = conn.execute(
                        "SELECT record_date, price_close FROM nse_delivery_log "
                        "WHERE symbol = 'NIFTY 50' AND record_date >= ? AND price_close IS NOT NULL "
                        "ORDER BY record_date ASC LIMIT 1",
                        (start_date_str[:10],)
                    ).fetchone()
                    # Most recent available Nifty 50 close
                    nifty_end_row = conn.execute(
                        "SELECT record_date, price_close FROM nse_delivery_log "
                        "WHERE symbol = 'NIFTY 50' AND price_close IS NOT NULL "
                        "ORDER BY record_date DESC LIMIT 1"
                    ).fetchone()

                    if nifty_start_row and nifty_end_row:
                        n_start_price = nifty_start_row[1]
                        n_end_price = nifty_end_row[1]
                        nifty_start_date = nifty_start_row[0]
                        n_start_dt = datetime.strptime(nifty_start_row[0][:10], "%Y-%m-%d")
                        n_end_dt = datetime.strptime(nifty_end_row[0][:10], "%Y-%m-%d")
                        n_days = (n_end_dt - n_start_dt).days
                        if n_days > 0 and n_start_price and n_start_price > 0 and n_end_price and n_end_price > 0:
                            n_cagr = (n_end_price / n_start_price) ** (365.25 / n_days) - 1.0
                            nifty_cagr_pct = round(n_cagr * 100.0, 2)
                except Exception:
                    pass

            return {
                "open_positions": open_count,
                "pending_entries": pending_count,
                "pending_exits": pending_exit_count,
                "proposed": proposed_count,
                "total_closed": total_closed,
                "wins": wins,
                "win_rate": win_rate,
                "total_gross_pnl_abs": round(total_gross_pnl_abs, 2),
                "total_gross_pnl_pct": round(total_gross_pnl_pct, 2),
                "total_net_pnl_abs": round(total_net_pnl_abs, 2),
                "total_net_pnl_pct": round(total_net_pnl_pct, 2),
                "total_charges": round(total_charges, 2),
                "avg_duration_bars": round(avg_duration_bars, 1),
                "unrealized_pnl_abs": round(unrealized_pnl_abs, 2),
                "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
                "cagr_pct": round(cagr_pct, 2),
                "nifty_cagr_pct": nifty_cagr_pct,
                "nifty_start_date": nifty_start_date,
            }
        finally:
            conn.close()

    # ── Watchlists ───────────────────────────────────────────────────────

    def sync_positions_watchlist(self) -> None:
        """Keep a 'POSITIONS' watchlist synced with currently held positions."""
        conn = get_db_connection()
        try:
            # Check if POSITIONS watchlist exists, create if not
            row = conn.execute("SELECT id FROM watchlists WHERE name = 'POSITIONS'").fetchone()
            if row:
                wl_id = row[0]
            else:
                cursor = conn.execute(
                    "INSERT INTO watchlists (name, description) VALUES (?, ?)",
                    ("POSITIONS", "Auto-managed watchlist of active positions")
                )
                wl_id = cursor.lastrowid

            # Get distinct symbols of held positions
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM trading_positions WHERE status IN ('open', 'pending_exit', 'proposed_exit')"
            ).fetchall()
            symbols = [r[0] for r in rows]

            # Clear existing items for this watchlist
            conn.execute("DELETE FROM watchlist_items WHERE watchlist_id = ?", (wl_id,))

            # Insert current symbols
            for i, sym in enumerate(symbols):
                conn.execute(
                    "INSERT INTO watchlist_items (watchlist_id, symbol, display_order) VALUES (?, ?, ?)",
                    (wl_id, sym, i + 1)
                )

            conn.commit()
        finally:
            conn.close()
