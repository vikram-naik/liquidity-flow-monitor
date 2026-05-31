"""
LFM Stage 9: Qualitative Agentic Filter (The Guard).

Orchestrates the adversarial qualitative forensic audit on symbols
that have cleared the quantitative Gate, using Google Gemini Pro
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

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from src.database import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


class GuardOrchestrator:
    """Manages the EOD qualitative LLM Guard pipeline."""

    def __init__(self, db_path: str = str(DB_PATH)):
        self.db_path = db_path

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

    async def execute_forensic_audit(self, candidate: dict) -> dict | None:
        """Run the adversarial forensic audit using Gemini Pro with web search."""
        symbol = candidate["symbol"]
        date = candidate["date"]
        logger.info(f"Initiating Forensic Audit for {symbol} (Gate Score: {candidate['gate_score']:.2f})...")

        if not GEMINI_API_KEY:
            logger.warning(f"GEMINI_API_KEY not configured. Running {symbol} in simulation fallback mode...")
            await asyncio.sleep(0.5)
            return self._generate_simulated_result(candidate)

        # Adversarial Forensic Prompt
        prompt = f"""
        Conduct a qualitative forensic audit on Indian equity ticker: {symbol}
        Gate Detection Date: {date}
        Underlying price: INR {candidate["price"]}
        Setup Footprint: {candidate["setup_tag"]}

        Act as an adversarial short-seller or a highly cynical Risk Officer at a quantitative fund.
        Assume the technical signal is a structural trap until proven otherwise.

        AUDIT CONSTRAINTS & RATIONALE:
        1. LIQUIDITY-NEUTRAL OR MECHANICAL FLOW AUDIT:
           - Did a massive pre-negotiated block/bulk deal or promoter stake sale occur on {date}?
           - Is this date close to an MSCI, FTSE, or Nifty index rebalance flow date for {symbol}?

        2. FUNDAMENTAL & CASH FLOW CONVERSION AUDIT:
           - Cross-reference Screener.in data: Is Debt-to-Equity > 1.0? 
           - Does 3-year Cumulative Operating Cash Flow (OCF) diverge significantly from 3-year Net Profit (OCF/Net Profit < 0.80)? If yes, flag inventory or receivables bloating.
           - Are Receivable Days deteriorating? Is the RoCE < 15%?

        3. PROMOTER INTEGRITY & GOVERNANCE AUDIT:
           - Is promoter pledging > 10%? Any pledging > 20% must be flagged as a critical margin-call risk.
           - Has promoter stake decreased by > 1% in the last 3 quarters? Who is selling shares?
           - Are there any pending regulatory investigations, SEBI warnings, or auditor resignations?

        4. THE VETO RISK REPORT:
           - Provide a strict 3-point adversarial short thesis explaining why a rational investor should VETO this breakout.

        Return the final verdict in raw JSON matching this schema:
        {{
          "symbol": "{symbol}",
          "verdict": "APPROVE" | "VETO",
          "veto_reasons": ["List of red flags if VETOed, else empty"],
          "catalyst_type": "GENUINE_ACCUMULATION" | "BLOCK_DEAL_DISTRIBUTION" | "PASSIVE_INDEX_FLOW" | "RETAIL_CHURN_PUMP" | "DEBT_STRESSED_LIQUIDATION",
          "fundamental_grade": "A" | "B" | "C" | "F",
          "governance_risk": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
          "qualitative_score": 0 to 100,
          "key_metrics_checked": {{
            "debt_to_equity": 0.0,
            "promoter_pledge_pct": 0.0,
            "ocf_to_net_profit_3yr": 0.0,
            "receivable_days_trend": "IMPROVING" | "STABLE" | "DETERIORATING"
          }},
          "evidence_citations": ["List of source URLs and announcement reference numbers"]
        }}
        """

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={GEMINI_API_KEY}"
        
        # Build API payload with Google Search Tool enabled
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ],
            "tools": [
                {
                    "googleSearchRetrieval": {}
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }

        # Run block-based HTTP request in executor to respect async event loop
        loop = asyncio.get_running_loop()
        try:
            try:
                raw_response = await loop.run_in_executor(None, self._send_http_request, url, payload)
            except Exception as first_err:
                logger.warning(f"Initial search-grounded API call failed for {symbol}: {first_err}. Retrying without search tools...")
                # Clean tools property to fallback to standard inference
                if "tools" in payload:
                    del payload["tools"]
                raw_response = await loop.run_in_executor(None, self._send_http_request, url, payload)

            result = json.loads(raw_response)
            
            # Extract JSON from the Gemini response structure
            candidates_list = result.get("candidates", [])
            if not candidates_list:
                logger.error(f"No response candidates returned by Gemini for {symbol}")
                return None
                
            text = candidates_list[0]["content"]["parts"][0]["text"].strip()
            
            # Clean up potential markdown wraps
            if text.startswith("```json"):
                text = text.split("```json")[1].split("```")[0].strip()
            elif text.startswith("```"):
                text = text.split("```")[1].split("```")[0].strip()
                
            audit_json = json.loads(text)
            logger.info(f"Audit completed for {symbol}. Verdict: {audit_json.get('verdict')} (Qualitative Score: {audit_json.get('qualitative_score')})")
            return audit_json

        except Exception as e:
            logger.error(f"Qualitative audit failed completely for {symbol}: {e}")
            return None

    def _send_http_request(self, url: str, payload: dict) -> str:
        """Synchronous HTTP request wrapper run inside threadpool executor."""
        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err_content = e.read().decode("utf-8")
            raise RuntimeError(f"HTTP {e.code}: {e.reason} - Details: {err_content}")

    def _generate_simulated_result(self, candidate: dict) -> dict:
        """Simulation fallback generator for local testing when API key is missing."""
        symbol = candidate["symbol"]
        # Basic deterministic outcomes to make testing predictable
        is_veto = (symbol.startswith("Z") or "exit" in candidate["setup_tag"].lower())
        return {
            "symbol": symbol,
            "verdict": "VETO" if is_veto else "APPROVE",
            "veto_reasons": ["Debt-to-equity ratio > 1.2", "Auditor warning on subsidiary holdings"] if is_veto else [],
            "catalyst_type": "DEBT_STRESSED_LIQUIDATION" if is_veto else "GENUINE_ACCUMULATION",
            "fundamental_grade": "C" if is_veto else "A",
            "governance_risk": "HIGH" if is_veto else "LOW",
            "qualitative_score": 45 if is_veto else 88,
            "key_metrics_checked": {
                "debt_to_equity": 1.45 if is_veto else 0.12,
                "promoter_pledge_pct": 22.0 if is_veto else 0.0,
                "ocf_to_net_profit_3yr": 0.62 if is_veto else 0.94,
                "receivable_days_trend": "DETERIORATING" if is_veto else "STABLE"
            },
            "evidence_citations": [f"https://www.screener.in/company/{symbol}"]
        }

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
