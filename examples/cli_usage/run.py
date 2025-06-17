import os
import asyncio

from pydantic_ai.agent import Agent
from pydantic_ai.mcp import MCPServerStreamableHTTP

from mcp_servers import load_env_vars
from examples.utils import chatify, DEFAULT_MODEL_NAME


load_env_vars()


assert os.environ.get("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY must be defined"


async def main():
    mcp_servers = [
        MCPServerStreamableHTTP(
            "http://127.0.0.1:8765/mcp"
        ),  # CLI: mcpserver start --server filesystem --port 8765
        MCPServerStreamableHTTP(
            "http://127.0.0.1:8766/mcp"
        ),  # CLI: mcpserver start --server brave      --port 8766
        MCPServerStreamableHTTP(
            "http://127.0.0.1:8767/mcp"
        ),  # CLI: mcpserver start --server searxng    --port 8767  # this one also require: mcpserver run_external_container --container searxng
        MCPServerStreamableHTTP(
            "http://127.0.0.1:8768/mcp"
        ),  # CLI: mcpserver start --server tavily     --port 8768
    ]
    mcp_servers_streamable_https = mcp_servers

    system_prompt = """
    """

    agent = Agent(
        model=f"openrouter:{DEFAULT_MODEL_NAME}",
        mcp_servers=mcp_servers_streamable_https,
        system_prompt=system_prompt,
    )

    async with agent.run_mcp_servers():
        _ = await chatify(agent)
        return


if __name__ == "__main__":
    asyncio.run(main())
