import os
import time
import typing as t
import asyncio
import shutil
import tempfile
from pathlib import Path

from dotenv import load_dotenv

import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic_ai.mcp import MCPServerHTTP

load_dotenv()

class MCPServerFilesystem:
    def __init__(self):
        allowed_dir = os.environ.get("MCP_SERVER_FILESYSTEM_ALLOWED_DIR")
        self.ALLOWED_DIRECTORY = Path(allowed_dir or str(tempfile.mkdtemp())).expanduser().resolve()
        print(self.ALLOWED_DIRECTORY)

        self.SERVER_NAME = "MCP_SERVER_FILESYSTEM"
        self.SERVER_HOST = str(os.environ.get("MCP_SERVER_FILESYSTEM_HOST", "0.0.0.0"))
        self.SERVER_PORT = int(os.environ.get("MCP_SERVER_FILESYSTEM_PORT", 8765))

        self.validate()
        print(f"INFO: {self.SERVER_NAME}")
        print(f"INFO: Allowed directory for operations: {self.ALLOWED_DIRECTORY}")
        print(f"INFO: Server will run on port: {self.SERVER_PORT}")
        self.server = None
        self.serve_task = None
        self.uvicorn_server = None

    def validate(self):
        if not self.ALLOWED_DIRECTORY.is_dir():
            print(f"Warning: ALLOWED_DIRECTORY '{self.ALLOWED_DIRECTORY}' does not exist or is not a directory. Creating it.")
            self.ALLOWED_DIRECTORY.mkdir(parents=True, exist_ok=True)

    async def start(self):
        server = FastMCP(
            name=self.SERVER_NAME,
            port=self.SERVER_PORT
        )

        def _resolve_path_and_ensure_within_allowed(relative_path_str: str) -> Path:
            """
            Resolves a relative path against ALLOWED_DIRECTORY and ensures
            the resulting path is within ALLOWED_DIRECTORY.
            Raises ValueError if the path is outside the allowed scope or invalid.
            """
            if not relative_path_str:
                relative_path_str = "."

            if ".." in Path(relative_path_str).parts:
                raise ValueError("Parent directories are not allowed.")

            try:
                if Path(relative_path_str).is_absolute():
                    prospective_path = Path(relative_path_str).resolve()
                else:
                    prospective_path = (self.ALLOWED_DIRECTORY / relative_path_str).resolve()
            except Exception as e:
                raise ValueError(f"Invalid path specified: {relative_path_str}. Error: {e}")

            if prospective_path == self.ALLOWED_DIRECTORY:
                return prospective_path

            is_safe = prospective_path.is_relative_to(self.ALLOWED_DIRECTORY)
            if not is_safe: # Allow exact match for ALLOWED_DIRECTORY
                raise ValueError(
                    f"Operation on path '{prospective_path}' is not allowed. "
                    f"Paths must be within '{self.ALLOWED_DIRECTORY}'."
                )
            return prospective_path


        @server.tool()
        async def get_working_directory() -> str:
            """
            Returns the current working directory for file operations.
            All operations are sandboxed to this directory and its subdirectories.
            """
            return str(self.ALLOWED_DIRECTORY)

        @server.tool()
        async def list_files(path: str = ".") -> t.Union[t.List[t.Dict[str, str]], str]:
            """
            Lists files and directories at the given path, relative to the allowed working directory.
            Args:
                path (str, optional): The relative path from the working directory. Defaults to ".".
            Returns:
                A list of dictionaries, each with 'name' and 'type' ('file' or 'directory'),
                or an error string.
            """
            try:
                target_path = _resolve_path_and_ensure_within_allowed(path)
                if not target_path.is_dir():
                    return f"Error: Path '{path}' is not a directory or does not exist."

                entries = []
                for item in target_path.iterdir():
                    entries.append({
                        "name": item.name,
                        "type": "directory" if item.is_dir() else "file"
                    })
                return entries
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                return f"Error listing files at '{path}': {e}"

        @server.tool()
        async def read_file(path: str) -> str:
            """
            Reads the content of a file at the given path, relative to the allowed working directory.
            Args:
                path (str): The relative path to the file.
            Returns:
                The content of the file as a string, or an error string.
            """
            try:
                file_path = _resolve_path_and_ensure_within_allowed(path)
                if not file_path.is_file():
                    return f"Error: Path '{path}' is not a file or does not exist."
                # For very large files, streaming or chunking might be better,
                # but for typical agent interactions, reading the whole file is common.
                return file_path.read_text(encoding='utf-8')
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                return f"Error reading file '{path}': {e}"

        @server.tool()
        async def write_file(path: str, content: str) -> str:
            """
            Writes content to a file at the given path, relative to the allowed working directory.
            Creates the file if it doesn't exist. Overwrites if it does.
            Parent directories must exist.
            Args:
                path (str): The relative path to the file.
                content (str): The content to write to the file.
            Returns:
                A success message or an error string.
            """
            try:
                file_path = _resolve_path_and_ensure_within_allowed(path)

                # Security: Disallow writing to a directory
                if file_path.is_dir():
                    return f"Error: Path '{path}' is a directory. Cannot write file content to a directory."

                # Ensure parent directory exists within the allowed scope
                parent_dir = file_path.parent
                if not parent_dir.is_dir() or not parent_dir.is_relative_to(self.ALLOWED_DIRECTORY) and parent_dir != self.ALLOWED_DIRECTORY:
                     return f"Error: Parent directory for '{path}' does not exist within the allowed scope."

                file_path.write_text(content, encoding='utf-8')
                return f"Successfully wrote to file '{path}'."
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                return f"Error writing to file '{path}': {e}"

        @server.tool()
        async def move_file(source_path: str, destination_path: str) -> str:
            """
            Moves or renames a file or directory from source_path to destination_path,
            both relative to the allowed working directory.
            Args:
                source_path (str): The relative path of the source file/directory.
                destination_path (str): The relative path of the destination.
            Returns:
                A success message or an error string.
            """
            try:
                source_abs_path = _resolve_path_and_ensure_within_allowed(source_path)
                destination_abs_path = _resolve_path_and_ensure_within_allowed(destination_path)

                if not source_abs_path.exists():
                    return f"Error: Source path '{source_path}' does not exist."
                if destination_abs_path.exists() and destination_abs_path.is_dir() and source_abs_path.is_file():
                     # If destination is an existing directory, move source file into it
                    destination_abs_path = destination_abs_path / source_abs_path.name
                    # Re-check this new path to ensure it's still valid (though it should be)
                    destination_abs_path = _resolve_path_and_ensure_within_allowed(str(destination_abs_path.relative_to(self.ALLOWED_DIRECTORY)))


                shutil.move(str(source_abs_path), str(destination_abs_path))
                return f"Successfully moved '{source_path}' to '{destination_path}'."
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                return f"Error moving '{source_path}' to '{destination_path}': {e}"

        @server.tool()
        async def delete_file(path: str) -> str:
            """
            Deletes a file at the given path, relative to the allowed working directory.
            Args:
                path (str): The relative path to the file to delete.
            Returns:
                A success message or an error string.
            """
            try:
                file_path = _resolve_path_and_ensure_within_allowed(path)
                if not file_path.exists():
                    return f"Error: File '{path}' not found."
                if file_path.is_dir():
                    return f"Error: Path '{path}' is a directory. Use 'delete_directory' to delete directories."
                file_path.unlink()
                return f"Successfully deleted file '{path}'."
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                return f"Error deleting file '{path}': {e}"

        @server.tool()
        async def create_directory(path: str) -> str:
            """
            Creates a directory at the given path, relative to the allowed working directory.
            Creates parent directories if they don't exist (like mkdir -p).
            Args:
                path (str): The relative path of the directory to create.
            Returns:
                A success message or an error string.
            """
            try:
                dir_path = _resolve_path_and_ensure_within_allowed(path)
                # exist_ok=True: no error if directory already exists
                # parents=True: create parent_dirs if necessary
                dir_path.mkdir(parents=True, exist_ok=True)
                return f"Successfully created directory '{path}' (or it already existed)."
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                return f"Error creating directory '{path}': {e}"

        @server.tool()
        async def delete_directory(path: str, recursive: bool = False) -> str:
            """
            Deletes a directory at the given path, relative to the allowed working directory.
            Args:
                path (str): The relative path of the directory to delete.
                recursive (bool, optional): If True, delete the directory and its contents (like rm -rf).
                                            If False, only delete if empty. Defaults to False.
            Returns:
                A success message or an error string.
            """
            try:
                dir_path = _resolve_path_and_ensure_within_allowed(path)
                if not dir_path.is_dir():
                    return f"Error: Path '{path}' is not a directory or does not exist."
                if dir_path == self.ALLOWED_DIRECTORY:
                    return f"Error: Cannot delete the root allowed directory '{path}'."

                if recursive:
                    shutil.rmtree(dir_path)
                    return f"Successfully deleted directory '{path}' and its contents."
                else:
                    if any(dir_path.iterdir()): # Check if directory is empty
                        return f"Error: Directory '{path}' is not empty. Use recursive=True to delete non-empty directories."
                    dir_path.rmdir()
                    return f"Successfully deleted empty directory '{path}'."
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                return f"Error deleting directory '{path}': {e}"

        uviconfig = uvicorn.Config(server.sse_app, host=self.SERVER_HOST, port=self.SERVER_PORT, log_level="info")
        self.uvicorn_server = uvicorn.Server(uviconfig)
        self.serve_task = asyncio.create_task(self.uvicorn_server.serve())

        while True:
            await asyncio.sleep(1)
            if self.uvicorn_server.started:
                break

        return self.serve_task

    async def stop(self): # Optional: for more controlled shutdown
        if hasattr(self, 'server_instance') and self.uvicorn_server:
            print(f"INFO: Attempting to shut down {self.SERVER_NAME}...")
            self.uvicorn_server.should_exit = True
            # Give it a moment, or await a shutdown completion if available
            if self.serve_task and not self.serve_task.done():
                 self.serve_task.cancel()
                 try:
                     await self.serve_task
                 except asyncio.CancelledError:
                     print(f"INFO: {self.SERVER_NAME} serve task successfully cancelled.")
                 except Exception as e:
                     print(f"ERROR: Exception during serve_task cancellation: {e}")
        print(f"INFO: {self.SERVER_NAME} stop sequence initiated.")

    def get_mcp_server_http(self):
        return MCPServerHTTP(url=f'http://{self.SERVER_HOST}:{self.SERVER_PORT}/sse')
