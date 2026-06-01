"""
Llama.cpp local LLM Provider.
"""

from __future__ import annotations

import os
from src.agents.llm.openai import OpenAIProvider


class LlamaCppProvider(OpenAIProvider):
    """
    LLM Provider for local llama.cpp instances.
    Since llama.cpp server is fully OpenAI-compatible, we inherit from OpenAIProvider
    and default the target to the standard local server endpoint.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "gemma-4-E2B-it",
        system_prompt: str | None = None
    ):
        # Default to llama.cpp's default local server address if base_url is not provided
        default_base_url = base_url or os.getenv("LLAMACPP_BASE_URL", "http://localhost:8080/v1")
        super().__init__(
            api_key=api_key or "not-needed",
            base_url=default_base_url,
            model=model,
            system_prompt=system_prompt
        )
