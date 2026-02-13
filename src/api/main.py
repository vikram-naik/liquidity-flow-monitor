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
import threading
from src.scripts.intraday_worker import run_worker

app = FastAPI(title="LFM Data Ingestion API", docs_url="/lfm/api/docs", openapi_url="/lfm/api/openapi.json")

@app.on_event("startup")
def startup_event():
    # 1. Ensure database is initialized
    print("🚀 Initializing database...")
    init_db()
    
    # 2. Start Background Intraday Worker
    print("🚀 Starting Intraday Data Worker thread (via API)...")
    worker_thread = threading.Thread(target=run_worker, daemon=True)
    worker_thread.start()

# --- Models ---

class MarginUpload(BaseModel):
    timestamp: Optional[datetime] = None
    exchange: str
    symbol: str
    asset_class: Optional[str] = None
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

class TreasuryAuctionUpload(BaseModel):
    record_date: date
    auction_date: Optional[date] = None
    security_type: str
    maturity: str
    bid_to_cover: Optional[float] = None
    tail_bps: Optional[float] = None
    high_yield: Optional[float] = None
    offering_amount: Optional[float] = None
    total_accepted: Optional[float] = None
    primary_dealer_accepted: Optional[float] = None
    direct_bidder_accepted: Optional[float] = None
    indirect_bidder_accepted: Optional[float] = None
    soma_accepted: Optional[float] = None
    soma_maturing: Optional[float] = None
    noncomp_accepted: Optional[float] = None
    is_new_issuance: Optional[bool] = False

class TreasuryLiquidityUpload(BaseModel):
    record_date: date
    tga_balance: Optional[float] = None
    rrp_balance: Optional[float] = None
    cds_spread: Optional[float] = None

class TreasuryDebtProfileUpload(BaseModel):
    record_date: date
    maturing_1yr: Optional[float] = None
    maturing_5yr: Optional[float] = None
    total_debt: Optional[float] = None

class TreasuryBuybackUpload(BaseModel):
    record_date: date
    total_offered: float
    total_accepted: float
    security_type: str
    maturity_bucket: str

class TreasuryFlowUpload(BaseModel):
    record_date: date
    security_type: str
    transaction_type: str
    amount_mil: float

class TreasuryScheduleUpload(BaseModel):
    record_date: date
    maturity_date: date
    security_class: str
    amount_mil: float
    issue_date: Optional[date] = None

class TreasuryAvgRateUpload(BaseModel):
    record_date: date
    security_desc: str
    avg_interest_rate_amt: float

class TreasuryIssuancePlanUpload(BaseModel):
    auction_date: date
    security_term: str
    offering_amount: float
    is_new_issuance: bool

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
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def resolve_instrument(conn: sqlite3.Connection, exchange_name: str, symbol: str, asset_class: Optional[str] = None) -> int:
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
    cursor.execute("SELECT id, asset_class FROM instruments WHERE exchange_id = ? AND symbol = ?", (exch_id, symbol))
    inst_row = cursor.fetchone()
    if not inst_row:
        cursor.execute("INSERT INTO instruments (exchange_id, symbol, asset_class) VALUES (?, ?, ?)", (exch_id, symbol, asset_class))
        inst_id = cursor.lastrowid
    else:
        inst_id = inst_row['id']
        # Update asset_class if it's missing on remote but provided
        if asset_class and not inst_row['asset_class']:
            cursor.execute("UPDATE instruments SET asset_class = ? WHERE id = ?", (asset_class, inst_id))
    
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
            inst_id = resolve_instrument(conn, item.exchange, item.symbol, item.asset_class)
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

