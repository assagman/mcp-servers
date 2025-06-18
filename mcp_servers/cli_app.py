"""Command line interface for mcpserver."""

import os
import sys
import argparse
import logging
import asyncio
import signal
import shutil
import secrets
import subprocess
from typing import List, Dict, Any

import httpx
import daemon
import daemon.pidfile
import psutil

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
from mcp_servers.logger import MCPServersLogger

logger = MCPServersLogger.get_logger("mcpserver")


# Constants for file paths.  Consider making these configurable via environment variables.
PID_DIR = "/tmp"  # Or a more appropriate location like /var/run

load_env_vars()

# TODO: implement configuration loading and saving using configparser or PyYAML
# import configparser
# config = configparser.ConfigParser()


def initialize_config(subcommand: str, force: bool):
    """Initialize the MCP server configuration."""
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
            with open(DEFAULT_ENV_FILE, "w") as f:
                f.write(response.text)
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
        try:
            with open(DEFAULT_SEARXNG_SETTINGS_FILE, "w") as f:
                f.write(
                    f"""
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
                    """
                )
            print(f"Created SearXNG config file: {DEFAULT_SEARXNG_SETTINGS_FILE}")
        except OSError as e:
            print(f"Error writing to file: {e}")
    else:
        print("Skipped init for searxng")


def check_container_command_exists(command: str) -> bool:
    """Check if a command exists and is executable."""
    return shutil.which(command) is not None


def get_container_tool() -> str:
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


async def start_server(args: argparse.Namespace):
    """Main entry point for the mcpserver CLI application."""
    # Handle the 'start' command
    if args.command == "start":
        server_type = args.server  # more readable
        if server_type == "filesystem":
            server = MCPServerFilesystem(
                host=args.host, port=args.port, allowed_dir=args.allowed_dir
            )
            try:
                await server.start()
                await server.await_server_task()
            except KeyboardInterrupt:
                print("\nServer shutting down...")
                await server.stop()
                sys.exit(0)
        elif server_type == "brave":
            assert os.getenv("BRAVE_API_KEY"), "BRAVE_API_KEY must be set"

            server = MCPServerBrave(host=args.host, port=args.port)
            try:
                await server.start()
                await server.await_server_task()
            except KeyboardInterrupt:
                print("\nServer shutting down...")
                await server.stop()
                sys.exit(0)
        elif server_type == "searxng":
            assert os.getenv("SEARXNG_BASE_URL"), "SEARXNG_BASE_URL must be set"

            server = MCPServerSearxng(host=args.host, port=args.port)
            try:
                await server.start()
                await server.await_server_task()
            except KeyboardInterrupt:
                print("\nServer shutting down...")
                await server.stop()
                sys.exit(0)
        elif server_type == "tavily":
            assert os.getenv("TAVILY_API_KEY"), "TAVILY_API_KEY must be set"

            server = MCPServerTavily(host=args.host, port=args.port)
            try:
                await server.start()
                await server.await_server_task()
            except KeyboardInterrupt:
                print("\nServer shutting down...")
                await server.stop()
                sys.exit(0)
        else:
            raise ValueError(f"Unknown server type: {args.server}")


def stop_server(server: str, port: int) -> None:
    """Stop the running daemonized server."""

    base_file_name = f"mcp_server_{server}_{port}"

    pid_filename = os.path.join(PID_DIR, base_file_name + ".pid")
    out_filename = os.path.join(PID_DIR, base_file_name + ".out")
    err_filename = os.path.join(PID_DIR, base_file_name + ".err")

    logger.info(f"Attempting to stop server with PID file: {pid_filename}")

    try:
        with open(pid_filename, "r") as f:
            pid = int(f.read().strip())
    except (IOError, ValueError) as e:
        logger.error(f"Error reading PID file: {e}")
        # only remove the out and error if we read the pid file
        logger.info("Cleaning up related server process files if exist")
        if os.path.exists(out_filename):
            os.remove(out_filename)
        if os.path.exists(err_filename):
            os.remove(err_filename)
        sys.exit(1)

    # Check if process is running
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        logger.info(f"Sent shutdown signal to server (PID: {pid}).")
    except psutil.NoSuchProcess:
        logger.warning(f"Error: No process found with PID {pid}.")
        logger.info("Removing stale server process files if exist.")
        if os.path.exists(pid_filename):
            os.remove(pid_filename)
        if os.path.exists(out_filename):
            os.remove(out_filename)
        if os.path.exists(err_filename):
            os.remove(err_filename)
        sys.exit(1)

    except Exception as e:
        logger.error(f"Error sending shutdown signal: {e}")
        sys.exit(1)


async def handle_shutdown(signum, frame, logger):
    """Handle graceful shutdown signals."""
    logger.info("Received shutdown signal, stopping server")
    logger.shutdown()
    asyncio.get_event_loop().stop()
    sys.exit(0)


def setup_damon_logging(server: str, port: int) -> logging.Logger:
    """Set up logging for the daemon process."""
    log_file = os.path.join(PID_DIR, f"mcp_server_{server}_{port}.log")
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(f"mcp_server_{server}_{port}")
    return logger


def daemon_main(args: argparse.Namespace):
    """Main function for daemonized process."""
    # Handle graceful shutdown
    daemon_logger = setup_damon_logging(args.server, args.port)
    daemon_logger.info(f"Starting {args.server}:{args.port} in daemon mode")
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda sig=sig: asyncio.create_task(
                handle_shutdown(sig, None, daemon_logger)
            ),
        )

    try:
        asyncio.run(start_server(args))
    except Exception as e:
        daemon_logger.error(f"Daemon failed: {str(e)}", exc_info=True)
        sys.exit(1)


