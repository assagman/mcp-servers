import os
import asyncio

from pydantic_ai.agent import Agent
from pydantic_ai.mcp import MCPServerHTTP

from mcp_servers import load_env_vars
from examples.utils import chatify, DEFAULT_MODEL_NAME


load_env_vars()


assert os.environ.get("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY must be defined"
if not os.environ.get("MCP_SERVER_FILESYSTEM_HOST"):
    os.environ["MCP_SERVER_FILESYSTEM_HOST"] = "0.0.0.0"
if not os.environ.get("MCP_SERVER_FILESYSTEM_PORT"):
    os.environ["MCP_SERVER_FILESYSTEM_PORT"] = "8765"


async def main():
    mcp_server_filesystem = MCPServerHTTP(
        f"http://{os.environ['MCP_SERVER_FILESYSTEM_HOST']}:{os.environ['MCP_SERVER_FILESYSTEM_PORT']}/sse"
    )

    system_prompt = """
    """

    agent = Agent(
        model=f"openrouter:{DEFAULT_MODEL_NAME}",
        mcp_servers=[mcp_server_filesystem],
        system_prompt=system_prompt,
    )

    async with agent.run_mcp_servers():
        await chatify(agent)


if __name__ == "__main__":
    asyncio.run(main())
