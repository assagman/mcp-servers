import os
import asyncio
from pathlib import Path
from typing import List

from pydantic_ai.agent import Agent

from mcp_servers.base import AbstractMCPServer
from mcp_servers.filesystem import MCPServerFilesystem
from mcp_servers.brave import MCPServerBrave
from mcp_servers.searxng import MCPServerSearxng
from mcp_servers.tavily import MCPServerTavily

from mcp_servers import load_env_vars
from examples.utils import chatify, DEFAULT_MODEL_NAME


load_env_vars()


assert os.environ.get("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY must be defined"


async def main():
    mcp_servers: List[AbstractMCPServer] = [
        MCPServerFilesystem(host="127.0.0.1", port=8000, allowed_dir=Path.cwd()),
        MCPServerBrave(host="127.0.0.1", port=8001),
        MCPServerSearxng(host="127.0.0.1", port=8002),
        MCPServerTavily(host="127.0.0.1", port=8003),
    ]
    mcp_servers_streamable_https = [
        mcpserver.get_mcp_server_streamable_http() for mcpserver in mcp_servers
    ]

    for mcpserver in mcp_servers:
        await mcpserver.start()

    system_prompt = """
    """

    agent = Agent(
        model=f"openrouter:{DEFAULT_MODEL_NAME}",
        mcp_servers=mcp_servers_streamable_https,
        system_prompt=system_prompt,
    )

    async with agent.run_mcp_servers():
        end_signal = await chatify(agent)

    if end_signal == "<EXIT>":
        stop_tasks = [mcpserver.stop() for mcpserver in mcp_servers]
        await asyncio.gather(*stop_tasks)


if __name__ == "__main__":
    asyncio.run(main())
