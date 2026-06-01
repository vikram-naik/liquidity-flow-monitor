# DuckDuckGo MCP Server Installation & Setup Guide

This guide details how to install the `uv` toolchain (which includes `uvx`) and run the `duckduckgo-mcp-server` directly in **browser mode** without cloning the source code.

---

## 1. How `uvx` Handles Dependencies (Globally vs. Locally)

> [!NOTE]
> **Does `uvx` install packages globally?**
> **No.** `uvx` (part of the `uv` toolchain) does not install packages or their dependencies globally, nor does it pollute your system's global Python environment.
>
> Instead, `uvx` downloads and runs applications in **isolated, ephemeral virtual environments** stored in `uv`'s internal system cache directory.
> * **Zero Dependency Conflicts:** Running a tool via `uvx` will never clash with other Python projects or global CLI packages.
> * **Automatic Caching:** Once downloaded, `uvx` caches the environment. Subsequent runs are near-instantaneous.
> * **Garbage Collection:** You can easily prune these cached environments when they are no longer needed.

---

## 2. Install `uv` and `uvx`

`uv` is an extremely fast Python package manager and resolver. `uvx` is its companion tool for running executable Python packages in isolated virtual environments.

### On macOS / Linux
Run the following in your terminal to install `uv` (which installs `uvx` automatically):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### On Windows
Run the following in PowerShell:
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Via Python `pip` (Alternative)
If you already have a global Python environment set up:
```bash
pip install uv
```

> [!TIP]
> After installation, you may need to restart your terminal or source your shell configuration (e.g., `source ~/.bashrc` or `source ~/.zshrc`) to add `uv` and `uvx` to your `PATH`.

---

## 3. Running with SSE (Server-Sent Events) Transport

By default, the server uses standard input/output (`stdio`) transport, which is what Claude Desktop and Claude Code expect. However, if you are connecting from other MCP clients or hosting the server over a network, you can run it using **SSE transport**.

When using SSE, the server binds to a local IP and port (defaulting to `127.0.0.1:8000`).

### Basic SSE Command (Auto-browser fallback)
```bash
uvx --with "duckduckgo-mcp-server[browser]" duckduckgo-mcp-server --transport sse --fetch-backend auto
```

### Advanced SSE Command (Custom Host, Port, and strict browser mode)
If you need to bind the server to a specific interface or port (e.g., to access it from another machine):
```bash
uvx --with "duckduckgo-mcp-server[browser]" duckduckgo-mcp-server \
  --transport sse \
  --host 0.0.0.0 \
  --port 7070 \
  --fetch-backend curl
```

---

## 4. Stdio Command Reference (Claude Desktop / Claude Code)

For local client integrations using standard I/O:

### Option A: Automatic Fallback Mode (Recommended)
This tries the standard `httpx` client first and automatically switches to browser TLS impersonation (`curl`) if the site blocks requests (e.g., returns `403` or a Cloudflare challenge):
```bash
uvx --with "duckduckgo-mcp-server[browser]" duckduckgo-mcp-server --fetch-backend auto
```

### Option B: Strict Browser Mode
This forces the server to use browser TLS impersonation (`curl`) for *every* page fetch:
```bash
uvx --with "duckduckgo-mcp-server[browser]" duckduckgo-mcp-server --fetch-backend curl
```

---

## 5. Configuring Claude Desktop

To integrate the DuckDuckGo search and fetch capabilities directly into Claude Desktop, add the server to your configuration file.

### Configuration File Locations
* **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
* **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

### Configuration Snippet
Add the following to your `mcpServers` object:

```json
{
  "mcpServers": {
    "duckduckgo": {
      "command": "uvx",
      "args": [
        "--with",
        "duckduckgo-mcp-server[browser]",
        "duckduckgo-mcp-server",
        "--fetch-backend",
        "auto"
      ]
    }
  }
}
```

---

## 6. Environment Variables (Optional)

You can customize search behavior using environment variables:

| Variable | Values | Description |
| :--- | :--- | :--- |
| `DDG_SAFE_SEARCH` | `STRICT` \| `MODERATE` (Default) \| `OFF` | Control SafeSearch filter |
| `DDG_REGION` | e.g., `us-en`, `uk-en`, `jp-ja` | Standard regional locale code |

Example running manually with custom environment settings:
```bash
DDG_SAFE_SEARCH=OFF DDG_REGION=us-en uvx --with "duckduckgo-mcp-server[browser]" duckduckgo-mcp-server --fetch-backend auto
```