def check_existing_server(pid_file: str) -> None:
    """Check if a server of the given type is already running."""
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                pid = int(f.read().strip())
            # Check if process is running
            proc = psutil.Process(pid)
            if proc.status() != psutil.STATUS_ZOMBIE:
                logger.error(
                    f"Error: A server is already running with PID {pid}. Stop it first using 'stop --server {{server}}'."
                )
                sys.exit(1)
            else:
                logger.warning(
                    f"Warning: Zombie process found with PID {pid}. Removing stale PID file."
                )
                os.remove(pid_file)

        except (IOError, ValueError) as e:
            logger.error(f"Error reading PID file: {e}. Removing stale PID file.")
            os.remove(pid_file)
        except psutil.NoSuchProcess:
            logger.warning(
                f"No process running related to {pid_file}. Removing stale PID file."
            )
            os.remove(pid_file)


def show_status():
    """
    Display MCP servers' status, both attached and detached.
    """

    def find_processes_by_cmdline(search_string):
        processes = []
        for process in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                process_info = process.info
                cmdline = (
                    " ".join(process_info["cmdline"]).lower()
                    if process_info["cmdline"]
                    else ""
                )
                if search_string.lower() in cmdline:
                    processes.append(process_info)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        return processes

    search_term = "mcpserver"
    matching_processes = find_processes_by_cmdline(search_term)

    mcpserver_ps_info: List[Dict[str, Any]] = []

    if matching_processes:
        for process in matching_processes:
            ps_dict = {}
            cmd_parts: list = process["cmdline"][2:]
            mcpserver_cmd = cmd_parts[0]
            if mcpserver_cmd == "start":
                try:
                    server_opt_idx = cmd_parts.index("--server")
                    mcpserver_name = cmd_parts[server_opt_idx + 1]
                    ps_dict["server"] = mcpserver_name
                except ValueError as _:
                    print("Unable to find mcpserver name")
                    raise

                try:
                    port_opt_idx = cmd_parts.index("--port")
                    mcpserver_port = cmd_parts[port_opt_idx + 1]
                    ps_dict["port"] = mcpserver_port
                except ValueError as _:
                    ps_dict["port"] = "N/A"

                try:
                    _ = cmd_parts.index("--detach")
                    ps_dict["detached"] = True
                except ValueError as _:
                    ps_dict["detached"] = False

                mcpserver_ps_info.append(ps_dict)

    if not mcpserver_ps_info:
        print("No active mcpserver found.")
        return

    # construct table
    table_column_width = 20
    table_columns = ["server", "ports", "detached"]
    table_columns_row = ""
    for col in table_columns:
        fixed_col_name_str = f"| {col}"
        table_columns_row += fixed_col_name_str + " " * (
            table_column_width - len(fixed_col_name_str)
        )
    table_columns_row += "|"
    table_width = len(table_columns_row)
    print("╭" + "-" * (table_width - 2) + "╮")
    print(table_columns_row)
    print("|" + "-" * (table_width - 2) + "|")

    for ps_dict in mcpserver_ps_info:
        server = ps_dict["server"]
        port = ps_dict["port"]
        detached = ps_dict["detached"]

        info_row = ""
        server_part_wo_spaces = f"| {server}"
        info_row += server_part_wo_spaces + " " * (
            table_column_width - len(server_part_wo_spaces)
        )

        port_part_wo_spaces = f"| {port}"
        info_row += port_part_wo_spaces + " " * (
            table_column_width - len(port_part_wo_spaces)
        )

        detached_part_wo_spaces = f"| {detached}"
        info_row += detached_part_wo_spaces + " " * (
            table_column_width - len(detached_part_wo_spaces)
        )
        info_row += "|"
        print(info_row)

    print("╰" + "-" * (table_width - 2) + "╯")


def main():
    """Parse arguments and decide whether to run in foreground or daemon mode."""
    parser = argparse.ArgumentParser(
        description="Command line interface for mcpserver",
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
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host address to bind the server to. Defaults to 127.0.0.1",
    )
    start_parser.add_argument(
        "--port",
        type=int,
        required=True,
        help="Port to run the server on",
    )
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
    stop_parser.add_argument(
        "--port", type=int, required=True, help="Port to stop the server on"
    )

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

    _ = subparsers.add_parser("status", help="Show MCP server status")

    # Parse the arguments
    args = parser.parse_args()

    # Configure logging for the main process (non-daemon)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    if args.command == "start":
        if args.detach:
            # Run in daemon mode
            base_file_name = f"mcp_server_{args.server}"
            base_file_name = base_file_name + "_" + str(args.port)

            pid_filename = os.path.join(PID_DIR, base_file_name + ".pid")
            out_filename = os.path.join(PID_DIR, base_file_name + ".out")
            err_filename = os.path.join(PID_DIR, base_file_name + ".err")

            check_existing_server(pid_filename)

            pidfile = daemon.pidfile.TimeoutPIDLockFile(pid_filename)
            logger.info(
                f"Starting {args.server} at {args.host}:{args.port} in detach mode."
            )
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
    elif args.command == "status":
        show_status()


if __name__ == "__main__":
    main()
