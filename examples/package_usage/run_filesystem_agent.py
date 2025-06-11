import os
import asyncio

from pydantic_ai.agent import Agent

from mcp_servers.filesystem import MCPServerFilesystem
from mcp_servers import load_env_vars
from examples.utils import chatify, DEFAULT_MODEL_NAME


load_env_vars()


assert os.environ.get("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY must be defined"


async def main():
    mcp_server_filesystem = MCPServerFilesystem()
    _ = await mcp_server_filesystem.start()


    system_prompt = """
    """

    agent = Agent(
        model=f"openrouter:{DEFAULT_MODEL_NAME}",
        mcp_servers=[mcp_server_filesystem.get_mcp_server_http()],
        system_prompt=system_prompt,
    )

    async with agent.run_mcp_servers():
        await chatify(agent)


if __name__ == "__main__":
    asyncio.run(main())
