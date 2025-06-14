import os
import sys
import argparse
from pathlib import Path
import shutil
import secrets
import subprocess
import asyncio
import httpx
import daemon
import daemon.pidfile
import logging
import signal

import mcp_servers
from mcp_servers.filesystem import MCPServerFilesystem
from mcp_servers.brave import MCPServerBrave
from mcp_servers.searxng import MCPServerSearxng
from mcp_servers.tavily import MCPServerTavily
from mcp_servers import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_ENV_FILE,
    DEFAULT_SEARXNG_CONFIG_DIR,
    DEFAULT_SEARXNG_SETTINGS_FILE,
    load_env_vars,
)

load_env_vars()


def initialize_config(subcommand: str, force: bool):
    if not subcommand:
        subcommand = "all"

    if force and subcommand == "all":
        if DEFAULT_CONFIG_DIR.exists():
            print(f"Force removing tree: {DEFAULT_CONFIG_DIR}")
            shutil.rmtree(DEFAULT_CONFIG_DIR)
    else:
        print(f"Skipped removing tree: {DEFAULT_CONFIG_DIR}")

    os.makedirs(DEFAULT_CONFIG_DIR, exist_ok=True)

    if force and subcommand in ["all", "env"]:
        if DEFAULT_ENV_FILE.exists():
            print(f"Deleting {DEFAULT_ENV_FILE}")
            DEFAULT_ENV_FILE.unlink()
    else:
        print(f"Skipped removing {DEFAULT_ENV_FILE}")

    if not DEFAULT_ENV_FILE.exists() and subcommand in ["all", "env"]:
        print(f"Creating {DEFAULT_ENV_FILE}")
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
    else:
        print("Skipped init for env")

    if force and subcommand in ["all", "searxng"]:
        if DEFAULT_SEARXNG_CONFIG_DIR.exists():
            print(f"Force removed tree: {DEFAULT_SEARXNG_CONFIG_DIR}")
            shutil.rmtree(DEFAULT_CONFIG_DIR)
    else:
        print(f"Skipped removing tree: {DEFAULT_SEARXNG_CONFIG_DIR}")

    os.makedirs(DEFAULT_SEARXNG_CONFIG_DIR, exist_ok=True)

    if not DEFAULT_SEARXNG_SETTINGS_FILE.exists() and subcommand in ["all", "searxng"]:
        with open(DEFAULT_SEARXNG_SETTINGS_FILE, "w") as f:
            f.write(f"""
use_default_settings: true

server:
  secret_key: {secrets.token_hex(32)}
  limiter: false

search:
  formats:
    - html
    - json

engines:
  - name: startpage
    disabled: true
            """)
    else:
        print("Skipped init for searxng")


def check_container_command_exists(command):
    """Check if a command exists and is executable."""
    return shutil.which(command) is not None


def get_container_tool():
    """Determine which container tool (podman or docker) is available."""
    if check_container_command_exists("podman"):
        return "podman"
    elif check_container_command_exists("docker"):
        return "docker"
    else:
        print("Error: Neither podman nor docker is installed or executable.")
        sys.exit(1)


def run_searxng_container_command():
    """Execute the container run command using podman or docker."""
    container_tool = get_container_tool()

    searxng_base_url = os.getenv("SEARXNG_BASE_URL")
    if not searxng_base_url:
        raise ValueError(f"SEARXNG_BASE_URL env var must be set in {DEFAULT_ENV_FILE}")

    # Define the container run command
    command = [
        container_tool,
        "run",
        "-d",
        "--name",
        "searxng-local",
        "-p",
        f"{str(os.environ['SEARXNG_BASE_URL']).replace('http://', '')}:8080",
        "-v",
        f"{os.path.expanduser('~/.mcp_servers/searxng_config')}:/etc/searxng:Z",
        "-e",
        f"SEARXNG_BASE_URL={str(os.getenv('SEARXNG_BASE_URL'))}",
        "-e",
        "SEARXNG_LIMITER=false",
        "docker.io/searxng/searxng",
    ]

    # Execute the command
    try:
        result = subprocess.run(command, check=True, text=True, capture_output=True)
        print(f"Container started successfully using {container_tool}.")
        print(f"Output: {result.stdout}")
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to run container with {container_tool}.")
        print(f"Error message: {e.stderr}")
        sys.exit(1)


