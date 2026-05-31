"""
Scratch Verification Script: Gate & Guard End-to-End Pipeline.
Runs the DVL engine for 3MINDIA, prints the calculated Gate indicators, 
executes the Guard Forensic audit, and verifies database insertion.
"""

from __future__ import annotations

import os
import sys
import sqlite3
import json
import asyncio
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.divergence_engine.engine import DivergenceEngine
from scripts.guard_orchestrator import GuardOrchestrator
from src.database import DB_PATH

async def verify_pipeline():
    print("==================================================================")
    print("1. RUNNING QUANTITATIVE GATE ON 3MINDIA...")
    print("==================================================================")
    
    # Initialize the engine for 3MINDIA. It automatically loads all historical OHLC+delivery
    engine = DivergenceEngine("3MINDIA")
    result = engine.run()
    df = result.ledger
    
    print(f"Data shape loaded & processed: {df.shape}")
    latest = result.latest
    
    print("\nLATEST GATE Telemetry on 3MINDIA:")
    print(f"  Date:                 {latest.get('date')}")
    print(f"  Close Price:          {latest.get('close')}")
    print(f"  Delivery %:           {latest.get('delivery_pct')}%")
    print(f"  CWVAP:                {latest.get('cwvap')}")
    print(f"  Z-Score (5-Day):      {df['z_5'].iloc[-1]:.4f}")
    print(f"  Z-Score (20-Day):     {df['z_20'].iloc[-1]:.4f}")
    print(f"  Z-Score (252-Day):    {df['z_252'].iloc[-1]:.4f}")
    print(f"  Composite S_total:    {latest.get('s_total'):.2f}")
    print(f"  Classified Setup:     {latest.get('gate_setup')}")
    print(f"  Gate Signal Fired:    {latest.get('gate_signal')}")
    
    print("\n==================================================================")
    print("2. EXECUTING LLM FORENSIC AUDIT (THE GUARD)...")
    print("==================================================================")
    
    # Construct a candidate dict for the Guard
    candidate = {
        "symbol": "3MINDIA",
        "date": latest.get("date")[:10],
        "price": latest.get("close"),
        "gate_score": latest.get("s_total"),
        "setup_tag": "SIAB" # Simulate SIAB setup audit
    }
    
    orchestrator = GuardOrchestrator()
    
    # Run the forensic audit (runs mock simulation or live API call based on GEMINI_API_KEY)
    audit_result = await orchestrator.execute_forensic_audit(candidate)
    
    if not audit_result:
        print("ERROR: Forensic audit returned None!")
        return
        
    print("\nLLM GUARD OUTCOME:")
    print(f"  Verdict:              {audit_result.get('verdict')}")
    print(f"  Catalyst Type:        {audit_result.get('catalyst_type')}")
    print(f"  Fundamental Grade:    {audit_result.get('fundamental_grade')}")
    print(f"  Governance Risk:      {audit_result.get('governance_risk')}")
    print(f"  Qualitative Score:    {audit_result.get('qualitative_score')}")
    print(f"  Red Flags Identified: {audit_result.get('veto_reasons')}")
    print(f"  Citations:            {audit_result.get('evidence_citations')}")
    
    print("\n==================================================================")
    print("3. PERSISTING TO DATABASE & VERIFYING TABLE COHERENCE...")
    print("==================================================================")
    
    # Save the result
    orchestrator.save_result(candidate, audit_result)
    
    # Query database to verify insertion
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM gate_guard_signals WHERE symbol = '3MINDIA'").fetchone()
    conn.close()
    
    if row:
        print("SUCCESS! Data successfully written to gate_guard_signals table:")
        print(f"  DB Symbol:            {row['symbol']}")
        print(f"  DB Date:              {row['date']}")
        print(f"  DB Gate Score:        {row['gate_score']:.2f}")
        print(f"  DB Setup Tag:         {row['setup_tag']}")
        print(f"  DB Verdict:           {row['verdict']}")
        print(f"  DB Catalyst Type:     {row['catalyst_type']}")
        print(f"  DB Qualitative Score: {row['qualitative_score']:.2f}")
        print(f"  DB Red Flags:         {row['red_flags']}")
    else:
        print("ERROR: Symbol 3MINDIA was not found in gate_guard_signals table!")
        
    print("\n==================================================================")
    print("Verification Completed Successfully.")
    print("==================================================================")

if __name__ == "__main__":
    asyncio.run(verify_pipeline())
