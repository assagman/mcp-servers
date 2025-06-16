import os
from pathlib import Path
from typing import Union, List, Dict, cast, Any, Optional
import datetime
import tempfile
import shutil
import subprocess

from pydantic import Field, AliasChoices, field_validator, model_validator
from mcp.server.fastmcp import FastMCP

from mcp_servers.base import AbstractMCPServer, BaseMCPServerSettings
from mcp_servers.logger import MCPServersLogger

ERROR_PREFIX = "Error: "
STR_ENCODING = "utf-8"


class MCPServerFilesystemSettings(BaseMCPServerSettings):
    """
    Configuration settings for the MCPServerFilesystem.
    Settings can be provided via environment variables (e.g., MCP_SERVER_FILESYSTEM_HOST).
    """
    SERVER_NAME: str = "MCP_SERVER_FILESYSTEM"
    HOST: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("MCP_SERVER_FILESYSTEM_HOST"),
        description="Hostname or IP address to bind the server to."
    )
    PORT: int = Field(
        default=8765,
        validation_alias=AliasChoices("MCP_SERVER_FILESYSTEM_PORT"),
        description="Port number for the server to listen on."
    )
    ALLOWED_DIRECTORY: Path = Field(
        default_factory=lambda: Path(tempfile.mkdtemp(prefix="mcp_fs_")),
        validation_alias=AliasChoices("MCP_SERVER_FILESYSTEM_ALLOWED_DIR", "FS_ALLOWED_DIR"),
        description=(
            "The root directory within which all file operations are sandboxed. "
            "If not specified, a temporary directory will be created. "
            "The path will be resolved to an absolute path."
        )
    )

    @field_validator("ALLOWED_DIRECTORY", mode="before")
    @classmethod
    def _validate_allowed_directory_path_str(cls, v: Any) -> Path:
        """Ensure string paths are converted to resolved Path objects early."""
        logger = MCPServersLogger.get_logger()
        if isinstance(v, str):
            try:
                if not v: # empty value = tmp folder
                    path = Path(tempfile.mkdtemp(prefix="mcp_fs_"))
                else:
                    path = Path(v).expanduser().resolve()
                logger.debug(f"Converted string path '{v}' to '{path}'")
                return path
            except Exception as e:
                logger.error(f"Error resolving path string '{v}': {e}")
                raise ValueError(f"Invalid path string for ALLOWED_DIRECTORY: {v}. Error: {e}") from e
        if isinstance(v, Path):
            logger.info("Given ALLOWED_DIR is Path")
            return v.expanduser().resolve() # Ensure even Path objects are fully resolved
        raise TypeError("ALLOWED_DIRECTORY must be a string or Path object.")

    @model_validator(mode="after")
    def _ensure_allowed_directory_is_valid(self) -> 'MCPServerFilesystemSettings':
        """
        Validate that the ALLOWED_DIRECTORY is an existing, absolute directory
        after all field validations have run.
        """
        logger = MCPServersLogger.get_logger()
        path = self.ALLOWED_DIRECTORY
        if not path.is_absolute():
            # This should ideally be caught by resolve() in the field_validator,
            # but as a safeguard:
            logger.warning(f"ALLOWED_DIRECTORY '{path}' was not absolute, resolving again.")
            path = path.resolve()
            self.ALLOWED_DIRECTORY = path

        if not path.is_dir():
            logger.error(f"ALLOWED_DIRECTORY '{path}' is not a directory.")
            raise ValueError(f"ALLOWED_DIRECTORY '{path}' must be a directory.")

        logger.debug(f"Validated and using ALLOWED_DIRECTORY: {path}")
        return self

    model_config = BaseMCPServerSettings.model_config


