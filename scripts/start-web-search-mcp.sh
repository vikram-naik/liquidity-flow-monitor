#!/bin/bash

DDG_SAFE_SEARCH=OFF DDG_REGION=us-en uvx --refresh --with "duckduckgo-mcp-server[browser]" duckduckgo-mcp-server \
  --transport sse \
  --host 0.0.0.0 \
  --port 7070 \
  --fetch-backend auto

