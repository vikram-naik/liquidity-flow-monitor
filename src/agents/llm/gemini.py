"""
Gemini LLM Provider using Google's new official google-genai SDK.
"""

from __future__ import annotations

import os
import logging
from typing import Type, TypeVar
from pydantic import BaseModel

from google import genai
from google.genai import types
from google.genai.errors import APIError

from src.agents.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class GeminiProvider(BaseLLMProvider):
    """Provides structured text generation using the new Google GenAI SDK and gemini-3.5-flash."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-3.5-flash",
        system_prompt: str | None = None
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("No GEMINI_API_KEY provided or found in environment variables.")
        
        # Initialize Google GenAI client
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        self.model = model

        # Load external system prompt
        from pathlib import Path
        if system_prompt:
            self.system_prompt = system_prompt
        else:
            try:
                prompt_path = Path(__file__).resolve().parent / "system_prompt.txt"
                with open(prompt_path, "r", encoding="utf-8") as f:
                    self.system_prompt = f.read().strip()
            except Exception as e:
                logger.warning(f"Failed to load system_prompt.txt: {e}. Using fallback system prompt.")
                self.system_prompt = "You are a professional adversarial qualitative analyst. Act strictly as requested and output data matching the required schema."

    async def generate_structured_json(
        self,
        prompt: str,
        response_schema: Type[T],
        enable_search: bool = False
    ) -> T | None:
        """Generates structured content conforming to response_schema using Gemini."""
        if not self.client:
            logger.error("GeminiClient is not initialized (missing API key).")
            return None

        # Build tools if search grounding is requested
        tools = [types.Tool(google_search=types.GoogleSearch())] if enable_search else None

        # Configure reasoning/thinking effort to "LOW" as requested
        thinking_config = None
        try:
            thinking_config = types.ThinkingConfig(thinking_level="LOW")
            logger.info("Configured Gemini thinking_config effort level to: 'LOW'")
        except Exception as e:
            logger.warning(f"ThinkingConfig with thinking_level='LOW' not supported by this SDK version: {e}. Falling back to default.")

        config = types.GenerateContentConfig(
            tools=tools,
            response_mime_type="application/json",
            response_schema=response_schema,
            system_instruction=self.system_prompt,
            thinking_config=thinking_config,
            temperature=0.1,  # Low temperature for highly consistent forensic analysis
        )

        try:
            # We execute client call
            logger.info(f"Sending request to {self.model} (search_grounding={enable_search})...")
            
            # Since generate_content is synchronous in the basic client, we run in executor
            # google-genai Client also supports async via client.aio, but to keep execution simple
            # and prevent potential event-loop blockages, we can use client.aio if it exists or run synchronous call.
            # Let's check: the google-genai SDK supports `client.aio.models.generate_content`.
            # Let's use `client.aio` which is the official async client in the new SDK!
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=config
                )
            except Exception as e:
                # If search is enabled, try falling back to search-less generation on error
                if enable_search:
                    logger.warning(f"Gemini search-grounded call failed: {e}. Retrying without search...")
                    config.tools = None
                    response = await self.client.aio.models.generate_content(
                        model=self.model,
                        contents=prompt,
                        config=config
                    )
                else:
                    raise e

            # Parse and validate response
            parsed_obj = None
            if response.parsed:
                parsed_obj = response.parsed
            else:
                text = response.text
                if not text:
                    logger.error("Received empty response text from Gemini.")
                    return None
                
                # Clean markdown JSON wrapping if present
                if text.startswith("```json"):
                    text = text.split("```json")[1].split("```")[0].strip()
                elif text.startswith("```"):
                    text = text.split("```")[1].split("```")[0].strip()
                    
                parsed_obj = response_schema.model_validate_json(text)

            # Programmatically harvest and stamp live Google Search Grounding URLs
            if parsed_obj and hasattr(response, "candidates") and response.candidates:
                candidate = response.candidates[0]
                if getattr(candidate, "grounding_metadata", None):
                    metadata = candidate.grounding_metadata
                    
                    # Log executed search queries
                    queries = getattr(metadata, "web_search_queries", None)
                    if queries:
                        logger.info(f"Gemini executed Google Search Grounding queries: {queries}")
                        
                    # Extract source URLs from grounding chunks
                    chunks = getattr(metadata, "grounding_chunks", None)
                    if chunks:
                        grounding_urls = []
                        for chunk in chunks:
                            web = getattr(chunk, "web", None)
                            if web and getattr(web, "uri", None):
                                grounding_urls.append(web.uri)
                                
                        if grounding_urls:
                            logger.info(f"Harvested {len(grounding_urls)} live Google Search Grounding URLs.")
                            # Ensure evidence_citations is initialized
                            if not getattr(parsed_obj, "evidence_citations", None):
                                parsed_obj.evidence_citations = []
                            # Merge and eliminate duplicates
                            current_citations = set(parsed_obj.evidence_citations)
                            for url in grounding_urls:
                                if url not in current_citations:
                                    parsed_obj.evidence_citations.append(url)
                                    current_citations.add(url)
                                    
            return parsed_obj

        except APIError as api_err:
            logger.error(f"Gemini API Error occurred: {api_err}")
            return None
        except Exception as e:
            logger.error(f"Failed to generate structured JSON from Gemini: {e}")
            return None
