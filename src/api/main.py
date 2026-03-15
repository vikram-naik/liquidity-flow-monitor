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
from src.cache import get_cache

cache = get_cache()

NSE_INDICES = {
    "NIFTY 50": "ind_nifty50list.csv",
    "NIFTY NEXT 50": "ind_niftynext50list.csv",
    "NIFTY 100": "ind_nifty100list.csv",
    "NIFTY 200": "ind_nifty200list.csv",
    "NIFTY 500": "ind_nifty500list.csv",
    "NIFTY MIDCAP 50": "ind_niftymidcap50list.csv",
    "NIFTY MIDCAP 100": "ind_niftymidcap100list.csv",
    "NIFTY SMALLCAP 100": "ind_niftysmallcap100list.csv",
    "NIFTY BANK": "ind_niftybanklist.csv",
    "NIFTY FIN SERVICE": "ind_niftyfinancelist.csv",
    "NIFTY IT": "ind_niftyitlist.csv",
    "NIFTY PHARMA": "ind_niftypharmalist.csv",
    "NIFTY FMCG": "ind_niftyfmcglist.csv",
    "NIFTY METAL": "ind_niftymetallist.csv",
    "NIFTY REALTY": "ind_niftyrealtylist.csv",
    "NIFTY AUTO": "ind_niftyautolist.csv",
    "NIFTY DEFENCE": "ind_niftyindiadefence_list.csv",
}


app = FastAPI(title="LFM Divergence Engine", docs_url="/de/api/docs", openapi_url="/de/api/openapi.json")

# Mount static web assets
_WEB_DIR = os.path.join(os.path.dirname(__file__), '..', 'web')
if os.path.isdir(_WEB_DIR):
    app.mount('/de/static', StaticFiles(directory=_WEB_DIR), name='static')

from fastapi.responses import RedirectResponse
@app.get("/")
def root_redirect():
    return RedirectResponse(url="/de/dashboard/RELIANCE")

@app.on_event("startup")
def startup_event():
    print("Initializing database...")
    init_db()


# --- Models ---

class WatchlistCreate(BaseModel):
    name: str
    description: Optional[str] = None

class WatchlistUpdate(BaseModel):
    name: str

class WatchlistItemAdd(BaseModel):
    symbol: str

class IndexImportRequest(BaseModel):
    watchlist_id: int
    index_name: str


# --- Database Helpers ---

def get_db():
    db_path = os.getenv("DB_PATH", DB_PATH)
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    try:
        yield conn
    finally:
        conn.close()


# --- Routes ---

@app.get("/de/api/health")
def health_check():
    return {"status": "healthy", "service": "lfm-api"}


# --- Divergence Engine Routes ---

from fastapi.responses import HTMLResponse

