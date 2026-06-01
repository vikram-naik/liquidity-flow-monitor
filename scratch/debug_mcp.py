import asyncio
import traceback
from mcp import ClientSession
from mcp.client.sse import sse_client

async def list_tools():
    try:
        async with sse_client("http://localhost:8811/sse") as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                print("Available MCP Tools on server:", tools)
    except Exception as e:
        print(f"Exception message: {e}")
        print("\nFull Traceback:")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(list_tools())
