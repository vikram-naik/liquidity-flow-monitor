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
    # Use environment variable if provided (for Docker)
    db_path = os.getenv("DB_PATH", DB_PATH)
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=20)
    conn.row_factory = sqlite3.Row
    # Enable Foreign Keys and WAL mode for every connection
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
        
        # Get the latest date from the last row of the ledger
        # Note: result.ledger can be a DataFrame or list; len() is safe for both.
        last_date = None
        if result.ledger is not None and len(result.ledger) > 0:
            # If it's a DataFrame, use .iloc[-1].date, if it's a list/namedtuple, use [-1].date
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
    """
    Serve the main chart UI for a given symbol.
    The HTML page loads data via fetch() from /de/api/divergence-engine/{symbol}.
    """
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

# --- State Rules Config Routes ---

@app.get("/de/api/config/state-rules")
def get_state_rules(db: sqlite3.Connection = Depends(get_db)):
    """Return current state classification config (defaults + user overrides)."""
    from src.divergence_engine import config_manager
    config = config_manager.get_config(db)
    defaults = config_manager.load_defaults()
    return {
        "thresholds": config["thresholds"],
        "defaults": defaults["thresholds"],
        "rules_count": len(config.get("rules", [])),
    }


class ThresholdUpdate(BaseModel):
    thresholds: dict


@app.put("/de/api/config/state-rules")
def update_state_rules(body: ThresholdUpdate, db: sqlite3.Connection = Depends(get_db)):
    """Save user threshold overrides. Only stores values that differ from defaults."""
    from src.divergence_engine import config_manager
    config = config_manager.save_user_config(db, body.thresholds)
    cache.clear()  # Invalidate analysis cache so next load uses new thresholds
    return {"status": "saved", "thresholds": config["thresholds"]}


@app.post("/de/api/config/state-rules/reset")
def reset_state_rules(db: sqlite3.Connection = Depends(get_db)):
    """Factory reset — delete all user overrides and return defaults."""
    from src.divergence_engine import config_manager
    config = config_manager.factory_reset(db)
    cache.clear()  # Invalidate analysis cache
    return {"status": "reset", "thresholds": config["thresholds"]}


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

# --- Signal Quality Routes ---

@app.get("/de/signal-quality")
def signal_quality_page():
    """Serve the Signal Quality Explorer UI."""
    html_path = os.path.join(_WEB_DIR, "signal_quality.html")
    if not os.path.isfile(html_path):
        raise HTTPException(status_code=404, detail="Signal Quality page not found")
    return FileResponse(html_path, media_type="text/html")


