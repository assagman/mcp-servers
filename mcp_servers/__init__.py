"""MCP (Model Context Protocol) servers"""

__version__ = "0.1.8"


from typing import Optional
from pathlib import Path

from dotenv import load_dotenv

from mcp_servers.logger import MCPServersLogger


DEFAULT_CONFIG_DIR = Path("~/.mcp_servers").expanduser().resolve()
DEFAULT_ENV_FILE = DEFAULT_CONFIG_DIR / ".env"
DEFAULT_SEARXNG_CONFIG_DIR = DEFAULT_CONFIG_DIR / "searxng_config"
DEFAULT_SEARXNG_SETTINGS_FILE = DEFAULT_SEARXNG_CONFIG_DIR / "settings.yml"

_logger = MCPServersLogger.get_logger(__name__)


def load_env_vars(dotenv_path: Optional[str] = str(DEFAULT_ENV_FILE)) -> None:
    """Loads environment variables from a .env file."""
    loaded = load_dotenv(dotenv_path=dotenv_path, override=True)
    if loaded:
        _logger.info(f".env file loaded successfully from {dotenv_path or 'default location'}.")
    else:
        _logger.warning("No .env file found or it was empty. Please consider running `mcpserver init`")
