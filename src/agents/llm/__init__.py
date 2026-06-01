"""
LLM Call Interface package.
Implements the write-to-interface design pattern to support multiple LLM backends.
"""

from __future__ import annotations

import os
import logging

from src.agents.llm.base import BaseLLMProvider, ForensicAuditResult, KeyMetricsChecked
from src.agents.llm.gemini import GeminiProvider
from src.agents.llm.openai import OpenAIProvider
from src.agents.llm.llamacpp import LlamaCppProvider
from src.agents.llm.simulation import SimulationProvider

logger = logging.getLogger(__name__)


def get_llm_provider(
    provider_name: str | None = None,
    **kwargs
) -> BaseLLMProvider:
    """
    Factory function to retrieve the configured LLM provider instance.
    
    Args:
        provider_name: Explicit override for provider name ('gemini', 'openai', 'llamacpp', 'simulation').
                       If None, resolves from LLM_PROVIDER environment variable, falling back
                       to 'gemini' if GEMINI_API_KEY is present, else 'simulation'.
        **kwargs: Configuration arguments forwarded to the provider constructor.
        
    Returns:
        An instantiated BaseLLMProvider.
    """
    # 1. Resolve provider name
    if not provider_name:
        provider_name = os.getenv("LLM_PROVIDER")
        
    if not provider_name:
        if os.getenv("GEMINI_API_KEY"):
            provider_name = "gemini"
        elif os.getenv("OPENAI_API_KEY"):
            provider_name = "openai"
        else:
            provider_name = "simulation"

    provider_name = provider_name.lower().strip()
    logger.info(f"Resolving LLM Provider: {provider_name}")

    # 2. Instantiate and return provider
    if provider_name == "gemini":
        return GeminiProvider(**kwargs)
    elif provider_name == "openai":
        return OpenAIProvider(**kwargs)
    elif provider_name == "llamacpp":
        return LlamaCppProvider(**kwargs)
    elif provider_name == "simulation":
        return SimulationProvider()
    else:
        logger.warning(f"Unknown LLM provider '{provider_name}'. Falling back to 'simulation' mode.")
        return SimulationProvider()
