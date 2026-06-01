"""
Unit test for OpenAIProvider's multi-turn search/tool-use loop.
"""

from __future__ import annotations

import os
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.llm import OpenAIProvider, ForensicAuditResult


@pytest.mark.asyncio
async def test_openai_provider_with_search_loop():
    """Verify that OpenAIProvider executes the multi-turn tool use loop correctly."""
    # 1. Mock MCP components
    mock_tool = MagicMock()
    mock_tool.name = "web_search"
    mock_tool.description = "Search the web"
    mock_tool.inputSchema = {"type": "object"}

    mock_list_tools = MagicMock()
    mock_list_tools.tools = [mock_tool]

    mock_tool_result = MagicMock()
    mock_tool_content = MagicMock()
    mock_tool_content.type = "text"
    mock_tool_content.text = "NSE block deal: RELIANCE 100000 shares clean sweep."
    mock_tool_result.content = [mock_tool_content]

    # Create session mock
    session_instance = AsyncMock()
    session_instance.initialize = AsyncMock()
    session_instance.list_tools = AsyncMock(return_value=mock_list_tools)
    session_instance.call_tool = AsyncMock(return_value=mock_tool_result)

    # We need to mock `sse_client` which is a context manager.
    mock_read_write = (MagicMock(), MagicMock())
    
    class AsyncContextManagerMock:
        async def __aenter__(self):
            return mock_read_write
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    # 2. Mock responses from OpenAI completions
    # Turn 1: Request tool call
    choice1 = {
        "message": {
            "role": "assistant",
            "content": "Let me search the web for block deals.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": '{"query": "RELIANCE block deals"}'
                    }
                }
            ]
        }
    }
    
    # Turn 2: Done searching (no tool calls)
    choice2 = {
        "message": {
            "role": "assistant",
            "content": "I have completed the search. The results look good."
        }
    }

    # Final Turn: final structured JSON matching ForensicAuditResult
    expected_response_dict = {
        "symbol": "RELIANCE",
        "verdict": "APPROVE",
        "veto_reasons": [],
        "catalyst_type": "GENUINE_ACCUMULATION",
        "fundamental_grade": "A",
        "governance_risk": "LOW",
        "qualitative_score": 90.0,
        "key_metrics_checked": {
            "fo_eligibility": "TRUE",
            "block_bulk_deal_type": "CLEAN_SWEEP",
            "index_rebalance_proximity": "FALSE",
            "insider_transaction_type": "NONE",
            "derivative_expiry_pressure": "FALSE",
            "unexplained_pump": "FALSE"
        },
        "evidence_citations": ["https://nseindia.com/block-deals"]
    }
    
    choice3 = {
        "message": {
            "role": "assistant",
            "content": json.dumps(expected_response_dict)
        }
    }

    # Sequence of responses returned by _send_http_request
    response_sequence = [
        json.dumps({"choices": [choice1]}),
        json.dumps({"choices": [choice2]}),
        json.dumps({"choices": [choice3]})
    ]
    
    current_response_idx = 0

    def mock_send_http_request(url, payload, headers):
        nonlocal current_response_idx
        res = response_sequence[current_response_idx]
        if current_response_idx < len(response_sequence) - 1:
            current_response_idx += 1
        return res

    provider = OpenAIProvider(api_key="test-key", model="gpt-4o-mini")
    provider._send_http_request = MagicMock(side_effect=mock_send_http_request)

    # 3. Patch sse_client and ClientSession in the mcp modules directly
    with patch("mcp.client.sse.sse_client", return_value=AsyncContextManagerMock()), \
         patch("mcp.ClientSession") as mock_session_class_patched:
        
        class ClientSessionCM:
            async def __aenter__(self):
                return session_instance
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass
        
        mock_session_class_patched.return_value = ClientSessionCM()

        # Run the structured generation with search enabled
        result = await provider.generate_structured_json(
            prompt="Conduct qualitative audit on RELIANCE",
            response_schema=ForensicAuditResult,
            enable_search=True
        )

        assert result is not None
        assert result.symbol == "RELIANCE"
        assert result.verdict == "APPROVE"
        assert result.catalyst_type == "GENUINE_ACCUMULATION"
        assert result.qualitative_score == 90.0
        
        # Verify the sequence of calls: 3 HTTP completions requests were sent
        assert provider._send_http_request.call_count == 3
