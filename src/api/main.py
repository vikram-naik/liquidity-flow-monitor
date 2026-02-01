from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime, timezone, date
from typing import List, Optional
import sqlite3
import os
import sys

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

# Import DB path from analytics or database if possible
from src.database import DB_PATH, init_db

app = FastAPI(title="LFM Data Ingestion API", docs_url="/lfm/api/docs", openapi_url="/lfm/api/openapi.json")

@app.on_event("startup")
def startup_event():
    # Ensure database is initialized
    print("🚀 Initializing database...")
    init_db()

# --- Models ---

class MarginUpload(BaseModel):
    timestamp: Optional[datetime] = None
    exchange: str
    symbol: str
    margin_percent: float
    contract_price: float
    open_interest: int

class YieldUpload(BaseModel):
    timestamp: Optional[datetime] = None
    currency: str
    tenor: str
    rate: float

class HolidayUpload(BaseModel):
    exchange: str
    holiday_date: date
    holiday_name: str
    is_partial: bool = False
    notes: Optional[str] = None

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

def resolve_instrument(conn: sqlite3.Connection, exchange_name: str, symbol: str) -> int:
    cursor = conn.cursor()
    # 1. Resolve Exchange
    cursor.execute("SELECT id FROM exchanges WHERE name = ?", (exchange_name,))
    exch_row = cursor.fetchone()
    if not exch_row:
        # Create exchange if missing? For now, requirement is mostly focused on ingestion.
        # But let's be robust.
        cursor.execute("INSERT INTO exchanges (name) VALUES (?)", (exchange_name,))
        exch_id = cursor.lastrowid
    else:
        exch_id = exch_row['id']

    # 2. Resolve Instrument
    cursor.execute("SELECT id FROM instruments WHERE exchange_id = ? AND symbol = ?", (exch_id, symbol))
    inst_row = cursor.fetchone()
    if not inst_row:
        cursor.execute("INSERT INTO instruments (exchange_id, symbol) VALUES (?, ?)", (exch_id, symbol))
        inst_id = cursor.lastrowid
    else:
        inst_id = inst_row['id']
    
    return inst_id

# --- Routes ---

@app.get("/lfm/api/health")
def health_check():
    return {"status": "healthy", "service": "lfm-api"}

@app.post("/lfm/api/upload/margins")
def upload_margins(data: List[MarginUpload], conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    count = 0
    try:
        for item in data:
            inst_id = resolve_instrument(conn, item.exchange, item.symbol)
            ts = item.timestamp or datetime.now(timezone.utc)
            
            cursor.execute("""
                INSERT OR REPLACE INTO margin_logs (timestamp, instrument_id, margin_percent, contract_price, open_interest)
                VALUES (?, ?, ?, ?, ?)
            """, (ts, inst_id, item.margin_percent, item.contract_price, item.open_interest))
            count += 1
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    
    return {"status": "success", "inserted": count}

@app.post("/lfm/api/upload/yields")
def upload_yields(data: List[YieldUpload], conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    count = 0
    try:
        for item in data:
            ts = item.timestamp or datetime.now(timezone.utc)
            cursor.execute("""
                INSERT OR REPLACE INTO yield_logs (timestamp, currency, tenor, rate)
                VALUES (?, ?, ?, ?)
            """, (ts, item.currency, item.tenor, item.rate))
            count += 1
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    
    return {"status": "success", "inserted": count}

@app.post("/lfm/api/upload/holidays")
def upload_holidays(data: List[HolidayUpload], conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    count = 0
    try:
        for item in data:
            # Resolve Exchange ID
            cursor.execute("SELECT id FROM exchanges WHERE name = ?", (item.exchange,))
            exch_row = cursor.fetchone()
            if not exch_row:
                cursor.execute("INSERT INTO exchanges (name) VALUES (?)", (item.exchange,))
                exch_id = cursor.lastrowid
            else:
                exch_id = exch_row['id']

            cursor.execute("""
                INSERT OR REPLACE INTO trading_holidays (exchange_id, holiday_date, holiday_name, is_partial, notes)
                VALUES (?, ?, ?, ?, ?)
            """, (exch_id, item.holiday_date, item.holiday_name, item.is_partial, item.notes))
            count += 1
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    
    return {"status": "success", "inserted": count}
