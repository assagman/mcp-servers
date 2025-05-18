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

Set on OPENROUTER_API_KEY: `cp ./examples/.env.example ./examples/.env` then
set your OPENROUTER_API_KEY in `./examples/.env`. Then execute:

### Example usage: as a package in client code

`python examples/run_filesystem_agent.py`

without making any changes, this command should be executed successfully as is, and it'll
operate on a temporary directory.

### Example usage: as a separate, dedicated MCP server

`mcpserver start --server filesystem --port 8765 --allowed-dir $(pwd) --detach`

Above command should start _MCP server for filesystem_ in the background process. `ps aux | rg mcpserver`
is expected to show this related process.

`python examples/run_filesystem_agent_2.py`

after some experimentation, please run:

`mcpserver stop --server filesystem`

This specific example uses CLI app utility to start/stop MCP seperately from the client code
(different from the first example).
