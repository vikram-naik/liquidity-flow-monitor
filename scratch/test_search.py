import asyncio
import os
from mcp import ClientSession
from mcp.client.sse import sse_client

async def test_search():
    mcp_url = "http://127.0.0.1:7070/sse"
    print(f"Connecting to MCP server at {mcp_url}...")
    
    try:
        async with sse_client(mcp_url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                queries = [
                    # 1. Simple search
                    "POWERGRID stock price news",
                    # 2. Precise query with quotes and site
                    'POWERGRID "block deal" site:nseindia.com',
                    # 3. Simple query with site only
                    "POWERGRID site:trendlyne.com",
                ]
                
                for idx, query in enumerate(queries):
                    print(f"\n--- TEST {idx + 1}: Query: '{query}' ---")
                    try:
                        # Sleep to avoid rapid rate-limiting
                        await asyncio.sleep(2.0)
                        
                        result = await session.call_tool(
                            name="search", 
                            arguments={"query": query, "max_results": 3}
                        )
                        
                        print(f"Result count: {len(result.content)}")
                        for content in result.content:
                            if getattr(content, "type", None) == "text":
                                snippet = content.text[:500]
                                print(f"Snippet:\n{snippet}\n...")
                    except Exception as err:
                        print(f"Query '{query}' failed: {err}")
                        
    except Exception as e:
        print(f"Failed to connect to MCP: {e}")

if __name__ == "__main__":
    asyncio.run(test_search())
