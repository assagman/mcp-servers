import os
import asyncio
from pathlib import Path

from pydantic_ai.agent import Agent

from mcp_servers.filesystem import MCPServerFilesystem
from mcp_servers.brave import MCPServerBrave
from mcp_servers.searxng import MCPServerSearxng
from mcp_servers.tavily import MCPServerTavily

from mcp_servers import load_env_vars
from examples.utils import chatify, DEFAULT_MODEL_NAME


load_env_vars()


assert os.environ.get("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY must be defined"


async def main():
    mcp_server_filesystem = MCPServerFilesystem(
        host="localhost", port=8000, allowed_dir=Path.cwd()
    )
    _ = await mcp_server_filesystem.start()

    mcp_server_brave = MCPServerBrave(host="localhost", port=8001)
    _ = await mcp_server_brave.start()

    mcp_server_searxng = MCPServerSearxng(host="localhost", port=8002)
    _ = await mcp_server_searxng.start()

    mcp_server_tavily = MCPServerTavily(host="localhost", port=8003)
    _ = await mcp_server_tavily.start()

    system_prompt = """
    """

    agent = Agent(
        model=f"openrouter:{DEFAULT_MODEL_NAME}",
        mcp_servers=[
            mcp_server_filesystem.get_mcp_server_http(),
            mcp_server_brave.get_mcp_server_http(),
            mcp_server_searxng.get_mcp_server_http(),
            mcp_server_tavily.get_mcp_server_http(),
        ],
        system_prompt=system_prompt,
    )

    async with agent.run_mcp_servers():
        await chatify(agent)


if __name__ == "__main__":
    asyncio.run(main())
