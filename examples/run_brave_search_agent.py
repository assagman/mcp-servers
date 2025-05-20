import os
import asyncio
from pathlib import Path
from dotenv import load_dotenv

from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.agent import Agent
from pydantic_ai.mcp import MCPServerHTTP

from mcp_servers import load_env

load_env()


assert os.environ.get("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY must be defined"

if not os.environ.get("MCP_SERVER_BRAVE_SEARCH_HOST"):
    os.environ['MCP_SERVER_BRAVE_SEARCH_HOST'] = "0.0.0.0"
if not os.environ.get("MCP_SERVER_BRAVE_SEARCH_PORT"):
    os.environ['MCP_SERVER_BRAVE_SEARCH_PORT'] = "8766"

async def main():
    # Instantiate the server
    mcp_server_filesystem = MCPServerHTTP(f"http://{os.environ['MCP_SERVER_BRAVE_SEARCH_HOST']}:{os.environ['MCP_SERVER_BRAVE_SEARCH_PORT']}/sse")
    #
    model = OpenAIModel(
        model_name="google/gemini-2.5-flash-preview",
        provider=OpenAIProvider(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        ),
    )

    system_prompt = f"""
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
        model,
        mcp_servers=[mcp_server_filesystem],
        system_prompt=system_prompt,
    )

    async with agent.run_mcp_servers():
        result = None
        while True:
            # Call a tool on the server
            message_history = []
            if result:
                message_history = result.all_messages()
            result = await agent.run(input('[USER]: '), message_history=message_history)
            print(result.output)
            print()
            print()



if __name__ == "__main__":
    asyncio.run(main())
