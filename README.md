# mcp-servers

A Python package providing a collection of Model-Control-Protocol (MCP) servers and a CLI tool to manage them efficiently.

## Disclaimer

This project is created for personal use and does not guarantee stable behavior. It is made public solely as a reference for other programmers. The project is currently in early development, potentially unstable, and may produce undesired outcomes. Use at your own risk.

---

## Overview

mcp-servers implements Model-Control-Protocol servers for various integrations:
- Filesystem access
- Brave Search integration
- SearXNG search integration
- Tavily Search integration

These servers can be used by AI agents to interact with your system and external services in a controlled manner.

## Pre-requisites

- [x] Python 3.12+
- [x] `uv` (optional but recommended package/environment manager)
- [x] `podman` or `docker` for container operations (e.g., running local SearXNG instance)
- [x] OpenRouter API key and credits (for experimentation with examples)

## Installation

### Package Managers

#### Using uv (recommended)
```sh
uv venv --python 3.12
source .venv/bin/activate
uv pip install --upgrade mcp-servers
```

#### Using pip
```sh
pip install --upgrade mcp-servers
```

### Development Setup
```sh
git clone git@github.com:assagman/mcp_servers.git
cd mcp_servers
uv venv --python 3.12
source .venv/bin/activate
uv sync --extra dev
```

## Configuration

The package requires specific configuration files in your home directory. Initialize everything at once with:

```sh
mcpserver init
```

This command will:
- Create `~/.mcp_servers/.env` → MCP Server HOST:PORTs, API KEYs, URLs, etc.
- Create `~/.mcp_servers/searxng_config/settings.yml` → Configuration for local SearXNG instance

⚠️ **Important**: You must set your own API keys in the generated `.env` file. They are left empty by default.

For environment variable reference, see [.env.example](https://github.com/assagman/mcp_servers/blob/main/.env.example)

## Usage

### CLI Tool

This package provides a CLI tool (`mcpserver`) to manage configuration, MCP servers, and external container operations:

```sh
mcpserver -h  # Show help
```

#### Server Management

Each MCP server can be started in standard mode or detached mode. Detached mode runs the server in the background.

##### Filesystem Server

```sh
# Start with temporary directory
mcpserver start --server filesystem

# Operate on specific directory
mcpserver start --server filesystem --allowed-dir $(pwd)

# Custom port
mcpserver start --server filesystem --port 8765 --allowed-dir $(pwd)

# Detached mode
mcpserver start --server filesystem --detached
mcpserver stop --server filesystem  # Stop detached server
```

##### Brave Search Server

Requires `BRAVE_API_KEY` environment variable.

```sh
# Start server
mcpserver start --server brave

# Custom port
mcpserver start --server brave --port 8766

# Detached mode
mcpserver start --server brave --detached
mcpserver stop --server brave  # Stop detached server
```

##### SearXNG Search Server

Requires `SEARXNG_BASE_URL` environment variable.

```sh
# Start local SearXNG container
mcpserver run_external_container --container searxng

# Start server
mcpserver start --server searxng

# Custom port
mcpserver start --server searxng --port 8767

# Detached mode
mcpserver start --server searxng --detached
mcpserver stop --server searxng  # Stop detached server

# Stop SearXNG container
mcpserver stop_external_container --container searxng
```

##### Tavily Search Server

Requires `TAVILY_API_KEY` environment variable.

```sh
# Start server
mcpserver start --server tavily

# Custom port
mcpserver start --server tavily --port 8768

# Detached mode
mcpserver start --server tavily --detached
mcpserver stop --server tavily  # Stop detached server
```

### Python API

The package can also be imported and used programmatically. See example
files in [examples/package_usage](https://github.com/assagman/mcp_servers/blob/main/examples/package_usage)

## Examples

The package includes examples demonstrating Agent-MCP Server usage with `pydantic_ai` Agents. All examples use `MCPServerHTTP` to connect agents with MCP Servers.

To experiment with all MCP servers:

1. Set `BRAVE_API_KEY` in `~/.mcp_servers/.env` (for Brave search server)
2. Set `OPENROUTER_API_KEY` in `~/.mcp_servers/.env` (required for all examples)
3. Start SearXNG container (for SearXNG search server)

### CLI Usage Examples

See [examples/cli_usage](https://github.com/assagman/mcp_servers/blob/main/examples/cli_usage) for examples requiring MCP servers to be started via the CLI commands mentioned above.

### Package Usage Examples

See [examples/package_usage](https://github.com/assagman/mcp_servers/blob/main/examples/package_usage) for examples that can be executed as-is.

## Advanced Usage

### Custom Configuration

You can customize server behavior by modifying configuration files:

```sh
# Edit SearXNG settings
vim ~/.mcp_servers/searxng_config/settings.yml

# Edit environment variables
vim ~/.mcp_servers/.env
```

### Multiple Servers

You can run multiple MCP servers simultaneously by specifying different ports:

```sh
mcpserver start --server filesystem --port 8765 --detached
mcpserver start --server brave --port 8766 --detached
mcpserver start --server searxng --port 8767 --detached
mcpserver start --server tavily --port 8768 --detached
```

## Troubleshooting

### Common Issues

1. **API Keys Not Working**: Ensure you've set the correct API keys in `~/.mcp_servers/.env`
2. **Port Conflicts**: If a port is already in use, specify a different port with `--port`
3. **Container Issues**: Use `podman logs searxng` or `docker logs searxng` to diagnose SearXNG container problems

## Testing Specifications

- Tested on macOS (arm64)
- Python 3.12

## License

This project is provided as-is with no warranty. See the [LICENSE](https://github.com/assagman/mcp_servers/blob/main/LICENSE) file for details.

## Contributing

Contributions are welcome but not expected. If you find a bug or have a feature request, please consider forking this repo and use your own custom version.
