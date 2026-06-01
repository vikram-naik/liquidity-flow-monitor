"""
OpenAI and OpenAI-compatible LLM Provider.
"""

from __future__ import annotations

import os
import json
import logging
from typing import Type, TypeVar
import urllib.request
import urllib.error
import asyncio
from pydantic import BaseModel

from pathlib import Path
from src.agents.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def yahoo_search_fallback(query: str) -> str:
    """Fallback Yahoo Search scraper to bypass DuckDuckGo's aggressive bot firewalls."""
    import urllib.request
    import urllib.parse
    import re
    
    url = f"https://search.yahoo.com/search?q={urllib.parse.quote(query)}"
    logger.info(f"Running high-reliability Yahoo Search fallback for query: '{query}'")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8")
            
            blocks = re.findall(r'<div class="[^"]*algo[^"]*".*?</li>', html, re.DOTALL)
            parsed_results = []
            
            for block in blocks:
                # Extract link and title
                link_match = re.search(r'<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', block, re.DOTALL)
                if not link_match:
                    continue
                url_val = link_match.group(1)
                title_val = re.sub(r'<[^>]*>', '', link_match.group(2)).strip()
                
                # Extract snippet
                snippet_match = re.search(r'<div class="[^"]*compText[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
                snippet_val = ""
                if snippet_match:
                    snippet_val = re.sub(r'<[^>]*>', '', snippet_match.group(1)).strip()
                
                # Clean Yahoo redirected URL if present
                clean_url = url_val
                ru_match = re.search(r'/RU=([^/]*)/', url_val)
                if ru_match:
                    clean_url = urllib.parse.unquote(ru_match.group(1))
                
                parsed_results.append(
                    f"Title: {title_val}\nURL: {clean_url}\nSnippet: {snippet_val}\n---"
                )
                
            if parsed_results:
                logger.info(f"Successfully retrieved {len(parsed_results)} Yahoo Search results.")
                return "\n".join(parsed_results[:5])
            else:
                return "No results returned from Yahoo Search."
                
    except Exception as e:
        logger.error(f"Yahoo Search fallback failed: {e}")
        return f"Yahoo Search fallback failed: {e}"


class OpenAIProvider(BaseLLMProvider):
    """Provides structured text generation using OpenAI or any OpenAI-compatible endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "gpt-4o-mini",
        system_prompt: str | None = None
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.model = model

        # Load external system prompt
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

        if not self.api_key and "openai.com" in self.base_url:
            logger.warning("No OPENAI_API_KEY found for OpenAI API endpoint.")

    async def generate_structured_json(
        self,
        prompt: str,
        response_schema: Type[T],
        enable_search: bool = False
    ) -> T | None:
        """Generates structured content conforming to response_schema using OpenAI-compatible APIs."""
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Setup structured output response format if supported, otherwise standard JSON object mode
        # OpenAI strict schema format
        json_schema = response_schema.model_json_schema()
        
        # Clean up unsupported fields in strict JSON schema if needed
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": response_schema.__name__,
                "schema": json_schema,
                "strict": True
            }
        }

        loop = asyncio.get_running_loop()

        # If search/grounding is requested, fetch and execute tools via the local MCP server in an agentic loop!
        if enable_search:
            mcp_url = os.getenv("MCP_SEARCH_URL", "http://127.0.0.1:7070/sse")
            logger.info(f"Connecting to MCP server for agentic tool use at: {mcp_url}")
            try:
                from mcp import ClientSession
                from mcp.client.sse import sse_client

                async with sse_client(mcp_url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        mcp_tools = await session.list_tools()
                        
                        # Map tools to OpenAI format
                        openai_tools = []
                        for t in mcp_tools.tools:
                            openai_tools.append({
                                "type": "function",
                                "function": {
                                    "name": t.name,
                                    "description": t.description,
                                    "parameters": t.inputSchema
                                }
                            })
                        
                        # Construct a search payload WITHOUT response_format so the model can freely output tool calls
                        search_payload = {
                            "model": self.model,
                            "messages": [
                                {
                                    "role": "system",
                                    "content": self.system_prompt
                                },
                                {
                                    "role": "user",
                                    "content": prompt
                                }
                            ],
                            "temperature": 0.1
                        }

                        if openai_tools:
                            search_payload["tools"] = openai_tools
                            logger.info(f"Successfully registered {len(openai_tools)} MCP tools for LLM agentic use.")
                            
                        # Stateful multi-turn tool calling loop
                        did_tool_call = False
                        for turn in range(5):
                            logger.info(f"Executing LLM Tool-Use Turn {turn + 1}...")
                            raw_response = await loop.run_in_executor(
                                None, 
                                self._send_http_request, 
                                url, 
                                search_payload, 
                                headers
                            )
                            response_json = json.loads(raw_response)
                            choices = response_json.get("choices", [])
                            if not choices:
                                logger.error("No choices returned in tool-use loop.")
                                return None
                                
                            msg = choices[0]["message"]
                            
                            # Log intermediate LLM thoughts/reasoning
                            content = msg.get("content")
                            if content and content.strip():
                                logger.info(f"\n--- LLM Thoughts/Reasoning (Turn {turn + 1}) ---\n{content.strip()}\n--------------------------------------\n")
                            
                            tool_calls = msg.get("tool_calls")
                            
                            if tool_calls:
                                did_tool_call = True
                                logger.info(f"Model requested {len(tool_calls)} tool calls.")
                                
                                # 1. Append assistant message containing tool calls
                                search_payload["messages"].append(msg)
                                
                                # 2. Execute each requested tool call
                                for tc in tool_calls:
                                    tc_id = tc["id"]
                                    func = tc["function"]
                                    t_name = func["name"]
                                    t_args = json.loads(func["arguments"])
                                    
                                    logger.info(f"-> [MCP Tool Call] {t_name}({json.dumps(t_args)})")
                                    # Pacing MCP tool calls to prevent anti-bot rate-limiting
                                    logger.info("Pacing MCP call: sleeping 2.5 seconds...")
                                    await asyncio.sleep(2.5)
                                    
                                    content_text = ""
                                    if t_name == "search":
                                        try:
                                            result = await session.call_tool(name=t_name, arguments=t_args)
                                            for content_item in result.content:
                                                if getattr(content_item, "type", None) == "text":
                                                    content_text += content_item.text + "\n"
                                        except Exception as mcp_err:
                                            logger.warning(f"MCP search call threw exception: {mcp_err}. Triaging to fallback...")
                                            
                                        # If DDG search returned bot detection block or was empty, trigger high-reliability Yahoo fallback!
                                        if not content_text.strip() or "No results were found" in content_text or "bot detection" in content_text:
                                            logger.warning("DuckDuckGo MCP search blocked or empty. Invoking high-reliability Yahoo Search fallback...")
                                            query_str = t_args.get("query", "")
                                            content_text = yahoo_search_fallback(query_str)
                                    else:
                                        # Standard content fetching or non-search tools
                                        try:
                                            result = await session.call_tool(name=t_name, arguments=t_args)
                                            for content_item in result.content:
                                                if getattr(content_item, "type", None) == "text":
                                                    content_text += content_item.text + "\n"
                                        except Exception as mcp_err:
                                            logger.error(f"MCP tool call '{t_name}' failed: {mcp_err}")
                                            content_text = f"MCP tool call '{t_name}' failed: {mcp_err}"
                                            
                                    logger.info(f"<- [MCP Tool Response] {content_text.strip()[:400]}... (total {len(content_text)} chars)")
                                    
                                    # Append tool execution observation
                                    search_payload["messages"].append({
                                        "role": "tool",
                                        "tool_call_id": tc_id,
                                        "name": t_name,
                                        "content": content_text.strip()
                                    })
                            else:
                                # No tool calls requested, we are done searching!
                                logger.info("Model finished search reasoning turn.")
                                search_payload["messages"].append(msg)
                                break
                        else:
                            logger.warning("Tool-use loop reached max turns without finishing.")

                        # Now perform the final generation pass to force JSON schema output!
                        logger.info("Executing final structured JSON formatting turn...")
                        search_payload["messages"].append({
                            "role": "user",
                            "content": "Now, compile all your findings, SEBI filings, corporate announcements, and bulk deal disclosures gathered during your search. Generate the final structured JSON conforming to the requested schema."
                        })
                        search_payload["response_format"] = response_format
                        if "tools" in search_payload:
                            del search_payload["tools"]

                        raw_final_response = await loop.run_in_executor(
                            None,
                            self._send_http_request,
                            url,
                            search_payload,
                            headers
                        )
                        final_json = json.loads(raw_final_response)
                        final_choices = final_json.get("choices", [])
                        if not final_choices:
                            logger.error("No choices returned in final formatting pass.")
                            return None
                            
                        final_content = final_choices[0]["message"]["content"].strip()
                        return response_schema.model_validate_json(final_content)

            except Exception as e:
                logger.error(f"Agentic MCP search loop failed: {e}. Falling back to search-less inference.")

        # Standard non-grounded / fallback completions call (or fallback from MCP search)
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": self.system_prompt
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "response_format": response_format,
            "temperature": 0.1
        }
        try:
            logger.info("Executing standard structured text generation...")
            raw_response = await loop.run_in_executor(
                None, 
                self._send_http_request, 
                url, 
                payload, 
                headers
            )
            response_json = json.loads(raw_response)
            
            choices = response_json.get("choices", [])
            if not choices:
                logger.error(f"No choices returned in standard OpenAI response: {response_json}")
                return None
                
            content = choices[0]["message"]["content"].strip()
            return response_schema.model_validate_json(content)
        except Exception as e:
            logger.error(f"Structured generation failed: {e}")
            return None

    def _send_http_request(self, url: str, payload: dict, headers: dict) -> str:
        """Synchronous HTTP post wrapper run inside threadpool executor."""
        req = urllib.request.Request(
            url, 
            data=json.dumps(payload).encode("utf-8"), 
            headers=headers, 
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err_content = e.read().decode("utf-8")
            raise RuntimeError(f"HTTP {e.code}: {e.reason} - Details: {err_content}")
