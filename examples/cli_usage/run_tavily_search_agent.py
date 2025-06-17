import os
import asyncio

from pydantic_ai.agent import Agent
from pydantic_ai.mcp import MCPServerHTTP

from mcp_servers import load_env_vars
from examples.utils import chatify, DEFAULT_MODEL_NAME


load_env_vars()


assert os.environ.get("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY must be defined"
if not os.environ.get("MCP_SERVER_TAVILY_HOST"):
    os.environ["MCP_SERVER_TAVILY_HOST"] = "0.0.0.0"
if not os.environ.get("MCP_SERVER_TAVILY_PORT"):
    os.environ["MCP_SERVER_TAVILY_PORT"] = "8767"


async def main():
    # Instantiate the server
    mcp_server_tavily = MCPServerHTTP(
        f"http://{os.environ['MCP_SERVER_TAVILY_HOST']}:{os.environ['MCP_SERVER_TAVILY_PORT']}/sse"
    )

    system_prompt = """
        You are an tavily search AI agent. You are allowed use MCP tools to perform web search, extraction url content, and crawling webpages recursively.

        - Always generate a maningful query for tavily search and perform web search, obtain links and extract content.
        - Provide necessary answer to users' questions
        - Provide all web links you used to answer users question as a bullet list at the end of your answers.
        - If you need to perform simultaneous tavily search calls, wait 1 second between each call
        - If you or user need the url content, use Tavily's extract feature.
        - if user ask you to deeply analyze an URL and it's sub pages, use crawl tool.

        Your typical answer template:
        <Search Topic Info>

        <Answer to users' question>

        <Citations>
    """

    agent = Agent(
        model=f"openrouter:{DEFAULT_MODEL_NAME}",
        mcp_servers=[mcp_server_tavily],
        system_prompt=system_prompt,
    )

    async with agent.run_mcp_servers():
        await chatify(agent)


if __name__ == "__main__":
    asyncio.run(main())
