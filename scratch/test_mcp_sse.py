import os
import asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client
from dotenv import load_dotenv

# Load .env
load_dotenv()

async def list_tools():
    urls_to_try = [
        os.getenv("MCP_SEARCH_URL", "http://0.0.0.0:7070/sse"),
        "http://127.0.0.1:7070/sse",
        "http://localhost:7070/sse"
    ]
    
    for mcp_url in urls_to_try:
        print(f"\nTrying to connect to MCP server at: {mcp_url} ...")
        try:
            async with sse_client(mcp_url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    print(f"SUCCESS! Connected to {mcp_url}")
                    print("Available MCP Tools on server:")
                    for tool in tools.tools:
                        print(f" - {tool.name}: {tool.description}")
                    return
        except Exception as e:
            print(f"Failed to query {mcp_url}: {e}")

if __name__ == "__main__":
    asyncio.run(list_tools())