class MCPServerFilesystem(AbstractMCPServer):
    """
    An MCP Server that provides tools for AI agents to interact with a sandboxed
    local filesystem. All operations are restricted to the `ALLOWED_DIRECTORY`
    defined in the settings.
    """

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None, allowed_dir: Optional[Path] = None):
        self.allowed_dir_override = allowed_dir
        super().__init__(host=host, port=port)

    def override_settings(self):
        super().override_settings()
        self._settings = cast(MCPServerFilesystemSettings, self._settings)

        if self.allowed_dir_override:
            self._settings.ALLOWED_DIRECTORY = self.allowed_dir_override

    @property
    def settings(self):
        return cast(MCPServerFilesystemSettings, self._settings)

    def _load_and_validate_settings(self) -> MCPServerFilesystemSettings:
        """Loads and validates the filesystem server settings."""
        return MCPServerFilesystemSettings()

    def _log_initial_config(self) -> None:
        """Logs the initial configuration of the server."""
        settings: MCPServerFilesystemSettings = self.settings
        self.logger.info("--- MCPServerFilesystem Configuration ---")
        self.logger.info(f"  SERVER_NAME:       {settings.SERVER_NAME}")
        self.logger.info(f"  HOST:              {settings.HOST}")
        self.logger.info(f"  PORT:              {settings.PORT}")
        self.logger.info(f"  ALLOWED_DIRECTORY: {settings.ALLOWED_DIRECTORY}")
        self.logger.info("--- End MCPServerFilesystem Configuration ---")

    def _resolve_path_and_ensure_within_allowed(self, relative_path_str: str) -> Path:
        """
        Resolves a relative path string against `ALLOWED_DIRECTORY` and ensures
        the resulting absolute path is securely within the `ALLOWED_DIRECTORY`.

        Args:
            relative_path_str: The user-provided path string, relative to `ALLOWED_DIRECTORY`.
                               An empty string or "." refers to `ALLOWED_DIRECTORY` itself.

        Returns:
            A resolved, absolute Path object that is confirmed to be within the sandbox.

        Raises:
            ValueError: If the path is invalid, attempts traversal, or falls outside
                        the `ALLOWED_DIRECTORY`.
        """
        allowed_dir = self.settings.ALLOWED_DIRECTORY

        if not isinstance(relative_path_str, str):
            raise ValueError("Path must be a string.")
        if not relative_path_str.strip():
            relative_path_str = "."

        # Disallow explicit path traversal components early.
        # Path.resolve() handles symbolic links, but this adds an explicit layer.
        if ".." in Path(relative_path_str).parts:
            raise ValueError(
                f"{ERROR_PREFIX}Path traversal ('..') is not allowed in '{relative_path_str}'."
            )

        try:
            # Important: Resolve the path *after* joining with allowed_dir if it's relative.
            # If an absolute path is given, Path() will handle it, but we still need to check
            # if it's within ALLOWED_DIRECTORY.
            candidate_path = Path(relative_path_str)
            if candidate_path.is_absolute():
                # If user provides an absolute path, it must still be within allowed_dir
                prospective_path = candidate_path.resolve()
            else:
                prospective_path = (allowed_dir / candidate_path).resolve()

        except Exception as e:
            self.logger.warning(
                f"Path resolution failed for '{relative_path_str}' "
                f"against '{allowed_dir}': {e}", exc_info=True
            )
            raise ValueError(
                f"{ERROR_PREFIX}Invalid path specified: '{relative_path_str}'. Error: {e}"
            ) from e

        # Final security check: The resolved path must be the allowed_dir itself or a descendant.
        # Path.is_relative_to() was added in Python 3.9.
        # For older Pythons, a common alternative is:
        # `allowed_dir.resolve() in prospective_path.resolve().parents` or string prefix matching.
        # However, `is_relative_to` is more robust.
        if not (prospective_path == allowed_dir or prospective_path.is_relative_to(allowed_dir)):
            raise ValueError(
                f"{ERROR_PREFIX}Operation on path '{prospective_path}' is not allowed. "
                f"Paths must be within the sandboxed directory: '{allowed_dir}'."
            )
        return prospective_path

    async def _register_tools(self, mcp_server: FastMCP) -> None:
        """Registers filesystem tools with the FastMCP server instance."""
        self.logger.info(f"Registering tools for {self.settings.SERVER_NAME}...")

        @mcp_server.tool(description="Get current working directory, CWD")
        async def get_working_directory() -> str:
            """
            Returns the absolute path to the current working directory for file operations.
            All operations are sandboxed to this directory and its subdirectories.
            """
            self.logger.info(f"Getting current working directory")
            return str(self.settings.ALLOWED_DIRECTORY)

        @mcp_server.tool()
        async def list_directory(path: str = ".") -> Union[List[Dict[str, str]], str]:
            """
            Lists files and directories at the given path, relative to the allowed working directory.

            Args:
                path (str, optional): The relative path from the working directory.
                                      Defaults to "." (the working directory itself).

            Returns:
                A list of dictionaries, each with 'name' and 'type' ('file' or 'directory'),
                or an error string if the operation fails.
            """
            self.logger.info(f"Listing contents of directory: {path}")

            try:
                target_path = self._resolve_path_and_ensure_within_allowed(path)
                if not target_path.exists():
                    return f"{ERROR_PREFIX}Path '{path}' does not exist."
                if not target_path.is_dir():
                    return f"{ERROR_PREFIX}Path '{path}' is not a directory."

                entries = []
                for item in target_path.iterdir():
                    entries.append(
                        {
                            "name": item.name,
                            "type": "directory" if item.is_dir() else "file",
                        }
                    )
                self.logger.debug(f"Listed directory '{target_path}', found {len(entries)} items.")
                return entries
            except ValueError as e: # From _resolve_path_and_ensure_within_allowed
                self.logger.warning(f"ValueError in list_directory for path '{path}': {e}")
                return str(e)
            except Exception as e:
                self.logger.error(f"Error listing files at '{path}': {e}", exc_info=True)
                return f"{ERROR_PREFIX}Could not list directory '{path}': {e}"

        @mcp_server.tool()
        def find_file_in_project(filename: str):
            self.logger.info(f"Finding file: {filename}")
            exclude_directories = [
                ".venv",
                "__pycache__",
                "node_modules",
            ]
            for root, _, files in os.walk(self.settings.ALLOWED_DIRECTORY):
                skip_dir = False
                for excluded_dir in exclude_directories:
                    if excluded_dir in root:
                        skip_dir = True

                if skip_dir:
                    continue

                if filename in files:
                    return os.path.join(root, filename)
            return None

        @mcp_server.tool()
        def get_files_containing_text(text: str):
            cmd = ["rg", "-il", text, str(Path(self.settings.ALLOWED_DIRECTORY).expanduser())]
            try:
                completed = subprocess.run(
                    cmd,
                    check=True,
                    text=True,
                    capture_output=True
                )
            except FileNotFoundError as exc:
                raise RuntimeError("ripgrep (rg) is not installed or not in PATH") from exc
            except subprocess.CalledProcessError as exc:
                # ripgrep returns exit-code 1 when it finds **no** matches.
                if exc.returncode == 1:
                    return []
                raise

            # stdout is one path per line.
            return [str(Path(line)) for line in completed.stdout.splitlines()]

        @mcp_server.tool(description="Get directory tree")
        def get_directory_tree_command(
                exclude_dirs=[
                    ".venv",
                    "__pycache__",
                    "node_modules",
                ],
                max_depth=None,
        ):
            command = ["tree"]
            if exclude_dirs:
                # Join exclusion patterns with | for regex
                exclude_pattern = "|".join(exclude_dirs)
                command.extend(["-I", exclude_pattern])
            if max_depth:
                command.extend(["-L", str(max_depth)])

            command.extend([str(self.settings.ALLOWED_DIRECTORY)])

            try:
                print(command)
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                return result.stdout
            except subprocess.CalledProcessError as e:
                return f"Error running tree command: {e}\n{e.stderr}"
            except FileNotFoundError:
                return "Error: 'tree' command not found. Please install it."

        @mcp_server.tool()
        async def read_file(path: str) -> str:
            """
            Reads the content of a file at the given path, relative to the allowed working directory.

            Args:
                path (str): The relative path to the file.

            Returns:
                The content of the file as a string, or an error string if the operation fails.
            """
            self.logger.info(f"Reading file: {path}")
            try:
                file_path = self._resolve_path_and_ensure_within_allowed(path)
                if not file_path.exists():
                    return f"{ERROR_PREFIX}File '{path}' not found."
                if not file_path.is_file():
                    return f"{ERROR_PREFIX}Path '{path}' is not a file."

                content = file_path.read_text(encoding=STR_ENCODING)
                self.logger.debug(f"Read file '{file_path}' ({len(content)} bytes).")
                # Consider adding a max file size limit here if desired.
                return content
            except ValueError as e:
                self.logger.warning(f"ValueError in read_file for path '{path}': {e}")
                return str(e)
            except Exception as e:
                self.logger.error(f"Error reading file '{path}': {e}", exc_info=True)
                return f"{ERROR_PREFIX}Could not read file '{path}': {e}"

        @mcp_server.tool()
        async def write_file(path: str, content: str, create_parents: bool = False) -> str:
            """
            Writes content to a file at the given path, relative to the allowed working directory.
            Creates the file if it doesn't exist. Overwrites if it does.

            Args:
                path (str): The relative path to the file.
                content (str): The content to write to the file.
                create_parents (bool, optional): If True, create parent directories if they
                                                 do not exist. Defaults to False.

            Returns:
                A success message or an error string.
            """
            try:
                file_path = self._resolve_path_and_ensure_within_allowed(path)

                if file_path.is_dir(): # Explicit check, though write_text would also fail
                    return f"{ERROR_PREFIX}Path '{path}' is a directory. Cannot write file content to a directory."

                parent_dir = file_path.parent
                if not parent_dir.exists():
                    if create_parents:
                        # Ensure parent_dir is also within ALLOWED_DIRECTORY (implicitly checked by file_path)
                        self.logger.info(f"Parent directory '{parent_dir}' for '{file_path}' does not exist. Creating.")
                        parent_dir.mkdir(parents=True, exist_ok=True)
                    else:
                        return f"{ERROR_PREFIX}Parent directory for '{path}' does not exist. Use create_parents=True to create it."
                elif not parent_dir.is_dir(): # Parent path exists but is not a directory
                     return f"{ERROR_PREFIX}Parent path '{parent_dir.relative_to(self.settings.ALLOWED_DIRECTORY)}' for '{path}' is not a directory."


                file_path.write_text(content, encoding=STR_ENCODING)
                self.logger.info(f"Successfully wrote {len(content)} bytes to file '{file_path}'.")
                return f"Successfully wrote to file '{path}'."
            except ValueError as e:
                self.logger.warning(f"ValueError in write_file for path '{path}': {e}")
                return str(e)
            except Exception as e:
                self.logger.error(f"Error writing to file '{path}': {e}", exc_info=True)
                return f"{ERROR_PREFIX}Could not write to file '{path}': {e}"

        @mcp_server.tool()
        async def move_item(source_path: str, destination_path: str) -> str:
            """
            Moves or renames a file or directory from source_path to destination_path.
            Both paths are relative to the allowed working directory.

            Args:
                source_path (str): The relative path of the source file/directory.
                destination_path (str): The relative path of the destination.

            Returns:
                A success message or an error string.
            """
            try:
                source_abs = self._resolve_path_and_ensure_within_allowed(source_path)
                dest_abs = self._resolve_path_and_ensure_within_allowed(destination_path)

                if not source_abs.exists():
                    return f"{ERROR_PREFIX}Source path '{source_path}' does not exist."

                # Prevent moving allowed_directory itself
                if source_abs == self.settings.ALLOWED_DIRECTORY:
                    return f"{ERROR_PREFIX}Cannot move the root allowed directory."

                # Handle case: moving a file into an existing directory
                if dest_abs.is_dir() and source_abs.is_file():
                    final_dest_abs = dest_abs / source_abs.name
                    # Re-validate the final destination path to be absolutely sure
                    # This should already be safe if dest_abs was validated, but belt-and-suspenders.
                    final_dest_abs_validated = self._resolve_path_and_ensure_within_allowed(
                        str(final_dest_abs.relative_to(self.settings.ALLOWED_DIRECTORY))
                    )
                    if final_dest_abs_validated.is_dir(): # Cannot overwrite a directory with a file implicitly
                        return f"{ERROR_PREFIX}Cannot overwrite directory '{final_dest_abs_validated.relative_to(self.settings.ALLOWED_DIRECTORY)}' with file '{source_path}'."
                    dest_abs = final_dest_abs_validated


                if dest_abs.exists() and source_abs.is_dir() and dest_abs.is_file():
                    return f"{ERROR_PREFIX}Cannot overwrite file '{destination_path}' with directory '{source_path}'."

                # Prevent moving a directory into itself or a subdirectory of itself.
                if source_abs.is_dir() and dest_abs.is_relative_to(source_abs):
                    return f"{ERROR_PREFIX}Cannot move directory '{source_path}' into itself or one of its subdirectories ('{destination_path}')."

                if source_abs == dest_abs:
                    return f"Source and destination '{source_path}' are the same. No action taken."


                shutil.move(str(source_abs), str(dest_abs))
                self.logger.info(f"Successfully moved '{source_abs}' to '{dest_abs}'.")
                return f"Successfully moved '{source_path}' to '{destination_path}'."
            except ValueError as e:
                self.logger.warning(f"ValueError in move_item from '{source_path}' to '{destination_path}': {e}")
                return str(e)
            except shutil.Error as e: # Catches SameFileError etc.
                self.logger.warning(f"Shutil error moving '{source_path}' to '{destination_path}': {e}", exc_info=True)
                return f"{ERROR_PREFIX}Failed to move '{source_path}' to '{destination_path}': {e}"
            except Exception as e:
                self.logger.error(f"Error moving '{source_path}' to '{destination_path}': {e}", exc_info=True)
                return f"{ERROR_PREFIX}Could not move '{source_path}' to '{destination_path}': {e}"

        @mcp_server.tool()
        async def delete_file(path: str) -> str:
            """
            Deletes a file at the given path, relative to the allowed working directory.

            Args:
                path (str): The relative path to the file to delete.

            Returns:
                A success message or an error string.
            """
            try:
                file_path = self._resolve_path_and_ensure_within_allowed(path)
                if not file_path.exists():
                    return f"{ERROR_PREFIX}File '{path}' not found."
                if file_path.is_dir():
                    return f"{ERROR_PREFIX}Path '{path}' is a directory. Use 'delete_directory' to delete directories."

                file_path.unlink()
                self.logger.info(f"Successfully deleted file '{file_path}'.")
                return f"Successfully deleted file '{path}'."
            except ValueError as e:
                self.logger.warning(f"ValueError in delete_file for path '{path}': {e}")
                return str(e)
            except Exception as e:
                self.logger.error(f"Error deleting file '{path}': {e}", exc_info=True)
                return f"{ERROR_PREFIX}Could not delete file '{path}': {e}"

        @mcp_server.tool()
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
                dir_path = self._resolve_path_and_ensure_within_allowed(path)
                if dir_path.exists() and not dir_path.is_dir():
                    return f"{ERROR_PREFIX}Path '{path}' exists and is not a directory. Cannot create directory."

                dir_path.mkdir(parents=True, exist_ok=True)
                self.logger.info(f"Successfully created directory '{dir_path}' (or it already existed).")
                return (
                    f"Successfully created directory '{path}' (or it already existed)."
                )
            except ValueError as e:
                self.logger.warning(f"ValueError in create_directory for path '{path}': {e}")
                return str(e)
            except Exception as e:
                self.logger.error(f"Error creating directory '{path}': {e}", exc_info=True)
                return f"{ERROR_PREFIX}Could not create directory '{path}': {e}"

        @mcp_server.tool()
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
                dir_path = self._resolve_path_and_ensure_within_allowed(path)

                if not dir_path.exists():
                    return f"{ERROR_PREFIX}Directory '{path}' not found."
                if not dir_path.is_dir():
                    return f"{ERROR_PREFIX}Path '{path}' is not a directory."
                if dir_path == self.settings.ALLOWED_DIRECTORY:
                    return f"{ERROR_PREFIX}Cannot delete the root allowed directory '{path}'."

                if recursive:
                    shutil.rmtree(dir_path)
                    self.logger.info(f"Successfully deleted directory '{dir_path}' and its contents.")
                    return f"Successfully deleted directory '{path}' and its contents."
                else:
                    if any(dir_path.iterdir()): # Check if directory is empty
                        return f"{ERROR_PREFIX}Directory '{path}' is not empty. Use recursive=True to delete non-empty directories."
                    dir_path.rmdir()
                    self.logger.info(f"Successfully deleted empty directory '{dir_path}'.")
                    return f"Successfully deleted empty directory '{path}'."
            except ValueError as e:
                self.logger.warning(f"ValueError in delete_directory for path '{path}': {e}")
                return str(e)
            except Exception as e:
                self.logger.error(f"Error deleting directory '{path}': {e}", exc_info=True)
                return f"{ERROR_PREFIX}Could not delete directory '{path}': {e}"

        @mcp_server.tool()
        async def get_item_metadata(path: str) -> Union[Dict[str, Any], str]:
            """
            Retrieves metadata for a file or directory at the given path.

            Args:
                path (str): The relative path to the file or directory.

            Returns:
                A dictionary containing metadata (name, path, type, size, modified_time, created_time, absolute_path)
                or an error string if the operation fails. Times are in ISO 8601 format.
            """
            try:
                target_path = self._resolve_path_and_ensure_within_allowed(path)
                if not target_path.exists():
                    return f"{ERROR_PREFIX}Path '{path}' does not exist."

                stat_info = target_path.stat()
                item_type = "directory" if target_path.is_dir() else "file"

                # Convert timestamps to human-readable ISO format
                # Note: ctime behavior varies by OS (creation time on Windows, metadata change time on Unix)
                modified_time = datetime.datetime.fromtimestamp(stat_info.st_mtime, tz=datetime.timezone.utc).isoformat()
                created_time = datetime.datetime.fromtimestamp(stat_info.st_ctime, tz=datetime.timezone.utc).isoformat()
                # For more accurate creation time on Unix, os.stat().st_birthtime (macOS, FreeBSD) might be available
                # but st_ctime is more portable as a "change time" / "birth time on Windows"
                try:
                    # Python 3.7+ on some OSes
                    birth_time_ts = stat_info.st_birthtime
                    created_time = datetime.datetime.fromtimestamp(birth_time_ts, tz=datetime.timezone.utc).isoformat()
                except AttributeError:
                    pass # st_birthtime not available, use st_ctime as fallback


                metadata = {
                    "name": target_path.name,
                    "relative_path": path, # The user-provided relative path
                    "absolute_path": str(target_path),
                    "type": item_type,
                    "size_bytes": stat_info.st_size,
                    "modified_time_utc": modified_time,
                    "created_time_utc": created_time, # Or metadata_changed_time_utc on some Unix
                    "is_symlink": target_path.is_symlink(),
                }
                if target_path.is_symlink():
                    try:
                        metadata["symlink_target"] = str(os.readlink(target_path))
                    except OSError:
                        metadata["symlink_target"] = "[Error reading link target]"


                self.logger.debug(f"Retrieved metadata for '{target_path}'.")
                return metadata
            except ValueError as e:
                self.logger.warning(f"ValueError in get_item_metadata for path '{path}': {e}")
                return str(e)
            except Exception as e:
                self.logger.error(f"Error getting metadata for '{path}': {e}", exc_info=True)
                return f"{ERROR_PREFIX}Could not get metadata for '{path}': {e}"

        self.logger.info(f"Successfully registered tools for {self.settings.SERVER_NAME}.")
