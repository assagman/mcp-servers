import os
import asyncio
from dotenv import load_dotenv

from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.agent import Agent

from mcp_servers.filesystem import MCPServerFilesystem


load_dotenv()


assert os.environ.get("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY must be defined"
assert os.environ.get("MCP_SERVER_FILESYSTEM_HOST"), "MCP_SERVER_FILESYSTEM_HOST must be defined"
assert os.environ.get("MCP_SERVER_FILESYSTEM_PORT"), "MCP_SERVER_FILESYSTEM_PORT must be defined"


async def main():
    # Instantiate the server
    mcp_server_filesystem = MCPServerFilesystem()
    await mcp_server_filesystem.run()
    #
    model = OpenAIModel(
        model_name="google/gemini-2.5-flash-preview",
        provider=OpenAIProvider(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        ),
    )

    system_prompt = f"""
        You are an filesystem AI agent. You are allowed to operate on one directory which is `the` working directory that you must retrieve it via MCP tool. It's also referred as CWD, current working directory.

        Always be aware of the CWD. Retrieve it, Analyze it, Be ready for user's questions about CWD.

        Always treat users questions as regarding CWD. You don't answer questions irrelevant from CWD content.

        Always use MCP tools to answer users questions. You are capable of combining/chaining MCP tools to achieve goals. You can list files while considering gitignore via reading it's content. You can count total import counts by looking at every single file in CWD.
    """

    agent = Agent(
        model,
        mcp_servers=[mcp_server_filesystem.get_mcp_server_http()],
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