def stop_searxng_container_command():
    """Stop and remove the searxng-local container."""
    container_tool = get_container_tool()

    # Stop the container
    stop_command = [container_tool, "stop", "searxng-local"]
    try:
        result = subprocess.run(
            stop_command, check=True, text=True, capture_output=True
        )
        print(f"Container stopped successfully using {container_tool}.")
        print(f"Output: {result.stdout}")
    except subprocess.CalledProcessError as e:
        if "no such container" in e.stderr.lower():
            print("Container searxng-local does not exist or is already stopped.")
        else:
            print(f"Error: Failed to stop container with {container_tool}.")
            print(f"Error message: {e.stderr}")
        # Continue to attempt removal even if stop fails (e.g., container already stopped)

    # Remove the container
    rm_command = [container_tool, "rm", "searxng-local"]
    try:
        result = subprocess.run(rm_command, check=True, text=True, capture_output=True)
        print(f"Container removed successfully using {container_tool}.")
        print(f"Output: {result.stdout}")
    except subprocess.CalledProcessError as e:
        if "no such container" in e.stderr.lower():
            print("Container searxng-local does not exist or is already removed.")
        else:
            print(f"Error: Failed to remove container with {container_tool}.")
            print(f"Error message: {e.stderr}")
            sys.exit(1)


def run_external_container(container: str):
    if container == "searxng":
        run_searxng_container_command()
    else:
        raise NotImplementedError(container)


def stop_external_container(container: str):
    if container == "searxng":
        stop_searxng_container_command()
    else:
        raise NotImplementedError(container)


async def start_server(args):
    """Main entry point for the MCPServer CLI application."""
    # Handle the 'start' command
    if args.command == "start":
        if args.server == "filesystem":
            # Set environment variables if provided
            if args.allowed_dir:
                os.environ["MCP_SERVER_FILESYSTEM_ALLOWED_DIR"] = str(
                    Path(args.allowed_dir).expanduser().resolve()
                )
            if args.host:
                os.environ["MCP_SERVER_FILESYSTEM_HOST"] = args.host
            if args.port:
                os.environ["MCP_SERVER_FILESYSTEM_PORT"] = str(args.port)

            server = MCPServerFilesystem()
            try:
                server_task = await server.start()
                await server_task
            except KeyboardInterrupt:
                print("\nServer shutting down...")
                await server.stop()
                sys.exit(0)
        elif args.server == "brave":
            assert os.getenv("BRAVE_API_KEY"), "BRAVE_API_KEY must be set"

            if args.host:
                os.environ["MCP_SERVER_BRAVE_HOST"] = args.host
            if args.port:
                os.environ["MCP_SERVER_BRAVE_PORT"] = str(args.port)

            server = MCPServerBrave()
            try:
                server_task = await server.start()
                await server_task
            except KeyboardInterrupt:
                print("\nServer shutting down...")
                await server.stop()
                sys.exit(0)
        elif args.server == "searxng":
            assert os.getenv("SEARXNG_BASE_URL"), "SEARXNG_BASE_URL must be set"

            if args.host:
                os.environ["MCP_SERVER_SEARXNG_HOST"] = args.host
            if args.port:
                os.environ["MCP_SERVER_SEARXNG_PORT"] = str(args.port)

            server = MCPServerSearxng()
            try:
                server_task = await server.start()
                await server_task
            except KeyboardInterrupt:
                print("\nServer shutting down...")
                await server.stop()
                sys.exit(0)
        elif args.server == "tavily":
            if args.host:
                os.environ["MCP_SERVER_TAVILY_HOST"] = args.host
            if args.port:
                os.environ["MCP_SERVER_TAVILY_PORT"] = str(args.port)

            server = MCPServerTavily()
            try:
                server_task = await server.start()
                await server_task
            except KeyboardInterrupt:
                print("\nServer shutting down...")
                await server.stop()
                sys.exit(0)
        else:
            raise ValueError(f"Unknown server type: {args.server}")


def stop_server(server: str, port: str):
    """Stop the running daemonized server."""

    base_file_name = f"mcp_server_{server}"
    if port:
        base_file_name = base_file_name + "_" + str(port)

    pid_filename = "/tmp/" + base_file_name + ".pid"
    out_filename = "/tmp/" + base_file_name + ".out"
    err_filename = "/tmp/" + base_file_name + ".err"
    print(pid_filename)
    print(out_filename)
    print(err_filename)
    if not os.path.exists(pid_filename):
        print("Error: No running server found (PID file does not exist).")
        os.remove(out_filename)
        os.remove(err_filename)
        sys.exit(1)

    try:
        with open(pid_filename, "r") as f:
            pid = int(f.read().strip())
    except (IOError, ValueError) as e:
        print(f"Error reading PID file: {e}")
        os.remove(out_filename)
        os.remove(err_filename)
        sys.exit(1)

    # Check if process is running
    try:
        os.kill(pid, 0)  # Check if process exists
    except OSError:
        print("Error: No process found with PID {pid}. Removing stale PID file.")
        os.remove(pid_filename)
        os.remove(out_filename)
        os.remove(err_filename)
        sys.exit(1)

    # Send SIGTERM to stop the server
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent shutdown signal to server (PID: {pid}).")
        os.remove(pid_filename)
        os.remove(out_filename)
        os.remove(err_filename)
    except OSError as e:
        print(f"Error sending shutdown signal: {e}")
        sys.exit(1)


