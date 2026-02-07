#!/usr/bin/env python3
"""
One-time script to backfill historical US 5Y CDS data.
Data was extracted via browser subagent from WorldGovernmentBonds.com.
"""

import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "liquidity_monitor.db")

CDS_DATA = [
  {"Date": "2026-02-06", "Spread": 30.12},
  {"Date": "2026-02-04", "Spread": 30.13},
  {"Date": "2026-02-03", "Spread": 28.78},
  {"Date": "2026-02-02", "Spread": 28.33},
  {"Date": "2026-02-01", "Spread": 28.33},
  {"Date": "2026-01-30", "Spread": 28.33},
  {"Date": "2026-01-29", "Spread": 28.33},
  {"Date": "2026-01-28", "Spread": 28.33},
  {"Date": "2026-01-27", "Spread": 28.34},
  {"Date": "2026-01-26", "Spread": 28.34},
  {"Date": "2026-01-25", "Spread": 28.34},
  {"Date": "2026-01-24", "Spread": 28.34},
  {"Date": "2026-01-23", "Spread": 28.33},
  {"Date": "2026-01-21", "Spread": 26.99},
  {"Date": "2026-01-20", "Spread": 26.99},
  {"Date": "2026-01-19", "Spread": 26.09},
  {"Date": "2026-01-18", "Spread": 26.09},
  {"Date": "2026-01-17", "Spread": 26.09},
  {"Date": "2026-01-16", "Spread": 26.09},
  {"Date": "2026-01-15", "Spread": 26.09},
  {"Date": "2026-01-14", "Spread": 26.09},
  {"Date": "2026-01-13", "Spread": 26.54},
  {"Date": "2026-01-12", "Spread": 26.54},
  {"Date": "2026-01-11", "Spread": 26.54},
  {"Date": "2026-01-10", "Spread": 26.54},
  {"Date": "2026-01-09", "Spread": 26.54},
  {"Date": "2026-01-08", "Spread": 26.53},
  {"Date": "2026-01-07", "Spread": 26.54},
  {"Date": "2026-01-06", "Spread": 26.54},
  {"Date": "2026-01-05", "Spread": 26.54},
  {"Date": "2026-01-04", "Spread": 26.54},
  {"Date": "2026-01-03", "Spread": 26.54},
  {"Date": "2026-01-02", "Spread": 26.54},
  {"Date": "2026-01-01", "Spread": 26.54},
  {"Date": "2025-12-31", "Spread": 26.54},
  {"Date": "2025-12-30", "Spread": 26.99},
  {"Date": "2025-12-29", "Spread": 26.99},
  {"Date": "2025-12-27", "Spread": 26.99},
  {"Date": "2025-12-26", "Spread": 26.99},
  {"Date": "2025-12-25", "Spread": 26.99},
  {"Date": "2025-12-24", "Spread": 26.99},
  {"Date": "2025-12-23", "Spread": 26.99},
  {"Date": "2025-12-22", "Spread": 26.99},
  {"Date": "2025-12-21", "Spread": 26.99},
  {"Date": "2025-12-20", "Spread": 26.99},
  {"Date": "2025-12-19", "Spread": 27.44},
  {"Date": "2025-12-18", "Spread": 27.44},
  {"Date": "2025-12-17", "Spread": 27.43},
  {"Date": "2025-12-16", "Spread": 27.44},
  {"Date": "2025-12-15", "Spread": 27.88},
  {"Date": "2025-12-14", "Spread": 27.88},
  {"Date": "2025-12-13", "Spread": 27.88},
  {"Date": "2025-12-12", "Spread": 27.89},
  {"Date": "2025-12-11", "Spread": 27.88},
  {"Date": "2025-12-10", "Spread": 28.35},
  {"Date": "2025-12-09", "Spread": 28.79},
  {"Date": "2025-12-08", "Spread": 28.78},
  {"Date": "2025-12-07", "Spread": 28.78},
  {"Date": "2025-12-06", "Spread": 28.78},
  {"Date": "2025-12-05", "Spread": 29.23},
  {"Date": "2025-12-04", "Spread": 29.23},
  {"Date": "2025-12-03", "Spread": 29.23},
  {"Date": "2025-12-02", "Spread": 29.68},
  {"Date": "2025-12-01", "Spread": 30.13},
  {"Date": "2025-11-30", "Spread": 30.13},
  {"Date": "2025-11-29", "Spread": 30.13},
  {"Date": "2025-11-28", "Spread": 30.13},
  {"Date": "2025-11-27", "Spread": 30.57},
  {"Date": "2025-11-26", "Spread": 31.02},
  {"Date": "2025-11-25", "Spread": 31.02},
  {"Date": "2025-11-24", "Spread": 31.93},
  {"Date": "2025-11-23", "Spread": 31.93},
  {"Date": "2025-11-22", "Spread": 31.93},
  {"Date": "2025-11-21", "Spread": 32.37},
  {"Date": "2025-11-20", "Spread": 32.37},
  {"Date": "2025-11-19", "Spread": 32.37},
  {"Date": "2025-11-18", "Spread": 32.82},
  {"Date": "2025-11-17", "Spread": 33.26},
  {"Date": "2025-11-16", "Spread": 33.26},
  {"Date": "2025-11-15", "Spread": 33.26},
  {"Date": "2025-11-14", "Spread": 33.26},
  {"Date": "2025-11-13", "Spread": 33.71},
  {"Date": "2025-11-12", "Spread": 33.71},
  {"Date": "2025-11-11", "Spread": 35.06},
  {"Date": "2025-11-10", "Spread": 35.06},
  {"Date": "2025-11-09", "Spread": 35.06},
  {"Date": "2025-11-08", "Spread": 35.06},
  {"Date": "2025-11-07", "Spread": 35.06},
  {"Date": "2025-11-06", "Spread": 35.06},
  {"Date": "2025-11-04", "Spread": 35.05},
  {"Date": "2025-11-03", "Spread": 35.96},
  {"Date": "2025-11-02", "Spread": 35.96},
  {"Date": "2025-11-01", "Spread": 35.96},
  {"Date": "2025-10-31", "Spread": 35.96},
  {"Date": "2025-10-30", "Spread": 36.40},
  {"Date": "2025-10-29", "Spread": 36.85},
  {"Date": "2025-10-28", "Spread": 36.87},
  {"Date": "2025-10-27", "Spread": 36.86},
  {"Date": "2025-10-26", "Spread": 36.86},
  {"Date": "2025-10-25", "Spread": 36.86},
  {"Date": "2025-10-24", "Spread": 36.85},
  {"Date": "2025-10-23", "Spread": 36.85},
  {"Date": "2025-10-22", "Spread": 36.86},
  {"Date": "2025-10-21", "Spread": 36.85},
  {"Date": "2025-10-20", "Spread": 36.84},
  {"Date": "2025-10-19", "Spread": 36.84},
  {"Date": "2025-10-18", "Spread": 36.84},
  {"Date": "2025-10-17", "Spread": 36.85},
  {"Date": "2025-10-16", "Spread": 37.30},
  {"Date": "2025-10-15", "Spread": 37.29},
  {"Date": "2025-10-14", "Spread": 37.29},
  {"Date": "2025-10-13", "Spread": 37.31},
  {"Date": "2025-10-12", "Spread": 37.31},
  {"Date": "2025-10-11", "Spread": 37.31},
  {"Date": "2025-10-10", "Spread": 37.29},
  {"Date": "2025-10-09", "Spread": 37.30},
  {"Date": "2025-10-08", "Spread": 37.31},
  {"Date": "2025-10-07", "Spread": 37.30},
  {"Date": "2025-10-06", "Spread": 37.30},
  {"Date": "2025-10-05", "Spread": 37.30},
  {"Date": "2025-10-04", "Spread": 37.30},
  {"Date": "2025-10-03", "Spread": 37.30},
  {"Date": "2025-10-02", "Spread": 37.30},
  {"Date": "2025-10-01", "Spread": 36.86},
  {"Date": "2025-09-30", "Spread": 36.86},
  {"Date": "2025-09-29", "Spread": 36.88},
  {"Date": "2025-09-28", "Spread": 36.88},
  {"Date": "2025-09-27", "Spread": 36.88},
  {"Date": "2025-09-26", "Spread": 36.87},
  {"Date": "2025-09-25", "Spread": 36.87},
  {"Date": "2025-09-24", "Spread": 36.87},
  {"Date": "2025-09-23", "Spread": 35.97},
  {"Date": "2025-09-22", "Spread": 35.97},
  {"Date": "2025-09-21", "Spread": 35.97},
  {"Date": "2025-09-20", "Spread": 35.97},
  {"Date": "2025-09-19", "Spread": 35.97},
  {"Date": "2025-09-18", "Spread": 35.96},
  {"Date": "2025-09-17", "Spread": 35.97},
  {"Date": "2025-09-16", "Spread": 35.97},
  {"Date": "2025-09-15", "Spread": 35.97},
  {"Date": "2025-09-14", "Spread": 35.97},
  {"Date": "2025-09-13", "Spread": 35.97},
  {"Date": "2025-09-12", "Spread": 35.96},
  {"Date": "2025-09-11", "Spread": 35.52},
  {"Date": "2025-09-10", "Spread": 35.52},
  {"Date": "2025-09-09", "Spread": 35.51},
  {"Date": "2025-09-08", "Spread": 35.53},
  {"Date": "2025-09-07", "Spread": 35.53},
  {"Date": "2025-09-06", "Spread": 35.53},
  {"Date": "2025-09-05", "Spread": 35.53},
  {"Date": "2025-09-04", "Spread": 35.53},
  {"Date": "2025-09-03", "Spread": 35.52},
  {"Date": "2025-09-02", "Spread": 35.52},
  {"Date": "2025-09-01", "Spread": 35.51},
  {"Date": "2025-08-31", "Spread": 35.51},
  {"Date": "2025-08-30", "Spread": 35.51},
  {"Date": "2025-08-29", "Spread": 35.50},
  {"Date": "2025-08-28", "Spread": 35.50},
  {"Date": "2025-08-27", "Spread": 35.52},
  {"Date": "2025-08-26", "Spread": 35.51},
  {"Date": "2025-08-25", "Spread": 35.53},
  {"Date": "2025-08-24", "Spread": 35.53},
  {"Date": "2025-08-23", "Spread": 35.53},
  {"Date": "2025-08-22", "Spread": 35.96},
  {"Date": "2025-08-21", "Spread": 35.95},
  {"Date": "2025-08-20", "Spread": 35.96},
  {"Date": "2025-08-19", "Spread": 35.97},
  {"Date": "2025-08-18", "Spread": 35.96},
  {"Date": "2025-08-17", "Spread": 35.96},
  {"Date": "2025-08-16", "Spread": 35.96},
  {"Date": "2025-08-15", "Spread": 35.94},
  {"Date": "2025-08-14", "Spread": 35.96},
  {"Date": "2025-08-13", "Spread": 35.96},
  {"Date": "2025-08-12", "Spread": 35.97},
  {"Date": "2025-08-11", "Spread": 35.96},
  {"Date": "2025-08-10", "Spread": 35.96},
  {"Date": "2025-08-09", "Spread": 35.96},
  {"Date": "2025-08-08", "Spread": 35.96},
  {"Date": "2025-08-07", "Spread": 35.96},
  {"Date": "2025-08-06", "Spread": 35.96}
]

def main():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        return
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print(f"Backfilling {len(CDS_DATA)} CDS records...")
    
    for item in CDS_DATA:
        date = item['Date']
        spread = item['Spread']
        
        # Use INSERT ... ON CONFLICT to update cds_spread without losing TGA/RRP data
        # Note: In SQLite, ON CONFLICT(record_date) works because record_date is PRIMARY KEY.
        cursor.execute('''
            INSERT INTO treasury_liquidity (record_date, cds_spread)
            VALUES (?, ?)
            ON CONFLICT(record_date) DO UPDATE SET 
                cds_spread = excluded.cds_spread
        ''', (date, spread))
    
    conn.commit()
    conn.close()
    print("Backfill complete.")

if __name__ == "__main__":
    main()
