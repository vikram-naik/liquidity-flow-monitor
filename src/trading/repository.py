"""
Repository pattern for trading module data access.

Centralises all SQL queries for signals, positions, and P&L tracking.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

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
            now = datetime.now().isoformat()
            for key, value in updates.items():
                conn.execute(
                    "INSERT INTO trading_config (key, value, updated_at) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, str(value), now),
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

    def approve_position(self, position_id: int) -> bool:
        """Promote proposed → pending_entry. Returns True if updated."""
        conn = get_db_connection()
        try:
            cursor = conn.execute(
                "UPDATE trading_positions SET status = 'pending_entry', updated_at = ? "
                "WHERE id = ? AND status = 'proposed'",
                (datetime.now().isoformat(), position_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def reject_position(self, position_id: int, reason: str = "manual_reject") -> bool:
        """Mark proposed position as rejected. Returns True if updated."""
        conn = get_db_connection()
        try:
            cursor = conn.execute(
                "UPDATE trading_positions SET status = 'rejected', exit_reason = ?, updated_at = ? "
                "WHERE id = ? AND status = 'proposed'",
                (reason, datetime.now().isoformat(), position_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def approve_all_proposed(self) -> int:
        """Promote all proposed → pending_entry. Returns count updated."""
        conn = get_db_connection()
        try:
            cursor = conn.execute(
                "UPDATE trading_positions SET status = 'pending_entry', updated_at = ? "
                "WHERE status = 'proposed'",
                (datetime.now().isoformat(),),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    def count_open_positions(self) -> int:
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM trading_positions WHERE status IN ('open', 'pending_entry', 'proposed')"
            ).fetchone()
            return row[0]
        finally:
            conn.close()

    def has_open_position(self, symbol: str) -> bool:
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM trading_positions WHERE symbol = ? "
                "AND status IN ('open', 'pending_entry', 'proposed')",
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
        fields["updated_at"] = datetime.now().isoformat()
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
    ) -> None:
        self.update_position(
            position_id,
            status="closed",
            exit_date=exit_date,
            exit_price=exit_price,
            exit_reason=exit_reason,
            final_pnl_pct=final_pnl_pct,
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

    # ── Funds ────────────────────────────────────────────────────────────

    def get_funds(self) -> dict:
        """Compute capital breakdown: total, deployed, available, realized P&L."""
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        try:
            # Seed capital from config
            row = conn.execute(
                "SELECT value FROM trading_config WHERE key = 'capital'"
            ).fetchone()
            total_capital = float(row[0]) if row else 1000000.0

            # Capital deployed in open positions (entry_price * quantity)
            deployed_row = conn.execute(
                "SELECT COALESCE(SUM(entry_price * quantity), 0) "
                "FROM trading_positions WHERE status = 'open'"
            ).fetchone()
            capital_deployed = deployed_row[0]

            # Current market value of open positions
            open_positions = conn.execute(
                "SELECT entry_price, quantity, current_pnl_pct "
                "FROM trading_positions WHERE status = 'open'"
            ).fetchall()

            market_value = 0.0
            unrealized_pnl = 0.0
            for p in open_positions:
                entry_val = (p["entry_price"] or 0) * (p["quantity"] or 0)
                pnl_pct = p["current_pnl_pct"] or 0
                current_val = entry_val * (1 + pnl_pct / 100)
                market_value += current_val
                unrealized_pnl += current_val - entry_val

            # Realized P&L from closed trades (absolute ₹ based on entry_price * quantity * pnl%)
            realized_row = conn.execute(
                "SELECT COALESCE(SUM(entry_price * quantity * final_pnl_pct / 100), 0) "
                "FROM trading_positions WHERE status = 'closed'"
            ).fetchone()
            realized_pnl = realized_row[0]

            # Available = seed capital + realized P&L - capital currently deployed
            available_capital = total_capital + realized_pnl - capital_deployed

            # Pending commitment (approved but not yet filled)
            pending_row = conn.execute(
                "SELECT COUNT(*) FROM trading_positions WHERE status = 'pending_entry'"
            ).fetchone()
            pending_count = pending_row[0]

            # Max positions from config
            max_pos_row = conn.execute(
                "SELECT value FROM trading_config WHERE key = 'max_concurrent_positions'"
            ).fetchone()
            max_positions = int(max_pos_row[0]) if max_pos_row else 8

            # Per-position allocation
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
                "net_worth": round(total_capital + realized_pnl + unrealized_pnl, 2),
            }
        finally:
            conn.close()

    # ── Summary ──────────────────────────────────────────────────────────

    def get_summary(self) -> dict:
        conn = get_db_connection()
        try:
            open_count = conn.execute(
                "SELECT COUNT(*) FROM trading_positions WHERE status = 'open'"
            ).fetchone()[0]

            pending_count = conn.execute(
                "SELECT COUNT(*) FROM trading_positions WHERE status = 'pending_entry'"
            ).fetchone()[0]

            proposed_count = conn.execute(
                "SELECT COUNT(*) FROM trading_positions WHERE status = 'proposed'"
            ).fetchone()[0]

            closed = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN final_pnl_pct > 0 THEN 1 ELSE 0 END) as wins, "
                "AVG(final_pnl_pct) as avg_pnl, "
                "SUM(final_pnl_pct) as total_pnl "
                "FROM trading_positions WHERE status = 'closed'"
            ).fetchone()

            total_closed = closed[0] or 0
            wins = closed[1] or 0
            avg_pnl = round(closed[2] or 0, 2)
            total_pnl = round(closed[3] or 0, 2)
            win_rate = round(wins / total_closed * 100, 1) if total_closed > 0 else 0

            # Unrealized P&L from open positions
            unrealized = conn.execute(
                "SELECT AVG(current_pnl_pct) FROM trading_positions WHERE status = 'open'"
            ).fetchone()[0]

            return {
                "open_positions": open_count,
                "pending_entries": pending_count,
                "proposed": proposed_count,
                "total_closed": total_closed,
                "wins": wins,
                "win_rate": win_rate,
                "avg_pnl": avg_pnl,
                "total_pnl": total_pnl,
                "unrealized_pnl": round(unrealized or 0, 2),
            }
        finally:
            conn.close()
