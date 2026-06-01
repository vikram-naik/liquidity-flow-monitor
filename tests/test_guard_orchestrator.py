"""
Unit tests for the modular LLM Guard Provider interface and GuardOrchestrator.
"""

from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock

from src.agents.llm import (
    get_llm_provider,
    BaseLLMProvider,
    ForensicAuditResult,
    SimulationProvider,
    GeminiProvider,
    OpenAIProvider,
)
from src.agents.guard_orchestrator import GuardOrchestrator


def test_factory_default():
    """Verify that get_llm_provider resolves to simulation when no env variables are set."""
    with patch.dict(os.environ, {}, clear=True):
        provider = get_llm_provider()
        assert isinstance(provider, SimulationProvider)


def test_factory_explicit():
    """Verify that get_llm_provider handles explicit names."""
    provider = get_llm_provider("simulation")
    assert isinstance(provider, SimulationProvider)

    provider_open = get_llm_provider("openai", api_key="test-key")
    assert isinstance(provider_open, OpenAIProvider)
    assert provider_open.api_key == "test-key"

    provider_gemini = get_llm_provider("gemini", api_key="test-key")
    assert isinstance(provider_gemini, GeminiProvider)
    assert provider_gemini.api_key == "test-key"


@pytest.mark.asyncio
async def test_simulation_provider_approve():
    """Verify that SimulationProvider parses the prompt and returns APPROVED result."""
    provider = SimulationProvider()
    prompt = """
    Conduct a qualitative forensic audit on Indian equity ticker: RELIANCE
    Gate Detection Date: 2026-06-01
    Underlying price: INR 2450.00
    Setup Footprint: SIAB
    F&O Segment Eligibility: The verified F&O segment status for RELIANCE is: TRUE.
    """
    
    result = await provider.generate_structured_json(
        prompt=prompt,
        response_schema=ForensicAuditResult
    )
    
    assert result is not None
    assert result.symbol == "RELIANCE"
    assert result.verdict == "APPROVE"
    assert result.catalyst_type == "GENUINE_ACCUMULATION"
    assert result.fundamental_grade == "A"
    assert result.governance_risk == "LOW"
    assert result.qualitative_score == 88.0
    assert result.key_metrics_checked.fo_eligibility == "TRUE"
    assert result.key_metrics_checked.block_bulk_deal_type == "CLEAN_SWEEP"
    assert len(result.veto_reasons) == 0


@pytest.mark.asyncio
async def test_simulation_provider_veto():
    """Verify that SimulationProvider parses the prompt and returns VETO result."""
    provider = SimulationProvider()
    prompt = """
    Conduct a qualitative forensic audit on Indian equity ticker: ZEE
    Gate Detection Date: 2026-06-01
    Underlying price: INR 150.00
    Setup Footprint: SIAB
    F&O Segment Eligibility: The verified F&O segment status for ZEE is: FALSE.
    """
    
    result = await provider.generate_structured_json(
        prompt=prompt,
        response_schema=ForensicAuditResult
    )
    
    assert result is not None
    assert result.symbol == "ZEE"
    assert result.verdict == "VETO"
    assert result.catalyst_type == "BLOCK_DEAL_DISTRIBUTION"
    assert result.fundamental_grade == "C"
    assert result.governance_risk == "HIGH"
    assert result.qualitative_score == 45.0
    assert result.key_metrics_checked.fo_eligibility == "FALSE"
    assert result.key_metrics_checked.block_bulk_deal_type == "PASSIVE_CROSSING"
    assert len(result.veto_reasons) > 0


@pytest.mark.asyncio
async def test_orchestrator_integration():
    """Verify that GuardOrchestrator works seamlessly with the modularized SimulationProvider."""
    # Use SimulationProvider explicitly to avoid relying on environment variables during test
    provider = SimulationProvider()
    orchestrator = GuardOrchestrator(llm_provider=provider)
    
    candidate = {
        "symbol": "TATASTEEL",
        "date": "2026-06-01",
        "price": 170.50,
        "gate_score": 85.0,
        "setup_tag": "CDMA"
    }
    
    # Mock database check for F&O status to return True deterministically
    orchestrator.is_symbol_fo_eligible = MagicMock(return_value=True)
    
    audit_result = await orchestrator.execute_forensic_audit(candidate)
    
    assert audit_result is not None
    assert audit_result["symbol"] == "TATASTEEL"
    assert audit_result["verdict"] == "APPROVE"
    assert audit_result["catalyst_type"] == "GENUINE_ACCUMULATION"
    assert audit_result["qualitative_score"] == 88.0
    assert audit_result["key_metrics_checked"]["fo_eligibility"] == "TRUE"
