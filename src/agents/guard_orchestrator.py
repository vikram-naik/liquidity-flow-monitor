"""
LFM Stage 9: Qualitative Agentic Filter (The Guard).

Orchestrates the adversarial qualitative forensic audit on symbols
that have cleared the quantitative Gate, using Google Gemini 3.5 Flash
with built-in web search tools.
"""

from __future__ import annotations

import os
import sys
import json
import sqlite3
import logging
import urllib.request
import urllib.error
import asyncio
from datetime import datetime
from pathlib import Path
from string import Template

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from src.database import DB_PATH
from src.agents.llm import BaseLLMProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


class GuardOrchestrator:
    """Manages the EOD qualitative LLM Guard pipeline."""

    def __init__(self, db_path: str = str(DB_PATH), llm_provider: BaseLLMProvider | None = None):
        self.db_path = db_path
        self.prompt_template_path = Path(__file__).resolve().parent / "llm" / "forensic_prompt.txt"
        
        # Resolve LLM provider via the write-to-interface factory
        from src.agents.llm import get_llm_provider
        self.provider = llm_provider or get_llm_provider()

    def get_gate_candidates(self) -> list[dict]:
        """Fetch symbols from screener_signals that cleared the Gate (gate_signal = 1), capped at top 20."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            query = """
                SELECT symbol, date, price, gate_setup, s_total 
                FROM screener_signals 
                WHERE gate_signal = 1
                ORDER BY s_total DESC
                LIMIT 20
            """
            rows = conn.execute(query).fetchall()
            return [
                {
                    "symbol": r["symbol"],
                    "date": r["date"],
                    "price": r["price"],
                    "gate_score": r["s_total"],
                    "setup_tag": r["gate_setup"]
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Failed to query gate candidates: {e}")
            return []
        finally:
            conn.close()

    def is_symbol_fo_eligible(self, symbol: str) -> bool:
        """Check if symbol is currently in the NSE F&O watchlist in the database."""
        conn = sqlite3.connect(self.db_path)
        try:
            query = """
                SELECT 1 FROM watchlist_items 
                WHERE watchlist_id = (SELECT id FROM watchlists WHERE name = 'NSE F&O')
                AND symbol = ?
            """
            row = conn.execute(query, (symbol.strip().upper(),)).fetchone()
            return row is not None
        except Exception as e:
            logger.error(f"Failed to query F&O eligibility for {symbol}: {e}")
            return False
        finally:
            conn.close()

    async def execute_forensic_audit(self, candidate: dict) -> dict | None:
        """Run the adversarial forensic audit using the configured LLM provider."""
        symbol = candidate["symbol"]
        date = candidate["date"]
        logger.info(f"Initiating Forensic Audit for {symbol} (Gate Score: {candidate['gate_score']:.2f})...")

        # Load and fill adversarial forensic prompt template
        try:
            with open(self.prompt_template_path, "r", encoding="utf-8") as f:
                template_content = f.read()
            template = Template(template_content)
            
            # Fetch verified F&O status from the database F&O watchlist
            is_fo = self.is_symbol_fo_eligible(symbol)
            fo_eligibility_str = "TRUE" if is_fo else "FALSE"
            logger.info(f"Verified F&O status for {symbol} in database: {fo_eligibility_str}")

            prompt = template.substitute(
                symbol=symbol,
                date=date,
                price=candidate["price"],
                setup_tag=candidate["setup_tag"],
                fo_eligibility=fo_eligibility_str
            )
        except Exception as err:
            logger.error(f"Failed to load prompt template from {self.prompt_template_path}: {err}")
            return None

        # Execute structured generation using the provider
        from src.agents.llm import ForensicAuditResult
        
        try:
            result_model = await self.provider.generate_structured_json(
                prompt=prompt,
                response_schema=ForensicAuditResult,
                enable_search=True
            )
            
            if result_model is None:
                logger.error(f"LLM Provider returned None for {symbol}")
                return None
                
            audit_json = result_model.model_dump()
            logger.info(f"Audit completed for {symbol}. Verdict: {audit_json.get('verdict')} (Qualitative Score: {audit_json.get('qualitative_score')})")
            return audit_json

        except Exception as e:
            logger.error(f"Qualitative audit failed completely for {symbol}: {e}")
            return None


    def save_result(self, candidate: dict, audit: dict) -> None:
        """Persist the merged Gate-Guard state into gate_guard_signals SQLite table."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO gate_guard_signals (
                    symbol, date, price, gate_score, setup_tag,
                    verdict, catalyst_type, fundamental_grade, governance_risk,
                    qualitative_score, red_flags, ratios_json, citations
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                candidate["symbol"],
                candidate["date"],
                candidate["price"],
                candidate["gate_score"],
                candidate["setup_tag"],
                audit.get("verdict", "VETO"),
                audit.get("catalyst_type", "RETAIL_CHURN_PUMP"),
                audit.get("fundamental_grade", "F"),
                audit.get("governance_risk", "CRITICAL"),
                audit.get("qualitative_score", 0.0),
                json.dumps(audit.get("veto_reasons", [])),
                json.dumps(audit.get("key_metrics_checked", {})),
                json.dumps(audit.get("evidence_citations", []))
            ))
            conn.commit()
            logger.info(f"Persisted Gate-Guard state for {candidate['symbol']} successfully.")
        except Exception as e:
            logger.error(f"Failed to persist Gate-Guard state for {candidate['symbol']}: {e}")
        finally:
            conn.close()

    async def run_pipeline(self) -> None:
        """Execute EOD Qualitative Guard Pipeline."""
        # Purge stale records from the previous EOD run to avoid mixed data
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DELETE FROM gate_guard_signals")
            conn.commit()
            logger.info("Purged stale data from gate_guard_signals table.")
        except Exception as e:
            logger.error(f"Failed to clear stale data from gate_guard_signals: {e}")
        finally:
            conn.close()

        candidates = self.get_gate_candidates()
        if not candidates:
            logger.info("No tickers cleared the quantitative Gate today. LLM Guard audit bypassed.")
            return

        logger.info(f"Found {len(candidates)} candidates cleared by the Gate. Launching sequential qualitative audits...")
        
        # Process candidates sequentially to respect 5 RPM limit (15s sleep between calls)
        for i, cand in enumerate(candidates):
            if i > 0:
                logger.info("Rate Limiter: Sleeping 15 seconds to respect the 5 RPM API threshold...")
                await asyncio.sleep(15.0)
            
            audit = await self.execute_forensic_audit(cand)
            if audit:
                self.save_result(cand, audit)


def main():
    orchestrator = GuardOrchestrator()
    asyncio.run(orchestrator.run_pipeline())


if __name__ == "__main__":
    main()
