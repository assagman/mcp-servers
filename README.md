# mcp_servers
A collection of MCP servers

## Development

`git clone git@github.com:assagman/mcp_servers.git`

`cd mcp_servers`

`uv venv --python 3.12 && source .venv/bin/activate`

`uv sync --extra dev`

## Example usage

make your shell recognize `mcp_server` folder as top-level package:
`export PYTHONPATH=$PYTHONPATH:$(pwd)`

Set your OPENROUTER_API_KEY in examples/.env file, then execute:
`python examples/run_filesystem_agent.py`
