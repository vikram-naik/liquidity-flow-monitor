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
from src.analysis.data import get_stock_data, load_stock_list
from src.analysis.ledger import get_or_create_anchor
from src.cache import get_cache

cache = get_cache()


app = FastAPI(title="LFM Data Ingestion API", docs_url="/lfm/api/docs", openapi_url="/lfm/api/openapi.json")

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



# --- Watchlist Models ---

class WatchlistCreate(BaseModel):
    name: str
    description: Optional[str] = ""

class WatchlistUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

class WatchlistResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    created_at: str

class WatchlistItemAdd(BaseModel):
    symbol: str

class WatchlistItemResponse(BaseModel):
    id: int
    symbol: str
    display_order: Optional[int] = 0
    added_at: str

class IndexImportRequest(BaseModel):
    index_name: str  # e.g., "NIFTY 50", "BANKNIFTY"



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

class ManualAnchorRequest(BaseModel):
    date: str # YYYY-MM-DD string

# --- Database Helpers ---

def get_db():
    # Use environment variable if provided (for Docker)
    db_path = os.getenv("DB_PATH", DB_PATH)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()



# --- Routes ---

@app.get("/lfm/api/health")
def health_check():
    return {"status": "healthy", "service": "lfm-api"}




@app.post("/lfm/api/upload/nse-delivery")
def upload_nse_delivery(data: List[NSEUpload], conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    count = 0
    try:
        for item in data:
            cursor.execute("""
                INSERT OR REPLACE INTO nse_delivery_log 
                (record_date, symbol, price_close, volume_total, delivery_qty, delivery_pct, 
                 price_change_pct, volume_change_pct, delivery_change_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (item.record_date, item.symbol, item.price_close, item.volume_total, item.delivery_qty, item.delivery_pct,
                 item.price_change_pct, item.volume_change_pct, item.delivery_change_pct))
            count += 1
        conn.commit()
        # Invalidate cache for uploaded symbols
        for item in data:
            cache_key = f"lfm:raw_data:{item.symbol.upper()}"
            cache.delete(cache_key)
            print(f"DEBUG: Invalidated cache for {item.symbol.upper()}")
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success", "inserted": count}


# ═══════════════════════════════════════════════════════════════════════════
#  WATCHLIST ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/lfm/api/watchlists", response_model=List[WatchlistResponse])
def get_watchlists(conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, description, created_at FROM watchlists ORDER BY created_at DESC")
    rows = cursor.fetchall()
    return [dict(row) for row in rows]

@app.post("/lfm/api/watchlists", response_model=WatchlistResponse)
def create_watchlist(watchlist: WatchlistCreate, conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO watchlists (name, description) VALUES (?, ?)", (watchlist.name, watchlist.description))
        conn.commit()
        wl_id = cursor.lastrowid
        cursor.execute("SELECT id, name, description, created_at FROM watchlists WHERE id = ?", (wl_id,))
        row = cursor.fetchone()
        return dict(row)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Watchlist with this name already exists")
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/lfm/api/watchlists/{id}")
def delete_watchlist(id: int, conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watchlists WHERE id = ?", (id,))
    conn.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return {"status": "success", "message": "Watchlist deleted"}

@app.patch("/lfm/api/watchlists/{id}", response_model=WatchlistResponse)
def update_watchlist(id: int, watchlist: WatchlistUpdate, conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    try:
        if watchlist.name:
            cursor.execute("UPDATE watchlists SET name = ? WHERE id = ?", (watchlist.name, id))
        if watchlist.description is not None:
            cursor.execute("UPDATE watchlists SET description = ? WHERE id = ?", (watchlist.description, id))
        conn.commit()
        
        cursor.execute("SELECT id, name, description, created_at FROM watchlists WHERE id = ?", (id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Watchlist not found")
        return dict(row)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Watchlist with this name already exists")
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/lfm/api/watchlists/{id}/items", response_model=List[WatchlistItemResponse])
def get_watchlist_items(id: int, conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    # verify list exists
    cursor.execute("SELECT id FROM watchlists WHERE id = ?", (id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Watchlist not found")
        
    cursor.execute("""
        SELECT id, symbol, display_order, added_at 
        FROM watchlist_items 
        WHERE watchlist_id = ? 
        ORDER BY display_order ASC, added_at DESC
    """, (id,))
    rows = cursor.fetchall()
    return [dict(row) for row in rows]

@app.post("/lfm/api/watchlists/{id}/items", response_model=WatchlistItemResponse)
def add_watchlist_item(id: int, item: WatchlistItemAdd, conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    try:
        # Check if list exists
        cursor.execute("SELECT id FROM watchlists WHERE id = ?", (id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Watchlist not found")

        symbol = item.symbol.upper()
        # Default order = max + 1
        cursor.execute("SELECT MAX(display_order) FROM watchlist_items WHERE watchlist_id = ?", (id,))
        max_order = cursor.fetchone()[0]
        new_order = (max_order or 0) + 1
        
        cursor.execute("""
            INSERT INTO watchlist_items (watchlist_id, symbol, display_order) 
            VALUES (?, ?, ?)
        """, (id, symbol, new_order))
        conn.commit()
        
        item_id = cursor.lastrowid
        cursor.execute("SELECT id, symbol, display_order, added_at FROM watchlist_items WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        return dict(row)
    except sqlite3.IntegrityError:
        # Symbol already in list - return existing
        cursor.execute("SELECT id, symbol, display_order, added_at FROM watchlist_items WHERE watchlist_id = ? AND symbol = ?", (id, symbol))
        row = cursor.fetchone()
        return dict(row)
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/lfm/api/watchlists/{id}/items/{symbol}")
def remove_watchlist_item(id: int, symbol: str, conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watchlist_items WHERE watchlist_id = ? AND symbol = ?", (id, symbol.upper()))
    conn.commit()
    return {"status": "success", "message": f"Removed {symbol}"}

# Supported NSE Indices mapping to their CSV filenames
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
}

@app.get("/lfm/api/watchlists/supported-indices")
def get_supported_indices():
    """Returns a list of supported indices for import."""
    return sorted(list(NSE_INDICES.keys()))

@app.post("/lfm/api/watchlists/import-index")
def import_index_constituents(req: IndexImportRequest, conn: sqlite3.Connection = Depends(get_db)):
    """
    Import constituents for a major index into a dedicated watchlist by fetching 
    the current CSV list directly from NSE archives.
    """
    cursor = conn.cursor()
    idx_name = req.index_name.strip().upper()
    
    csv_file = None
    # Flexible match (e.g. "Nifty 50" -> "NIFTY 50")
    for name, filename in NSE_INDICES.items():
        if name.upper() == idx_name:
            csv_file = filename
            idx_name = name # Use canonical name
            break
            
    if not csv_file:
         raise HTTPException(
             status_code=400, 
             detail=f"Index '{req.index_name}' not supported. Call /lfm/api/watchlists/supported-indices for list."
         )

    url = f"https://nsearchives.nseindia.com/content/indices/{csv_file}"
    print(f"[API] Fetching index constituents from {url}...")
    
    try:
        # NSE requires a User-Agent or it returns 403
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Failed to fetch from NSE: {response.status_code}")
            
        # Parse CSV
        df = pd.read_csv(io.StringIO(response.text))
        
        # Clean column names (strip whitespace and upper case)
        df.columns = [c.strip().lower() for c in df.columns]
        
        if 'symbol' not in df.columns:
            raise HTTPException(status_code=500, detail="NSE CSV format changed: 'Symbol' column missing.")
            
        symbols = df['symbol'].dropna().astype(str).tolist()
        symbols = [s.strip().upper() for s in symbols]
        
        if not symbols:
            raise HTTPException(status_code=404, detail="No symbols found in index file.")

        # 1. Get or Create Watchlist
        cursor.execute("SELECT id FROM watchlists WHERE name = ?", (idx_name,))
        row = cursor.fetchone()
        if row:
            wl_id = row[0]
        else:
            cursor.execute("INSERT INTO watchlists (name, description) VALUES (?, ?)", (idx_name, f"Auto-imported index constituents for {idx_name}"))
            wl_id = cursor.lastrowid

        # 2. Bulk Insert (Ignore duplicates)
        count = 0
        # Get current max order
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
                continue # Already in list
        
        conn.commit()
        return {"status": "success", "index": idx_name, "watchlist_id": wl_id, "imported": count, "total_constituents": len(symbols)}
        
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        print(f"[API] Error importing index: {e}")
        raise HTTPException(status_code=500, detail=f"Internal Error: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════
#  ANALYSIS ENDPOINTS (Consumption — powers the Lightweight Charts dashboard)
# ═══════════════════════════════════════════════════════════════════════════

@app.get('/lfm/dashboard')
def serve_dashboard():
    """Serve the Single Stock Analysis dashboard."""
    html_path = os.path.join(_WEB_DIR, 'dashboard.html')
    if not os.path.isfile(html_path):
        raise HTTPException(status_code=404, detail='Dashboard not found')
    return FileResponse(html_path, media_type='text/html')

@app.get('/lfm/api/analysis/stocks')
def list_stocks():
    """Return all available stock symbols."""
    return {'symbols': load_stock_list()}

@app.post('/lfm/api/analysis/stock/{symbol}/anchor/manual')
def set_manual_anchor(symbol: str, req: ManualAnchorRequest):
    """Manually set the Day Zero anchor by specifying a target month."""
    # Fetch full history (returns df, anchor, volume_profile)
    df, _, _ = get_stock_data(symbol, lookback_days=0)
    
    if df.empty:
        raise HTTPException(status_code=404, detail="No data found for symbol")
    
    # ensure index is datetime for calculation
    if 'record_date' in df.columns:
        df = df.set_index('record_date')

    # Force recalculation with manual date
    # The date provided should be used to identify the target month
    anchor = get_or_create_anchor(symbol, df, manual_date=req.date)
    return anchor

@app.post('/lfm/api/analysis/stock/{symbol}/anchor/auto')
def set_auto_anchor(symbol: str):
    """Force the system to recalculate the auto-anchor, dropping any manual override."""
    df, _, _ = get_stock_data(symbol, lookback_days=0)
    
    if df.empty:
        raise HTTPException(status_code=404, detail="No data found for symbol")
    
    if 'record_date' in df.columns:
        df = df.set_index('record_date')

    anchor = get_or_create_anchor(symbol, df, force_new=True)
    return anchor


@app.get('/lfm/api/analysis/stock/{symbol}')
def get_stock_analysis(
    symbol: str,
    lookback: int = Query(365, ge=0, description='Lookback in days (0 = all)'),
    agg: str = Query('daily', description='Aggregation: daily, weekly, monthly'),
):
    """Simple OHLC + Delivery data for a single stock."""
    df, anchor, volume_profile = get_stock_data(
        symbol=symbol.upper(),
        lookback_days=lookback,
        agg_period=agg,
    )
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    # Build response
    latest = df.iloc[-1]
    prev_close = df.iloc[-2]["price_close"] if len(df) > 1 else latest["price_close"]
    price_chg = (latest["price_close"] - prev_close) / prev_close * 100

    def _safe(v):
        import numpy as np
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return None if np.isnan(v) else round(float(v), 2)
        if pd.isna(v):
            return None
        return v

    meta = {
        "symbol": symbol.upper(),
        "last_price": _safe(latest.get("price_close")),
        "price_change_pct": round(float(price_chg), 2) if not pd.isna(price_chg) else 0.0,
        "data_points": len(df),
        "anchor_date": anchor['anchor_date'] if anchor else None,
        "anchor_type": anchor['anchor_type'] if anchor else None,
        "anchor_status": "CONFIRMED" if anchor else "MISSING",
        "ledger_angle": _safe(latest.get("ledger_angle")),
        "mcs_angle": _safe(latest.get("mcs_angle")),
        "ledger_velocity": _safe(latest.get("ledger_velocity"))
    }

    candles = []
    volumes = []
    ledger = []
    ledger_cumulative = []
    davwap = []
    mcs_data = []

    for _, r in df.iterrows():
        t = r["display_date_iso"]
        candles.append({
            "time": t,
            "open": _safe(r["price_open"]),
            "high": _safe(r["price_high"]),
            "low": _safe(r["price_low"]),
            "close": _safe(r["price_close"]),
            "is_coil": bool(r.get("is_coil", False)),
            "coil_score": _safe(r.get("coil_score", 0)),
            "is_ignition": bool(r.get("is_ignition", False)),
            "ignition_score": _safe(r.get("ignition_score", 0)),
        })
        
        # Color coding for volume bars based on MFM (as per PDF)
        # Green = Buyers dominant (MFM > 0), Red = Sellers dominant (MFM < 0)
        mfm = r.get("mfm_agg", 0)
        vol_color = "rgba(0, 227, 150, 0.45)" if mfm > 0 else "rgba(255, 73, 118, 0.45)"
        
        volumes.append({
            "time": t,
            "value": _safe(r["delivery_qty"]), # Primary focus is delivery qty
            "total_volume": _safe(r["volume_total"]),
            "color": vol_color,
        })
        
        if not pd.isna(r.get("dvl")):
            ledger.append({
                "time": t,
                "value": _safe(r.get("dvl")),
            })
        
        if not pd.isna(r.get("davwap")):
            davwap.append({
                "time": t,
                "value": _safe(r.get("davwap")),
            })
            
        if not pd.isna(r.get("dvl_cumulative")):
            ledger_cumulative.append({
                "time": t,
                "value": _safe(r.get("dvl_cumulative")),
            })
            
        if not pd.isna(r.get("mcs")):
            mcs_val = _safe(r.get("mcs"))
            if mcs_val is not None:
                mcs_color = "rgba(0, 227, 150, 0.8)" if mcs_val >= 0 else "rgba(255, 73, 118, 0.8)"
                mcs_data.append({
                    "time": t,
                    "value": mcs_val,
                    "color": mcs_color
                })

    return {
        "meta": meta,
        "candles": candles,
        "volumes": volumes,
        "ledger": ledger,
        "ledger_cumulative": ledger_cumulative,
        "davwap": davwap,
        "mcs": mcs_data,
        "volume_profile": volume_profile
    }
