"""
FastAPI router for trading module endpoints.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.trading.repository import TradingRepository

router = APIRouter()
repo = TradingRepository()

_WEB_DIR = os.path.join(os.path.dirname(__file__), '..', 'web')


# ── Models ───────────────────────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    updates: dict[str, str]


class RejectRequest(BaseModel):
    reason: str = "manual_reject"


class CapitalEventRequest(BaseModel):
    event_type: str  # 'injection' | 'withdrawal'
    amount: float
    note: str = ""


# ── Page ─────────────────────────────────────────────────────────────────────

@router.get("/de/trades")
def trades_page():
    html_path = os.path.join(_WEB_DIR, "trades.html")
    if not os.path.isfile(html_path):
        raise HTTPException(status_code=404, detail="Trades page not found")
    return FileResponse(html_path, media_type="text/html")


# ── API Endpoints ────────────────────────────────────────────────────────────

@router.get("/de/api/trading/positions")
def list_positions(status: str = Query("open", pattern="^(open|closed|pending_entry|pending_exit|proposed|rejected|all)$")):
    return repo.get_positions(status)


@router.get("/de/api/trading/positions/{position_id}")
def get_position(position_id: int):
    pos = repo.get_position(position_id)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    return pos


@router.get("/de/api/trading/signals")
def list_signals(days: int = Query(30, ge=1, le=365)):
    return repo.get_signals(days)


@router.get("/de/api/trading/trades")
def list_trades(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return repo.get_closed_trades(limit, offset)


@router.get("/de/api/trading/pnl")
def daily_pnl(days: int = Query(90, ge=1, le=365)):
    return repo.get_daily_pnl(days)


@router.get("/de/api/trading/summary")
def trading_summary():
    return repo.get_summary()


@router.get("/de/api/trading/funds")
def trading_funds():
    return repo.get_funds()


@router.get("/de/api/trading/config")
def get_config():
    return repo.get_config()


@router.put("/de/api/trading/config")
def update_config(body: ConfigUpdate):
    repo.update_config(body.updates)
    return {"status": "ok"}


# ── Orders ───────────────────────────────────────────────────────────────────

@router.get("/de/api/trading/orders")
def list_orders(limit: int = Query(50, ge=1, le=500)):
    return repo.get_recent_orders(limit)


@router.get("/de/api/trading/orders/{position_id}")
def orders_for_position(position_id: int):
    return repo.get_orders_for_position(position_id)


# ── Equity Curve ─────────────────────────────────────────────────────────────

@router.get("/de/api/trading/equity-curve")
def equity_curve(days: int = Query(365, ge=1, le=3650)):
    return repo.get_equity_curve(days)


# ── Ledger ───────────────────────────────────────────────────────────────────

@router.get("/de/api/trading/ledger")
def cash_ledger():
    return repo.get_ledger()


# ── Capital Events ───────────────────────────────────────────────────────────

@router.get("/de/api/trading/capital-events")
def list_capital_events():
    return repo.get_capital_events()


@router.post("/de/api/trading/capital-events")
def add_capital_event(body: CapitalEventRequest):
    if body.event_type not in ("injection", "withdrawal"):
        raise HTTPException(status_code=400, detail="event_type must be 'injection' or 'withdrawal'")
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")

    current_capital = repo.get_total_capital()
    if body.event_type == "injection":
        balance_after = current_capital + body.amount
    else:
        if body.amount > current_capital:
            raise HTTPException(status_code=400, detail="Withdrawal exceeds available capital")
        balance_after = current_capital - body.amount

    event_id = repo.create_capital_event(
        date=datetime.now().strftime("%Y-%m-%d"),
        event_type=body.event_type,
        amount=body.amount,
        balance_after=round(balance_after, 2),
        note=body.note,
    )
    return {"status": "ok", "id": event_id, "balance_after": round(balance_after, 2)}


# ── Position Approval ───────────────────────────────────────────────────────

@router.post("/de/api/trading/positions/{position_id}/approve")
def approve_position(position_id: int):
    """Promote a proposed position to pending_entry (will execute next scanner run)."""
    ok = repo.approve_position(position_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Position not found or not in 'proposed' status")
    return {"status": "approved", "id": position_id}


@router.post("/de/api/trading/positions/{position_id}/reject")
def reject_position(position_id: int, body: RejectRequest = RejectRequest()):
    """Reject a proposed position."""
    ok = repo.reject_position(position_id, body.reason)
    if not ok:
        raise HTTPException(status_code=404, detail="Position not found or not in 'proposed' status")
    return {"status": "rejected", "id": position_id}


@router.post("/de/api/trading/positions/approve-all")
def approve_all_positions():
    """Approve all proposed positions at once."""
    count = repo.approve_all_proposed()
    return {"status": "ok", "approved": count}


# ── Scan ─────────────────────────────────────────────────────────────────────

@router.post("/de/api/trading/scan")
def trigger_scan(dry_run: bool = Query(False)):
    """Trigger a manual scanner run in a background thread."""
    from src.trading.scanner import Scanner

    def _run():
        try:
            scanner = Scanner(dry_run=dry_run)
            scanner.run()
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Scan failed: %s", e)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"status": "scan_started", "dry_run": dry_run}


@router.post("/de/api/trading/execute")
def trigger_execute(dry_run: bool = Query(False)):
    """Trigger order execution in a background thread.

    Resolves prices and places BUY/SELL orders for all pending entries and exits.
    Designed to be called via cron during market hours the day after scanning.
    """
    from src.trading.executor import OrderExecutor

    def _run():
        try:
            executor = OrderExecutor(dry_run=dry_run)
            result = executor.run()
            import logging
            logging.getLogger(__name__).info("Execution complete: %s", result)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Execution failed: %s", e)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"status": "execution_started", "dry_run": dry_run}