@app.get("/de/api/divergence-engine/{symbol}")
def divergence_engine_data(
    symbol: str,
    start_date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    agg_mode: str = Query("daily", description="Aggregation mode: daily, weekly, monthly"),
):
    """Run the divergence engine and return JSON ledger + state summary."""
    if agg_mode not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=400, detail=f"Invalid agg_mode '{agg_mode}'. Must be daily, weekly, or monthly.")
    try:
        from src.divergence_engine.engine import DivergenceEngine
        from src.divergence_engine.chart import ledger_to_json, state_summary_to_json

        engine = DivergenceEngine(
            ticker=symbol.upper(),
            start_date=start_date,
            end_date=end_date,
            agg_mode=agg_mode,
        )
        result = engine.run()

        last_date = None
        if result.ledger is not None and len(result.ledger) > 0:
            try:
                last_date = result.ledger.iloc[-1].date if hasattr(result.ledger, 'iloc') else result.ledger[-1].date
            except:
                pass

        return {
            "ticker": result.ticker,
            "bars": len(result.ledger),
            "last_data_date": last_date,
            "latest": state_summary_to_json(result),
            "ledger": ledger_to_json(result.ledger),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Engine error: {e}")


@app.get("/de/dashboard/{symbol}")
def divergence_engine_chart(symbol: str):
    """Serve the main chart UI for a given symbol."""
    html_path = os.path.join(_WEB_DIR, "divergence_engine.html")
    if not os.path.isfile(html_path):
        raise HTTPException(status_code=404, detail="Chart page not found")
    return FileResponse(html_path, media_type="text/html")


@app.get("/de/help", response_class=HTMLResponse)
def help_guide():
    """Serve the help guide page."""
    html_path = os.path.join(_WEB_DIR, "help_guide.html")
    if not os.path.isfile(html_path):
        raise HTTPException(status_code=404, detail="Help guide not found")
    return FileResponse(html_path, media_type="text/html")


# --- Stock & Analysis Routes ---

@app.get("/de/api/analysis/stocks")
def get_all_stocks(db: sqlite3.Connection = Depends(get_db)):
    """Return all unique symbols and their latest names/prices."""
    query = """
    SELECT symbol, MAX(record_date) as last_date
    FROM nse_delivery_log
    GROUP BY symbol
    ORDER BY symbol ASC
    """
    try:
        cursor = db.execute(query)
        stocks = [dict(row) for row in cursor.fetchall()]
        return stocks
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Watchlist Routes ---

@app.get("/de/api/watchlists")
def list_watchlists(db: sqlite3.Connection = Depends(get_db)):
    cursor = db.execute("SELECT id, name, description, created_at FROM watchlists ORDER BY name ASC")
    return [dict(row) for row in cursor.fetchall()]

@app.post("/de/api/watchlists")
def create_watchlist(wl: WatchlistCreate, db: sqlite3.Connection = Depends(get_db)):
    try:
        cursor = db.execute("INSERT INTO watchlists (name, description) VALUES (?, ?)", (wl.name, wl.description))
        db.commit()
        return {"id": cursor.lastrowid, "name": wl.name}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Watchlist name already exists")

@app.patch("/de/api/watchlists/{watchlist_id}")
def rename_watchlist(watchlist_id: int, wl: WatchlistUpdate, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.execute("UPDATE watchlists SET name = ? WHERE id = ?", (wl.name, watchlist_id))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    db.commit()
    return {"status": "ok"}

@app.delete("/de/api/watchlists/{watchlist_id}")
def delete_watchlist(watchlist_id: int, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.execute("DELETE FROM watchlists WHERE id = ?", (watchlist_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    db.commit()
    return {"status": "ok", "deleted_id": watchlist_id}

@app.get("/de/api/watchlists/{watchlist_id}/items")
def get_watchlist_items(watchlist_id: int, db: sqlite3.Connection = Depends(get_db)):
    query = """
    SELECT symbol, display_order, added_at
    FROM watchlist_items
    WHERE watchlist_id = ?
    ORDER BY display_order ASC, symbol ASC
    """
    cursor = db.execute(query, (watchlist_id,))
    return [dict(row) for row in cursor.fetchall()]

@app.post("/de/api/watchlists/{watchlist_id}/items")
def add_watchlist_item(watchlist_id: int, item: WatchlistItemAdd, db: sqlite3.Connection = Depends(get_db)):
    try:
        db.execute("INSERT INTO watchlist_items (watchlist_id, symbol) VALUES (?, ?)", (watchlist_id, item.symbol.upper()))
        db.commit()
        return {"status": "ok"}
    except sqlite3.IntegrityError:
        return {"status": "already_exists"}

@app.delete("/de/api/watchlists/{watchlist_id}/items/{symbol}")
def remove_watchlist_item(watchlist_id: int, symbol: str, db: sqlite3.Connection = Depends(get_db)):
    db.execute("DELETE FROM watchlist_items WHERE watchlist_id = ? AND symbol = ?", (watchlist_id, symbol.upper()))
    db.commit()
    return {"status": "ok"}

@app.get("/de/api/watchlists/supported-indices")
def get_supported_indices():
    return list(NSE_INDICES.keys())


@app.post("/de/api/watchlists/import-index")
def import_index_constituents(req: IndexImportRequest, conn: sqlite3.Connection = Depends(get_db)):
    """Import constituents for a major index into a dedicated watchlist."""
    cursor = conn.cursor()
    idx_name = req.index_name.strip().upper()

    csv_file = None
    for name, filename in NSE_INDICES.items():
        if name.upper() == idx_name:
            csv_file = filename
            idx_name = name
            break

    if not csv_file:
         raise HTTPException(
             status_code=400,
             detail=f"Index '{req.index_name}' not supported. Call /de/api/watchlists/supported-indices for list."
         )

    url = f"https://nsearchives.nseindia.com/content/indices/{csv_file}"

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=15)

        if response.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Failed to fetch from NSE: {response.status_code}")

        df = pd.read_csv(io.StringIO(response.text))
        df.columns = [c.strip().lower() for c in df.columns]

        if 'symbol' not in df.columns:
            raise HTTPException(status_code=500, detail="NSE CSV format changed: 'Symbol' column missing.")

        symbols = df['symbol'].dropna().astype(str).tolist()
        symbols = [s.strip().upper() for s in symbols]

        if not symbols:
            raise HTTPException(status_code=404, detail="No symbols found in index file.")

        cursor.execute("SELECT id FROM watchlists WHERE name = ?", (idx_name,))
        row = cursor.fetchone()
        if row:
            wl_id = row[0]
        else:
            cursor.execute("INSERT INTO watchlists (name, description) VALUES (?, ?)", (idx_name, f"Auto-imported index constituents for {idx_name}"))
            wl_id = cursor.lastrowid

        count = 0
        cursor.execute("SELECT MAX(display_order) FROM watchlist_items WHERE watchlist_id = ?", (wl_id,))
        max_order = cursor.fetchone()[0] or 0

        for i, sym in enumerate(symbols):
            try:
                cursor.execute("""
                    INSERT INTO watchlist_items (watchlist_id, symbol, display_order)
                    VALUES (?, ?, ?)
                """, (wl_id, sym, max_order + i + 1))
                count += 1
            except sqlite3.IntegrityError:
                continue

        conn.commit()
        return {"status": "success", "index": idx_name, "watchlist_id": wl_id, "imported": count, "total_constituents": len(symbols)}

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Internal Error: {str(e)}")