@app.post("/lfm/api/upload/treasury-auctions")
def upload_treasury_auctions(data: List[TreasuryAuctionUpload], conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    count = 0
    try:
        for item in data:
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_auctions 
                (record_date, auction_date, security_type, maturity, bid_to_cover, tail_bps, high_yield, offering_amount, total_accepted,
                 primary_dealer_accepted, direct_bidder_accepted, indirect_bidder_accepted, soma_accepted, soma_maturing, noncomp_accepted, is_new_issuance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (item.record_date, item.auction_date, item.security_type, item.maturity, item.bid_to_cover, item.tail_bps, item.high_yield, 
                 item.offering_amount, item.total_accepted, item.primary_dealer_accepted, item.direct_bidder_accepted,
                 item.indirect_bidder_accepted, item.soma_accepted, item.soma_maturing, item.noncomp_accepted, item.is_new_issuance))
            count += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success", "inserted": count}

@app.post("/lfm/api/upload/treasury-buybacks")
def upload_treasury_buybacks(data: List[TreasuryBuybackUpload], conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    count = 0
    try:
        for item in data:
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_buybacks 
                (record_date, total_offered, total_accepted, security_type, maturity_bucket)
                VALUES (?, ?, ?, ?, ?)
            """, (item.record_date, item.total_offered, item.total_accepted, item.security_type, item.maturity_bucket))
            count += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success", "inserted": count}

@app.post("/lfm/api/upload/treasury-flows")
def upload_treasury_flows(data: List[TreasuryFlowUpload], conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    count = 0
    try:
        for item in data:
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_daily_debt_flows 
                (record_date, security_type, transaction_type, amount_mil)
                VALUES (?, ?, ?, ?)
            """, (item.record_date, item.security_type, item.transaction_type, item.amount_mil))
            count += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success", "inserted": count}

@app.post("/lfm/api/upload/maturity-schedule")
def upload_maturity_schedule(data: List[TreasuryScheduleUpload], conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    count = 0
    try:
        for item in data:
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_maturity_schedule 
                (record_date, maturity_date, security_class, amount_mil, issue_date)
                VALUES (?, ?, ?, ?, ?)
            """, (item.record_date, item.maturity_date, item.security_class, item.amount_mil, item.issue_date))
            count += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success", "inserted": count}

@app.post("/lfm/api/upload/avg-rates")
def upload_avg_rates(data: List[TreasuryAvgRateUpload], conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    count = 0
    try:
        for item in data:
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_avg_interest_rates 
                (record_date, security_desc, avg_interest_rate_amt)
                VALUES (?, ?, ?)
            """, (item.record_date, item.security_desc, item.avg_interest_rate_amt))
            count += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success", "inserted": count}

@app.post("/lfm/api/upload/issuance-plan")
def upload_issuance_plan(data: List[TreasuryIssuancePlanUpload], conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    count = 0
    try:
        for item in data:
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_issuance_plan 
                (auction_date, security_term, offering_amount, is_new_issuance)
                VALUES (?, ?, ?, ?)
            """, (item.auction_date, item.security_term, item.offering_amount, item.is_new_issuance))
            count += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success", "inserted": count}

@app.post("/lfm/api/upload/treasury-liquidity")
def upload_treasury_liquidity(data: List[TreasuryLiquidityUpload], conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    count = 0
    try:
        for item in data:
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_liquidity (record_date, tga_balance, rrp_balance, cds_spread)
                VALUES (?, ?, ?, ?)
            """, (item.record_date, item.tga_balance, item.rrp_balance, item.cds_spread))
            count += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success", "inserted": count}

@app.post("/lfm/api/upload/treasury-debt")
def upload_treasury_debt(data: List[TreasuryDebtProfileUpload], conn: sqlite3.Connection = Depends(get_db)):
    cursor = conn.cursor()
    count = 0
    try:
        for item in data:
            cursor.execute("""
                INSERT OR REPLACE INTO treasury_debt_profile (record_date, maturing_1yr, maturing_5yr, total_debt)
                VALUES (?, ?, ?, ?)
            """, (item.record_date, item.maturing_1yr, item.maturing_5yr, item.total_debt))
            count += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success", "inserted": count}


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
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success", "inserted": count}


class SyncTimestamp(BaseModel):
    timestamp: str


def get_sync_file_path():
    """Get path to sync timestamp file, respecting data mount"""
    data_dir = os.path.dirname(os.getenv("DB_PATH", "liquidity_monitor.db"))
    if data_dir:
        return os.path.join(data_dir, ".last_sync.txt")
    return ".last_sync.txt"


@app.post("/lfm/api/sync-timestamp")
def update_sync_timestamp(data: SyncTimestamp):
    """Update the last sync timestamp for the dashboard"""
    try:
        sync_file = get_sync_file_path()
        with open(sync_file, 'w') as f:
            f.write(data.timestamp)
        return {"status": "success", "timestamp": data.timestamp, "path": sync_file}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/lfm/api/sync-timestamp")
def get_sync_timestamp():
    """Get the last sync timestamp"""
    try:
        sync_file = get_sync_file_path()
        if os.path.exists(sync_file):
            with open(sync_file, 'r') as f:
                return {"timestamp": f.read().strip()}
        return {"timestamp": "Never"}
    except:
        return {"timestamp": "Never"}


