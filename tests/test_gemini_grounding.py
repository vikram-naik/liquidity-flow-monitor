"""
Unit test for GeminiProvider's search grounding metadata harvesting.
"""

from __future__ import annotations

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.llm import GeminiProvider, ForensicAuditResult


@pytest.mark.asyncio
async def test_gemini_provider_with_grounding_metadata():
    """Verify that GeminiProvider successfully extracts and merges Google Search grounding metadata."""
    # 1. Mock grounding_metadata structures
    mock_web = MagicMock()
    mock_web.uri = "https://economictimes.indiatimes.com/powergrid-block-deal"
    
    mock_chunk = MagicMock()
    mock_chunk.web = mock_web
    mock_chunk.maps = None
    
    mock_metadata = MagicMock()
    mock_metadata.web_search_queries = ["POWERGRID block deal 2026-06-01"]
    mock_metadata.grounding_chunks = [mock_chunk]
    
    mock_candidate = MagicMock()
    mock_candidate.grounding_metadata = mock_metadata
    
    # 2. Expected parsed Pydantic object
    expected_response = ForensicAuditResult(
        symbol="POWERGRID",
        verdict="VETO",
        veto_reasons=["Governance issues."],
        catalyst_type="UNEXPLAINED_PUMP_SUPPORTED",
        fundamental_grade="C",
        governance_risk="HIGH",
        qualitative_score=35.0,
        key_metrics_checked={
            "fo_eligibility": "TRUE",
            "block_bulk_deal_type": "NONE",
            "index_rebalance_proximity": "FALSE",
            "insider_transaction_type": "NONE",
            "derivative_expiry_pressure": "FALSE",
            "unexplained_pump": "TRUE"
        },
        evidence_citations=["https://nseindia.com/disclosures"]
    )
    
    mock_response = MagicMock()
    mock_response.parsed = expected_response
    mock_response.candidates = [mock_candidate]
    
    # 3. Mock genai.Client
    mock_client_instance = MagicMock()
    mock_client_instance.aio = MagicMock()
    mock_client_instance.aio.models = MagicMock()
    mock_client_instance.aio.models.generate_content = AsyncMock(return_value=mock_response)
    
    with patch("google.genai.Client", return_value=mock_client_instance):
        provider = GeminiProvider(api_key="test-key", model="gemini-3.5-flash")
        
        assert provider.client == mock_client_instance
        
        # Execute generate_structured_json with search enabled
        result = await provider.generate_structured_json(
            prompt="Audit POWERGRID",
            response_schema=ForensicAuditResult,
            enable_search=True
        )
        
        assert result is not None
        assert result.symbol == "POWERGRID"
        assert result.verdict == "VETO"
        
        # Verify the Google Search Grounding URLs were programmatically merged
        assert "https://economictimes.indiatimes.com/powergrid-block-deal" in result.evidence_citations
        assert "https://nseindia.com/disclosures" in result.evidence_citations
        assert len(result.evidence_citations) == 2
