"""
Base LLM provider interface and structured JSON models.
Defines the standard schema and the interface that all LLM providers must implement.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, List, Literal, Type, TypeVar
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class KeyMetricsChecked(BaseModel):
    fo_eligibility: str = Field(
        description="F&O segment eligibility status, must be 'TRUE' or 'FALSE'"
    )
    block_bulk_deal_type: Literal["NONE", "PASSIVE_CROSSING", "CLEAN_SWEEP"] = Field(
        description="Block/Bulk deal transaction pattern"
    )
    index_rebalance_proximity: str = Field(
        description="Proximity to scheduled index rebalancing, must be 'TRUE' or 'FALSE'"
    )
    insider_transaction_type: Literal["NONE", "BUYING", "SELLING", "PLEDGING"] = Field(
        description="Insider transaction pattern"
    )
    derivative_expiry_pressure: str = Field(
        description="Pressure from derivative expiry, must be 'TRUE' or 'FALSE'"
    )
    unexplained_pump: str = Field(
        description="Whether the pump has no corporate announcements/news, must be 'TRUE' or 'FALSE'"
    )


class ForensicAuditResult(BaseModel):
    symbol: str = Field(description="The ticker symbol of the audited stock")
    verdict: Literal["APPROVE", "VETO"] = Field(
        description="Qualitative forensic audit final decision"
    )
    veto_reasons: List[str] = Field(
        description="List of red flags or veto reasons if VETOed, else empty"
    )
    catalyst_type: Literal[
        "GENUINE_ACCUMULATION",
        "BLOCK_DEAL_DISTRIBUTION",
        "PASSIVE_INDEX_FLOW",
        "RETAIL_CHURN_PUMP",
        "DEBT_STRESSED_LIQUIDATION",
        "UNEXPLAINED_PUMP_SUPPORTED"
    ] = Field(description="Identified flow catalyst category")
    fundamental_grade: Literal["A", "B", "C", "F"] = Field(
        description="Assigned fundamental assessment grade"
    )
    governance_risk: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = Field(
        description="Assigned promoter/insider governance risk rating"
    )
    qualitative_score: float = Field(
        description="Overall qualitative confidence score from 0.0 to 100.0"
    )
    key_metrics_checked: KeyMetricsChecked = Field(
        description="Individual market mechanics and governance check outputs"
    )
    evidence_citations: List[str] = Field(
        description="List of source URLs, SEBI filing dates, or corporate announcement reference numbers"
    )


class BaseLLMProvider(ABC):
    """Abstract Base Class for all LLM providers in the LFM Guard pipeline."""

    @abstractmethod
    async def generate_structured_json(
        self,
        prompt: str,
        response_schema: Type[T],
        enable_search: bool = False
    ) -> T | None:
        """
        Generate a structured response adhering to response_schema.
        
        Args:
            prompt: The instruction prompt.
            response_schema: A Pydantic model class to enforce as the response structure.
            enable_search: Whether to enable web search grounding (if supported).
            
        Returns:
            An instance of response_schema (Pydantic model) or None if generation failed.
        """
        pass
