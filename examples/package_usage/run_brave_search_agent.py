import os
import asyncio

from pydantic_ai.agent import Agent

from mcp_servers.brave import MCPServerBrave
from mcp_servers import load_env_vars
from examples.utils import chatify, DEFAULT_MODEL_NAME


load_env_vars()


assert os.environ.get("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY must be defined"


async def main():
    mcp_server_brave = MCPServerBrave()
    _ = await mcp_server_brave.start()

    system_prompt = """
        You are an brave search AI agent. You are allowed use MCP tools to perform web search.

        - Always generate a maningful query for brave search and perform web search, obtain links and content.
        - Provide necessary answer to users' questions
        - Provide all web links you used to answer users question as a bullet list at the end of your answers.
        - If you need to perform simultaneous brave search calls, wait 1 second between each call

        Your typical answer template:
        <Search Topic Info>

        <Answer to users' question>

        <Citations>
    """

    agent = Agent(
        model=f"openrouter:{DEFAULT_MODEL_NAME}",
        mcp_servers=[mcp_server_brave.get_mcp_server_http()],
        system_prompt=system_prompt,
    )

    async with agent.run_mcp_servers():
        await chatify(agent)


if __name__ == "__main__":
    asyncio.run(main())
