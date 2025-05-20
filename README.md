# mcp_servers
A collection of MCP servers

## Disclaimer

This project is created for personal use and does not guarantee stable behavior. It is made public
solely as a reference for other programmers. The project is highly unstable, premature, and may produce
undesired outcomes, so use it at your own risk.

## Development Setup

`git clone git@github.com:assagman/mcp_servers.git`

`cd mcp_servers`

`uv venv --python 3.12 && source .venv/bin/activate`

`uv sync --extra dev`

## Examples

All LLM models are used from openrouter.ai, so on OPENROUTER_API_KEY
env var is mandatory for this project to interact with a LLM model.

`cp ./examples/.env.example ./examples/.env` then
set your OPENROUTER_API_KEY in `./examples/.env`. In these examples
`google/gemini-2.5-flash-preview` is picked as the LLM model since it's
the best fit model for this workflow I experienced. You need to buy
credits on https://openrouter.ai/settings/credits to experiment. Even
some free alternatives can work sometimes, but either they do not produce consistent
outputs or there are some problems with the hosting server like rate limits or unavailability.
Make sure you select models with `tools usage`. Generally Google and OpenAI models, even
with smaller/cheaper ones, works well.

### Example usage: as a package in client code

`python examples/run_filesystem_agent.py`

without making any changes, this command should be executed successfully as is, and it'll
operate on a temporary directory.

### Example usage: as a separate, dedicated MCP server

`mcpserver start --server filesystem --port 8765 --allowed-dir $(pwd) --detach`

Above command should start _MCP server for filesystem_ in the background process. `ps aux | rg mcpserver`
is expected to show this related process.

`python examples/run_filesystem_agent_2.py`

after some chat with agent, please run below to stop the MCP server:

`mcpserver stop --server filesystem`

This specific example uses CLI app utility to start/stop MCP seperately from the client code
(different from the first example).

### Others

#### brave_search

Create `.env` file in the top-level project directory, set BRAVE_API_KEY in it. Then excecute:

`mcpserver start --server brave_search --port 8766 --detach`

This will start the MCP Server. Let's test it with example agent:

`python examples/run_brave_search_agent.py`

This will allow user to chat with agent, using MCP server for Brave Search. After some chat,
you can stop it via:

`mcpserver stop --server brave_search`

#### searxng_search

This is very similar to brave_search, but since public searxng instances may not meet users requirements
in terms of configurations/settings like rate limiting, formats etc., it's better to run a local searxng instance
and make this MCP server use it.

To do that, `podman` or `docker` can be used to run a official searxng container. This will require a minimal searxng settings setup.
To manage all these minimal steps more easily, mcpserver CLI tool provide commands to start/stop searxng container:

`mcpserver run_external_container --container searxng`

This will require user to have SEARXNG_BASE_URL set as environment variable. You can simply set this variable in
`~/.mcp_servers/.env` file and tool will automatically pick it up, without any _sourcing_ process performed.

Confirm that the container is up-and-running (`podman ps` or `docker ps`). Then, start searxng_search MCP server:

`mcpserver start --server searxng_search --port 8767`

Now, this MCP server can be accessible by agents. Here is an example usage:

`python examples/run_searxng_search_agent.py`

after some experimentation, you can shutdown both MCP Server for searxng and the external container via:

`mcpserver stop --server searxng_search` for MCP server

`mcpserver stop_external_container --container searxng` for external searxng container


## Testing specs

- macOS - arm64
- python 3.12
