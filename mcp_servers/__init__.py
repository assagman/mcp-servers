"""MCP (Model Context Protocol) servers"""

__version__ = "0.1.4"


from pathlib import Path
DEFAULT_CONFIG_DIR = Path("~/.mcp_servers").expanduser().resolve()
DEFAULT_ENV_FILE = DEFAULT_CONFIG_DIR / ".env"
DEFAULT_SEARXNG_CONFIG_DIR = DEFAULT_CONFIG_DIR / "searxng_config"
DEFAULT_SEARXNG_SETTINGS_FILE = DEFAULT_SEARXNG_CONFIG_DIR / "settings.yml"


def load_env():
    from dotenv import load_dotenv
    load_dotenv(DEFAULT_ENV_FILE)