def setup_logging(server: str):
    """Set up logging for the daemon process."""
    logging.basicConfig(
        filename=f"/tmp/mcp_server_{server}.log",
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
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


def check_existing_server(pid_file: str):
    """Check if a server of the given type is already running."""
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                pid = int(f.read().strip())
            # Check if process is running
            os.kill(pid, 0)  # Raises OSError if process doesn't exist
            print(
                f"Error: A server is already running with PID {pid}. Stop it first using 'stop --server {{server}}'."
            )
            sys.exit(1)
        except (IOError, ValueError) as e:
            print(f"Error reading PID file: {e}. Removing stale PID file.")
            os.remove(pid_file)


def main():
    """Parse arguments and decide whether to run in foreground or daemon mode."""
    parser = argparse.ArgumentParser(
        description="Command line interface for MCP Server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    subparsers.required = True

    # Add 'start' command
    start_parser = subparsers.add_parser("start", help="Start an MCP server")
    start_parser.add_argument(
        "--server",
        choices=[
            "filesystem",
            "brave",
            "searxng",
            "tavily",
        ],
        required=True,
        help="Type of server to start",
    )
    start_parser.add_argument(
        "--allowed-dir",
        type=str,
        help="Directory to use as the root for file operations",
    )
    start_parser.add_argument(
        "--host", type=str, help="Host address to bind the server to"
    )
    start_parser.add_argument("--port", type=int, help="Port to run the server on")
    start_parser.add_argument(
        "--detach", action="store_true", help="Run the server in detached (daemon) mode"
    )

    stop_parser = subparsers.add_parser("stop", help="Stop a running MCP server")
    stop_parser.add_argument(
        "--server",
        choices=[
            "filesystem",
            "brave",
            "searxng",
            "tavily",
        ],
        required=True,
        help="Type of server to start",
    )
    stop_parser.add_argument("--port", type=int, help="Port to stop the server on")

    init_parser = subparsers.add_parser("init", help="Stop a running MCP server")
    init_parser.add_argument(
        "--force",
        action="store_true",
        help=f"Force to overwrite entire {DEFAULT_CONFIG_DIR}",
    )
    init_subparser = init_parser.add_subparsers(dest="subcommand")
    init_env_parser = init_subparser.add_parser("env", help="Initialize .env")
    init_env_parser.add_argument(
        "--force",
        action="store_true",
        help=f"Force to overwrite {DEFAULT_ENV_FILE}",
    )
    init_searxng_parser = init_subparser.add_parser(
        "searxng", help="Initialize searxng config files"
    )
    init_searxng_parser.add_argument(
        "--force",
        action="store_true",
        help=f"Force to overwrite entire {DEFAULT_SEARXNG_CONFIG_DIR}",
    )

    run_external_container_parser = subparsers.add_parser(
        "run_external_container", help="Run external container via podman or docker"
    )
    run_external_container_parser.add_argument(
        "--container",
        choices=[
            "searxng",
        ],
        required=True,
        help="Type of server to start",
    )

    stop_external_container_parser = subparsers.add_parser(
        "stop_external_container", help="Stop external container via podman or docker"
    )
    stop_external_container_parser.add_argument(
        "--container",
        choices=[
            "searxng",
        ],
        required=True,
        help="Type of server to start",
    )

    # Parse the arguments
    args = parser.parse_args()

    if args.command == "start":
        if args.detach:
            # Run in daemon mode
            base_file_name = f"mcp_server_{args.server}"
            if args.port:
                base_file_name = base_file_name + "_" + str(args.port)

            pid_filename = "/tmp/" + base_file_name + ".pid"
            out_filename = "/tmp/" + base_file_name + ".out"
            err_filename = "/tmp/" + base_file_name + ".err"

            check_existing_server(pid_filename)

            pidfile = daemon.pidfile.TimeoutPIDLockFile(pid_filename)
            with daemon.DaemonContext(
                pidfile=pidfile,
                stdout=open(out_filename, "w"),
                stderr=open(err_filename, "w"),
                detach_process=True,
            ):
                daemon_main(args)
        else:
            # Run in foreground
            asyncio.run(start_server(args))
    elif args.command == "stop":
        stop_server(args.server, args.port)
    elif args.command == "init":
        initialize_config(args.subcommand, args.force)
    elif args.command == "run_external_container":
        run_external_container(args.container)
    elif args.command == "stop_external_container":
        stop_external_container(args.container)
