"""
Simulation LLM Provider for local testing and deterministic mock outputs.
"""

from __future__ import annotations

import re
import logging
from typing import Type, TypeVar
from pydantic import BaseModel

from src.agents.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class SimulationProvider(BaseLLMProvider):
    """
    Simulation LLM Provider that parses candidate metadata from the prompt
    and returns a deterministic mock result for testing and simulation fallback.
    """

    async def generate_structured_json(
        self,
        prompt: str,
        response_schema: Type[T],
        enable_search: bool = False
    ) -> T | None:
        """Parses the prompt to simulate a realistic, deterministic response matching response_schema."""
        logger.info("Running in Simulation mode...")

        # Extract symbol
        symbol_match = re.search(r"ticker:\s*([A-Za-z0-9_-]+)", prompt, re.IGNORECASE)
        symbol = symbol_match.group(1).strip() if symbol_match else "UNKNOWN"

        # Extract setup_tag
        setup_match = re.search(r"Setup Footprint:\s*(.*)", prompt, re.IGNORECASE)
        setup_tag = setup_match.group(1).strip() if setup_match else "UNKNOWN"

        # Extract F&O eligibility
        fo_match = re.search(r"F&O segment status for .*? is:\s*(TRUE|FALSE)", prompt, re.IGNORECASE)
        fo_eligibility = fo_match.group(1).strip() if fo_match else "TRUE"

        # Deterministic simulation rules matching original _generate_simulated_result logic
        is_veto = (symbol.startswith("Z") or "exit" in setup_tag.lower())

        mock_data = {
            "symbol": symbol,
            "verdict": "VETO" if is_veto else "APPROVE",
            "veto_reasons": [
                "Passive block deal crossing without open-market sweep", 
                "MSCI index reweighting mechanical inflow"
            ] if is_veto else [],
            "catalyst_type": "BLOCK_DEAL_DISTRIBUTION" if is_veto else "GENUINE_ACCUMULATION",
            "fundamental_grade": "C" if is_veto else "A",
            "governance_risk": "HIGH" if is_veto else "LOW",
            "qualitative_score": 45.0 if is_veto else 88.0,
            "key_metrics_checked": {
                "fo_eligibility": fo_eligibility,
                "block_bulk_deal_type": "PASSIVE_CROSSING" if is_veto else "CLEAN_SWEEP",
                "index_rebalance_proximity": "TRUE" if is_veto else "FALSE",
                "insider_transaction_type": "SELLING" if is_veto else "BUYING",
                "derivative_expiry_pressure": "FALSE",
                "unexplained_pump": "FALSE"
            },
            "evidence_citations": [f"https://www.nseindia.com/get-quotes/equity?symbol={symbol}"]
        }

        try:
            return response_schema.model_validate(mock_data)
        except Exception as e:
            logger.error(f"Failed to validate simulated response against schema: {e}")
            return None
