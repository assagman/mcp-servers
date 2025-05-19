import os
import sys
import time
import argparse
from pathlib import Path
import shutil
import asyncio
import httpx
import daemon
import daemon.pidfile
import logging
import signal

from dotenv import load_dotenv

import mcp_servers
from mcp_servers.filesystem import MCPServerFilesystem
from mcp_servers.brave_search import MCPServerBraveSearch
from mcp_servers.searxng_search import MCPServerSearXNG

DEFAULT_CONFIG_DIR=Path("~/.mcp_servers").expanduser().resolve()
DEFAULT_ENV_FILE = DEFAULT_CONFIG_DIR / ".env"
load_dotenv(DEFAULT_ENV_FILE)


def initialize_config(force: bool):
    if force:
        shutil.rmtree(DEFAULT_CONFIG_DIR)

    os.makedirs(DEFAULT_CONFIG_DIR, exist_ok=True)

    if not DEFAULT_ENV_FILE.exists():
        url = f"https://raw.githubusercontent.com/assagman/mcp_servers/refs/tags/v{mcp_servers.__version__}/.env.example"

        try:
            with httpx.Client() as client:
                response = client.get(url)
            response.raise_for_status()
            DEFAULT_ENV_FILE.write_text(response.text)
            print(f"Example environment variable file written to {DEFAULT_ENV_FILE}")

        except httpx.HTTPError as e:
            print(f"Error fetching the file: {e}")
        except OSError as e:
            print(f"Error writing to file: {e}")

    # searxng
    searxng_config_dir = DEFAULT_CONFIG_DIR / "searxng_config"
    os.makedirs(searxng_config_dir, exist_ok=True)

    searxng_settings_file = searxng_config_dir / "settings.yml"
    if not searxng_settings_file.exists():
        with open(searxng_settings_file, "w") as f:
            f.write(f"""
use_default_settings: true

search:
  formats:
    - html
    - json

server:
  limiter: false
            """)


async def start_server(args):
    """Main entry point for the MCPServer CLI application."""
    # Handle the 'start' command
    if args.command == "start":
        if args.server == "filesystem":
            # Set environment variables if provided
            if args.allowed_dir:
                os.environ["MCP_SERVER_FILESYSTEM_ALLOWED_DIR"] = str(Path(args.allowed_dir).expanduser().resolve())
            if args.host:
                os.environ["MCP_SERVER_FILESYSTEM_HOST"] = args.host
            if args.port:
                os.environ["MCP_SERVER_FILESYSTEM_PORT"] = str(args.port)

            # Start the filesystem server
            server = MCPServerFilesystem()
            try:
                server_task = await server.start()
                await server_task
            except KeyboardInterrupt:
                print("\nServer shutting down...")
                await server.stop()
                sys.exit(0)
        elif args.server == "brave_search":
            if args.host:
                os.environ["MCP_SERVER_BRAVE_SEARCH_HOST"] = args.host
            if args.port:
                os.environ["MCP_SERVER_BRAVE_SEARCH_PORT"] = str(args.port)

            # Start the brave_search server
            server = MCPServerBraveSearch()
            try:
                server_task = await server.start()
                await server_task
            except KeyboardInterrupt:
                print("\nServer shutting down...")
                await server.stop()
                sys.exit(0)
        else:
            raise ValueError(f"Unknown server type: {args.server}")

def stop_server(server: str):
    """Stop the running daemonized server."""
    pid_file = f"/tmp/mcp_server_{server}.pid"
    if not os.path.exists(pid_file):
        print("Error: No running server found (PID file does not exist).")
        sys.exit(1)

    try:
        with open(pid_file, "r") as f:
            pid = int(f.read().strip())
    except (IOError, ValueError) as e:
        print(f"Error reading PID file: {e}")
        sys.exit(1)

    # Check if process is running
    try:
        os.kill(pid, 0)  # Check if process exists
    except OSError:
        print("Error: No process found with PID {pid}. Removing stale PID file.")
        os.remove(pid_file)
        sys.exit(1)

    # Send SIGTERM to stop the server
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent shutdown signal to server (PID: {pid}).")
    except OSError as e:
        print(f"Error sending shutdown signal: {e}")
        sys.exit(1)

def setup_logging(server: str):
    """Set up logging for the daemon process."""
    logging.basicConfig(
        filename=f"/tmp/mcp_server_{server}.log",
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    return logging.getLogger()

def daemon_main(args):
    """Main function for daemonized process."""
    logger = setup_logging(args.server)
    logger.info("Starting MCP Server in daemon mode")
    print("Starting MCP Server in daemon mode")

    # Handle graceful shutdown
    def handle_shutdown(signum, frame):
        logger.info("Received shutdown signal, stopping server")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    try:
        asyncio.run(start_server(args))
    except Exception as e:
        logger.error(f"Daemon failed: {str(e)}")
        sys.exit(1)

def check_existing_server(server: str):
    """Check if a server of the given type is already running."""
    pid_file = f"/tmp/mcp_server_{server}.pid"
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                pid = int(f.read().strip())
            # Check if process is running
            os.kill(pid, 0)  # Raises OSError if process doesn't exist
            print(f"Error: A {server} server is already running with PID {pid}. Stop it first using 'stop --server {server}'.")
            sys.exit(1)
        except (IOError, ValueError) as e:
            print(f"Error reading PID file: {e}. Removing stale PID file.")
            os.remove(pid_file)

def main():
    """Parse arguments and decide whether to run in foreground or daemon mode."""
    parser = argparse.ArgumentParser(
        description="Command line interface for MCP Server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    subparsers.required = True

    # Add 'start' command
    start_parser = subparsers.add_parser("start", help="Start an MCP server")
    start_parser.add_argument(
        "--server",
        choices=[
            "filesystem",
            "brave_search",
        ],
        required=True,
        help="Type of server to start"
    )
    start_parser.add_argument(
        "--allowed-dir",
        type=str,
        help="Directory to use as the root for file operations"
    )
    start_parser.add_argument(
        "--host",
        type=str,
        help="Host address to bind the server to"
    )
    start_parser.add_argument(
        "--port",
        type=int,
        help="Port to run the server on"
    )
    start_parser.add_argument(
        "--detach",
        action="store_true",
        help="Run the server in detached (daemon) mode"
    )

    stop_parser = subparsers.add_parser("stop", help="Stop a running MCP server")
    stop_parser.add_argument(
        "--server",
        choices=[
            "filesystem",
            "brave_search",
        ],
        required=True,
        help="Type of server to start"
    )

    init_parser = subparsers.add_parser("init", help="Stop a running MCP server")
    init_parser.add_argument(
        "--force",
        action="store_true",
        help=f"Force to overwrite entire {DEFAULT_CONFIG_DIR}",
    )


    # Parse the arguments
    args = parser.parse_args()

    if args.command == "start":
        # Check if a server of this type is already running
        check_existing_server(args.server)
        if args.detach:
            # Run in daemon mode
            pidfile = daemon.pidfile.TimeoutPIDLockFile(f"/tmp/mcp_server_{args.server}.pid")
            with daemon.DaemonContext(
                pidfile=pidfile,
                stdout=open(f"/tmp/mcp_server_{args.server}.out", "w"),
                stderr=open(f"/tmp/mcp_server_{args.server}.err", "w"),
                detach_process=True
            ):
                daemon_main(args)
        else:
            # Run in foreground
            asyncio.run(start_server(args))
    elif args.command == "stop":
        stop_server(args.server)
    elif args.command == "init":
        initialize_config(args.force)