@app.get("/de/api/signal-quality")
def signal_quality_data(
    run_date: Optional[str] = Query(None, description="Filter by run date (YYYY-MM-DD)"),
    signal_type: Optional[str] = Query(None, description="Demand or Supply"),
    volume_tier: Optional[str] = Query(None, description="Large, Mid, Small, Micro"),
    hit_horizon: Optional[str] = Query(None, description="Filter by hit/miss: hit_3d, hit_5d, hit_10d"),
    hit_value: Optional[int] = Query(None, description="0=miss, 1=hit"),
    limit: int = Query(10000, description="Max rows to return"),
    offset: int = Query(0, description="Offset for pagination"),
    db: sqlite3.Connection = Depends(get_db),
):
    """Return signal quality data for the AG Grid explorer."""
    import math

    # Build cache key from all params
    cache_key = f"sq:data:{run_date}:{signal_type}:{volume_tier}:{hit_horizon}:{hit_value}:{limit}:{offset}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Get available runs
    runs = [
        dict(r) for r in db.execute(
            "SELECT DISTINCT run_date, thresholds_hash, COUNT(*) as signal_count "
            "FROM signal_quality GROUP BY run_date, thresholds_hash "
            "ORDER BY run_date DESC"
        ).fetchall()
    ]

    if not runs:
        return {"runs": [], "signals": [], "summary": {}, "total": 0}

    # Use latest run if not specified
    target_run = run_date or runs[0]["run_date"]
    target_hash = None
    for r in runs:
        if r["run_date"] == target_run:
            target_hash = r["thresholds_hash"]
            break
    if not target_hash:
        target_run = runs[0]["run_date"]
        target_hash = runs[0]["thresholds_hash"]

    # Build query with optional filters
    where = "WHERE run_date = ? AND thresholds_hash = ?"
    params: list = [target_run, target_hash]

    if signal_type:
        where += " AND signal_type = ?"
        params.append(signal_type)
    if volume_tier:
        where += " AND volume_tier = ?"
        params.append(volume_tier)
    if hit_horizon and hit_value is not None and hit_horizon in ("hit_3d", "hit_5d", "hit_10d"):
        where += f" AND {hit_horizon} = ?"
        params.append(hit_value)

    # Total count for this filter
    total = db.execute(
        f"SELECT COUNT(*) FROM signal_quality {where}", params
    ).fetchone()[0]

    # Summary via SQL aggregation (fast for large tables)
    summary = _compute_summary_sql(db, where, params)

    # Paginated data
    rows = db.execute(
        f"SELECT * FROM signal_quality {where} ORDER BY signal_date DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    def _clean_row(row):
        d = dict(row)
        for k, v in d.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                d[k] = None
        return d

    signals = [_clean_row(r) for r in rows]

    result = {
        "runs": runs,
        "current_run": target_run,
        "current_hash": target_hash,
        "signal_count": len(signals),
        "total": total,
        "signals": signals,
        "summary": summary,
    }

    # Cache indefinitely (no TTL) — data is static until next report run
    cache.set(cache_key, result, ttl=0)

    return result


def _compute_summary_sql(db, where: str, params: list) -> dict:
    """Compute hit rate summary via SQL aggregation."""
    def _safe_round(v, d=2):
        import math
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            return 0.0
        return round(v, d)

    # By signal_type
    rows = db.execute(f"""
        SELECT signal_type AS grp,
            AVG(CASE WHEN hit_3d IS NOT NULL THEN hit_3d END) AS hr3,
            AVG(CASE WHEN hit_5d IS NOT NULL THEN hit_5d END) AS hr5,
            AVG(CASE WHEN hit_10d IS NOT NULL THEN hit_10d END) AS hr10,
            AVG(CASE WHEN ret_3d IS NOT NULL AND ABS(ret_3d) < 1000 THEN ret_3d END) AS ar3,
            AVG(CASE WHEN ret_5d IS NOT NULL AND ABS(ret_5d) < 1000 THEN ret_5d END) AS ar5,
            AVG(CASE WHEN ret_10d IS NOT NULL AND ABS(ret_10d) < 1000 THEN ret_10d END) AS ar10,
            SUM(CASE WHEN hit_3d IS NOT NULL THEN 1 ELSE 0 END) AS n3,
            SUM(CASE WHEN hit_5d IS NOT NULL THEN 1 ELSE 0 END) AS n5,
            SUM(CASE WHEN hit_10d IS NOT NULL THEN 1 ELSE 0 END) AS n10
        FROM signal_quality {where}
        GROUP BY signal_type
    """, params).fetchall()

    result = {}
    def _parse_group(rows_list):
        out = {}
        for r in rows_list:
            d = dict(r)
            grp = d["grp"]
            if not grp:
                continue
            out[grp] = {}
            for h, hr_key, ar_key, n_key in [(3,"hr3","ar3","n3"),(5,"hr5","ar5","n5"),(10,"hr10","ar10","n10")]:
                if d[n_key] and d[n_key] > 0:
                    out[grp][h] = {
                        "hit_rate": _safe_round((d[hr_key] or 0) * 100, 1),
                        "avg_ret": _safe_round(d[ar_key], 2),
                        "n": int(d[n_key]),
                    }
        return out

    result = _parse_group(rows)

    # By tier
    tier_rows = db.execute(f"""
        SELECT volume_tier AS grp,
            AVG(CASE WHEN hit_3d IS NOT NULL THEN hit_3d END) AS hr3,
            AVG(CASE WHEN hit_5d IS NOT NULL THEN hit_5d END) AS hr5,
            AVG(CASE WHEN hit_10d IS NOT NULL THEN hit_10d END) AS hr10,
            AVG(CASE WHEN ret_3d IS NOT NULL AND ABS(ret_3d) < 1000 THEN ret_3d END) AS ar3,
            AVG(CASE WHEN ret_5d IS NOT NULL AND ABS(ret_5d) < 1000 THEN ret_5d END) AS ar5,
            AVG(CASE WHEN ret_10d IS NOT NULL AND ABS(ret_10d) < 1000 THEN ret_10d END) AS ar10,
            SUM(CASE WHEN hit_3d IS NOT NULL THEN 1 ELSE 0 END) AS n3,
            SUM(CASE WHEN hit_5d IS NOT NULL THEN 1 ELSE 0 END) AS n5,
            SUM(CASE WHEN hit_10d IS NOT NULL THEN 1 ELSE 0 END) AS n10
        FROM signal_quality {where}
        GROUP BY volume_tier
    """, params).fetchall()

    result["by_tier"] = _parse_group(tier_rows)

    return result


@app.post("/de/api/watchlists/import-index")
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
             detail=f"Index '{req.index_name}' not supported. Call /de/api/watchlists/supported-indices for list."
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
