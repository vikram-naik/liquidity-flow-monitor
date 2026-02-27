from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from datetime import datetime, timezone, date
from typing import List, Optional
import sqlite3
import os
import sys
import requests
import pandas as pd
import io

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

# Import DB path from analytics or database if possible
from src.database import DB_PATH, init_db
from src.analysis.markers import MarkerRegistry
from src.cache import get_cache

cache = get_cache()
_registry = MarkerRegistry()


app = FastAPI(title="LFM API", docs_url="/lfm/api/docs", openapi_url="/lfm/api/openapi.json")

# Mount static web assets
_WEB_DIR = os.path.join(os.path.dirname(__file__), '..', 'web')
if os.path.isdir(_WEB_DIR):
    app.mount('/lfm/static', StaticFiles(directory=_WEB_DIR), name='static')

from fastapi.responses import RedirectResponse
@app.get("/")
def root_redirect():
    return RedirectResponse(url="/lfm/dashboard")

@app.on_event("startup")
def startup_event():
    # 1. Ensure database is initialized
    print("🚀 Initializing database...")
    init_db()
    


# --- Models ---

class NSEUpload(BaseModel):
    record_date: date
    symbol: str
    price_close: float
    volume_total: int
    delivery_qty: int
    delivery_pct: float
    price_change_pct: Optional[float] = 0.0
    volume_change_pct: Optional[float] = 0.0
    delivery_change_pct: Optional[float] = 0.0


# --- Database Helpers ---

def get_db():
    # Use environment variable if provided (for Docker)
    db_path = os.getenv("DB_PATH", DB_PATH)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()



# --- Routes ---

@app.get("/lfm/api/health")
def health_check():
    return {"status": "healthy", "service": "lfm-api"}


# --- Divergence Engine Routes ---

from fastapi.responses import HTMLResponse

@app.get("/lfm/api/divergence-engine/{symbol}")
def divergence_engine_data(
    symbol: str,
    start_date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
):
    """Run the divergence engine and return JSON ledger + state summary."""
    try:
        from src.divergence_engine.engine import DivergenceEngine
        from src.divergence_engine.chart import ledger_to_json, state_summary_to_json

        engine = DivergenceEngine(
            ticker=symbol.upper(),
            start_date=start_date,
            end_date=end_date,
        )
        result = engine.run()

        return {
            "ticker": result.ticker,
            "bars": len(result.ledger),
            "latest": state_summary_to_json(result),
            "ledger": ledger_to_json(result.ledger),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Engine error: {e}")


@app.get("/lfm/divergence-engine/{symbol}")
def divergence_engine_chart(symbol: str):
    """Serve the static divergence engine chart page.

    The HTML page loads data via fetch() from /lfm/api/divergence-engine/{symbol}.
    """
    html_path = os.path.join(_WEB_DIR, "divergence_engine.html")
    if not os.path.isfile(html_path):
        raise HTTPException(status_code=404, detail="Chart page not found")
    return FileResponse(html_path, media_type="text/html")
