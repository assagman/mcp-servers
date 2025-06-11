import os
import asyncio
from pathlib import Path

from pydantic_ai.agent import Agent

from mcp_servers.filesystem import MCPServerFilesystem
from mcp_servers.brave_search import MCPServerBraveSearch
from mcp_servers.searxng_search import MCPServerSearxngSearch
from mcp_servers.tavily_search import MCPServerTavilySearch

from mcp_servers import load_env_vars
from examples.utils import chatify, DEFAULT_MODEL_NAME


load_env_vars()


assert os.environ.get("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY must be defined"


async def main():
    mcp_server_filesystem = MCPServerFilesystem(host="localhost", port=8000, allowed_dir=Path.cwd())
    _ = await mcp_server_filesystem.start()

    mcp_server_brave_search = MCPServerBraveSearch(host="localhost", port=8001)
    _ = await mcp_server_brave_search.start()

    mcp_server_searxng_search = MCPServerSearxngSearch(host="localhost", port=8002)
    _ = await mcp_server_searxng_search.start()

    mcp_server_tavily_search = MCPServerTavilySearch(host="localhost", port=8003)
    _ = await mcp_server_tavily_search.start()

    system_prompt = """
    """

    agent = Agent(
        model=f"openrouter:{DEFAULT_MODEL_NAME}",
        mcp_servers=[
            mcp_server_filesystem.get_mcp_server_http(),
            mcp_server_brave_search.get_mcp_server_http(),
            mcp_server_searxng_search.get_mcp_server_http(),
            mcp_server_tavily_search.get_mcp_server_http(),
        ],
        system_prompt=system_prompt,
    )

    async with agent.run_mcp_servers():
        await chatify(agent)


if __name__ == "__main__":
    asyncio.run(main())
