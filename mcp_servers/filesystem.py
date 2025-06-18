import os
from pathlib import Path
from typing import Union, List, Dict, cast, Any, Optional
import datetime
import tempfile
import shutil
import subprocess

from pydantic import Field, AliasChoices, field_validator, model_validator

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
        default="127.0.0.1",
        validation_alias=AliasChoices("MCP_SERVER_FILESYSTEM_HOST"),
        description="Hostname or IP address to bind the server to.",
    )
    PORT: int = Field(
        description="Port number for the server to listen on.",
    )
    ALLOWED_DIRECTORY: Path = Field(
        description=(
            "The root directory within which all file operations are sandboxed. "
            "If not specified, a temporary directory will be created. "
            "The path will be resolved to an absolute path."
        ),
    )

    @field_validator("ALLOWED_DIRECTORY", mode="before")
    @classmethod
    def _validate_allowed_directory_path_str(cls, v: Any) -> Path:
        """Ensure string paths are converted to resolved Path objects early."""
        logger = MCPServersLogger.get_logger()
        if isinstance(v, str):
            try:
                if not v:  # empty value = tmp folder
                    path = Path(tempfile.mkdtemp(prefix="mcp_fs_"))
                else:
                    path = Path(v).expanduser().resolve()

                # always resolve path
                path = path.expanduser().resolve()
                logger.debug(f"Converted string path '{v}' to '{path}'")
                return path
            except Exception as e:
                logger.error(f"Error resolving path string '{v}': {e}")
                raise ValueError(
                    f"Invalid path string for ALLOWED_DIRECTORY: {v}. Error: {e}"
                ) from e
        if isinstance(v, Path):
            logger.debug("Given ALLOWED_DIR is Path")
            return (
                v.expanduser().resolve()
            )  # Ensure even Path objects are fully resolved
        raise TypeError("ALLOWED_DIRECTORY must be a string or Path object.")

    @model_validator(mode="after")
    def _ensure_allowed_directory_is_valid(self) -> "MCPServerFilesystemSettings":
        """
        Validate that the ALLOWED_DIRECTORY is an existing, absolute directory
        after all field validations have run.
        """
        logger = MCPServersLogger.get_logger()
        path = self.ALLOWED_DIRECTORY
        if not path.is_absolute():
            # This should ideally be caught by resolve() in the field_validator,
            # but as a safeguard:
            logger.warning(
                f"ALLOWED_DIRECTORY '{path}' was not absolute, resolving again."
            )
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

    def __init__(
        self,
        host: str,
        port: int,
        allowed_dir: Optional[Path] = None,
    ):
        super().__init__(host=host, port=port, allowed_dir=allowed_dir)

    @property
    def settings(self):
        return cast(MCPServerFilesystemSettings, self._settings)

    def _load_and_validate_settings(
        self, host: str, port: int, **kwargs
    ) -> MCPServerFilesystemSettings:
        """Loads and validates the filesystem server settings."""
        allowed_dir: str = kwargs.pop("allowed_dir") or tempfile.mkdtemp(
            prefix="mcp_fs_"
        )
        settings = MCPServerFilesystemSettings(
            HOST=host,
            PORT=port,
            ALLOWED_DIRECTORY=Path(allowed_dir).expanduser().resolve(),
        )
        return settings

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
                f"against '{allowed_dir}': {e}",
                exc_info=True,
            )
            raise ValueError(
                f"{ERROR_PREFIX}Invalid path specified: '{relative_path_str}'. Error: {e}"
            ) from e

        # Final security check: The resolved path must be the allowed_dir itself or a descendant.
        # Path.is_relative_to() was added in Python 3.9.
        # For older Pythons, a common alternative is:
        # `allowed_dir.resolve() in prospective_path.resolve().parents` or string prefix matching.
        # However, `is_relative_to` is more robust.
        if not (
            prospective_path == allowed_dir
            or prospective_path.is_relative_to(allowed_dir)
        ):
            raise ValueError(
                f"{ERROR_PREFIX}Operation on path '{prospective_path}' is not allowed. "
                f"Paths must be within the sandboxed directory: '{allowed_dir}'."
            )
        return prospective_path

    async def _get_current_working_directory(self) -> str:
        """
        Returns the absolute path to the current working directory(cwd) for file operations.
        All operations are sandboxed to this directory and its subdirectories.

        This directory is the top-level directory that is allowed to perform operations in the filesystem.
        """
        self.logger.info("Getting current working directory")
        return str(self.settings.ALLOWED_DIRECTORY)

    async def _list_directory(
        self,
        path: str,
    ) -> Union[List[Dict[str, str]], str]:
        """
        Lists files and directories at the given path, relative to the allowed working directory.

        Args:
            path (str): The relative path from the working directory.
                                  Defaults to "." (the working directory itself).

        Returns:
            A list of dictionaries, each with 'name' and 'type' ('file' or 'directory'),
            or an error string if the operation fails.
        """
        if not path:
            path = await self._get_current_working_directory()

        self.logger.info(f"Listing contents of directory: {path}")

        try:
            target_path = self._resolve_path_and_ensure_within_allowed(path)
            if not target_path.exists():
                self.logger.warning(f"List directory: Path '{path}' does not exist.")
                return f"{ERROR_PREFIX}Path '{path}' does not exist."
            if not target_path.is_dir():
                self.logger.warning(
                    f"List directory: Path '{path}' is not a directory."
                )
                return f"{ERROR_PREFIX}Path '{path}' is not a directory."

            entries = []
            for item in target_path.iterdir():
                entries.append(
                    {
                        "name": item.name,
                        "type": "directory" if item.is_dir() else "file",
                    }
                )
            self.logger.info(
                f"Listed directory '{target_path}', found {len(entries)} items."
            )

            return entries
        except ValueError as e:  # From _resolve_path_and_ensure_within_allowed
            self.logger.warning(f"ValueError in list_directory for path '{path}': {e}")
            return str(e)
        except Exception as e:
            self.logger.error(f"Error listing files at '{path}': {e}", exc_info=True)
            return f"{ERROR_PREFIX}Could not list directory '{path}': {e}"

    def _find_files_in_current_working_directory(
        self,
        filename: str,
        exact_match: bool = True,
    ) -> List[str]:
        """
        Search and return all files matching with given filename recursively in cwd. This tool
        uses `fd` and it's expected as executable in the system.

        Args:
            filename (str): Filename to search.
            exact_match (bool): Specifies if the name should be searched exactly or not.
                Like patterns: name vs ^name$

        Returns:
            A list of paths, each corresponds to relative paths to the current
            working directory.
        """
        self.logger.info(f"Searching for filename '{filename}' in project.")

        default_exclude_dirs = [
            ".venv",
            "__pycache__",
            "node_modules",
        ]

        if exact_match:
            filename_pattern = f"^{filename}$"
        else:
            filename_pattern = filename

        cmd = [
            "fd",  # fd executable
            "-Hi",  # --hidden, --ignore-case
            "-t",  # type
            "f",  # file
            *[f"-E {d}" for d in default_exclude_dirs],  # exclude dirs
            filename_pattern,
            ".",
        ]
        self.logger.debug(" ".join(cmd))
        try:
            completed = subprocess.run(cmd, check=True, text=True, capture_output=True)
            found_matches = [str(Path(line)) for line in completed.stdout.splitlines()]
            self.logger.info(f"Found {filename} matches: {'\n'.join(found_matches)}.")
            return found_matches
        except FileNotFoundError as exc:
            self.logger.error("fd is not installed or not in PATH.")
            raise RuntimeError("fd is not installed or not in PATH") from exc
        except subprocess.CalledProcessError as exc:
            if exc.returncode == 1:
                self.logger.info(f"No files found named '{filename}'.")
                return []
            self.logger.error(
                f"Error running ripgrep: {exc}\n{exc.stderr}", exc_info=True
            )
            raise
        except Exception as e:
            self.logger.error(
                f"Unexpected error occured while finding file '{filename}': {e}",
                exc_info=True,
            )
            raise

    def _find_directories_in_current_working_directory(
        self,
        dirname: str,
        exact_match: bool = True,
    ) -> List[str]:
        """
        Search and return all directories matching with given dirname recursively in cwd. This tool
        uses `fd` and it's expected as executable in the system.

        Args:
            dirname (str): Dirname to search.
            exact_match (bool): Specifies if the name should be searched exactly or not.
                Like patterns: name vs ^name$

        Returns:
            A list of paths, each corresponds to relative paths to the current
            working directory.
        """
        self.logger.info(f"Searching for dirname '{dirname}' in project.")

        default_exclude_dirs = [
            ".venv",
            "dist",
            "__pycache__",
            "node_modules",
        ]

        if exact_match:
            dirname_pattern = f"^{dirname}$"
        else:
            dirname_pattern = dirname

        cmd = [
            "fd",  # fd executable
            "-Hi",  # --hidden, --ignore-case
            "-t",  # type
            "d",  # directory
            *[f"-E {d}" for d in default_exclude_dirs],  # exclude dirs
            dirname_pattern,
            ".",
        ]
        self.logger.debug(" ".join(cmd))
        try:
            completed = subprocess.run(cmd, check=True, text=True, capture_output=True)
            found_matches = [str(Path(line)) for line in completed.stdout.splitlines()]
            self.logger.info(f"Found {dirname} matches: {'\n'.join(found_matches)}.")
            return found_matches
        except FileNotFoundError as exc:
            self.logger.error("fd is not installed or not in PATH.")
            raise RuntimeError("fd is not installed or not in PATH") from exc
        except subprocess.CalledProcessError as exc:
            if exc.returncode == 1:
                self.logger.info(f"No files found named '{dirname}'.")
                return []
            self.logger.error(
                f"Error running ripgrep: {exc}\n{exc.stderr}", exc_info=True
            )
            raise
        except Exception as e:
            self.logger.error(
                f"Unexpected error occured while finding file '{dirname}': {e}",
                exc_info=True,
            )
            raise

    def _grep_text_in_current_working_directory(
        self,
        text: str,
    ):
        """
        Search and return all files matching with given text. This tool
        uses `rg` and it's expected as executable in the system.

        Args:
            text (str): Text pattern to search.

        Returns:
            A list of paths, each corresponds to relative paths to the current
            working directory.
        """
        self.logger.info(f"Searching for text '{text}' in project files.")
        cmd = [
            "rg",
            "-il",
            text,
            str(Path(self.settings.ALLOWED_DIRECTORY).expanduser()),
        ]
        try:
            completed = subprocess.run(cmd, check=True, text=True, capture_output=True)
            found_files = [str(Path(line)) for line in completed.stdout.splitlines()]
            self.logger.info(
                f"Found {len(found_files)} files containing text '{text}'."
            )
            return found_files
        except FileNotFoundError as exc:
            self.logger.error("ripgrep (rg) is not installed or not in PATH.")
            raise RuntimeError("ripgrep (rg) is not installed or not in PATH") from exc
        except subprocess.CalledProcessError as exc:
            if exc.returncode == 1:
                self.logger.info(f"No files found containing text '{text}'.")
                return []
            self.logger.error(
                f"Error running ripgrep: {exc}\n{exc.stderr}", exc_info=True
            )
            raise
        except Exception as e:
            self.logger.error(
                f"Unexpected error searching for text '{text}': {e}", exc_info=True
            )
            raise

    def _get_directory_tree(
        self,
        exclude_dirs: List[str] = [],
        max_depth: int = 4,
    ) -> str:
        """
        Get directory tree. To exclude directories, provide directory names as list of strings

        Args:
            exclude_dirs (List[str]): Directory list to be excluded from the file tree.
            max_depth (int): Maximum depth of obtaining the file tree.

        Returns:
            `tree` command output as string
        """
        self.logger.info(
            f"Getting directory tree with exclude_dirs: {exclude_dirs}, max_depth: {max_depth}"
        )
        if not exclude_dirs:
            exclude_dirs = []

        if not max_depth:
            max_depth = 4

        if isinstance(exclude_dirs, str):
            exclude_dirs = [exclude_dirs]

        default_exclude_dirs = [
            ".venv",
            "dist",
            "__pycache__",
            "node_modules",
        ]
        exclude_dirs.extend(default_exclude_dirs)

        if max_depth > 10:
            msg = "max_depth > 10 is not allowed."
            self.logger.warning(msg)
            raise ValueError(msg)

        command = ["tree"]
        if exclude_dirs:
            # Join exclusion patterns with | for regex
            exclude_pattern = "|".join(exclude_dirs)
            command.extend(["-I", exclude_pattern])
        if max_depth:
            command.extend(["-L", str(max_depth)])

        command.extend([str(self.settings.ALLOWED_DIRECTORY)])

        try:
            self.logger.info(f"Executing: {' '.join(command)}")
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            self.logger.debug("Directory tree command executed successfully.")
            return result.stdout
        except subprocess.CalledProcessError as e:
            self.logger.error(
                f"Error running tree command: {e}\n{e.stderr}", exc_info=True
            )
            return f"Error running tree command: {e}\n{e.stderr}"
        except FileNotFoundError:
            self.logger.error("'tree' command not found.")
            return "Error: 'tree' command not found. Please install it."
        except Exception as e:
            self.logger.error(
                f"Unexpected error getting directory tree: {e}", exc_info=True
            )
            raise

    async def _read_file(self, path: str) -> str:
        """
        Reads the entire content of a file at the given path, relative to the allowed working directory.

        Args:
            path (str): The relative path to the file.

        Returns:
            The content of the file as a string, or an error string if the operation fails.
        """
        self.logger.info(f"Reading file: {path}")
        try:
            file_path = self._resolve_path_and_ensure_within_allowed(path)
            if not file_path.exists():
                self.logger.warning(f"Read file: File '{path}' not found.")
                return f"{ERROR_PREFIX}File '{path}' not found."
            if not file_path.is_file():
                self.logger.warning(f"Read file: Path '{path}' is not a file.")
                return f"{ERROR_PREFIX}Path '{path}' is not a file."

            content = file_path.read_text(encoding=STR_ENCODING)
            self.logger.info(f"Read file '{file_path}' ({len(content)} bytes).")
            # Consider adding a max file size limit here if desired.
            return content
        except ValueError as e:
            self.logger.warning(f"ValueError in read_file for path '{path}': {e}")
            return str(e)
        except Exception as e:
            self.logger.error(f"Error reading file '{path}': {e}", exc_info=True)
            return f"{ERROR_PREFIX}Could not read file '{path}': {e}"

    async def _write_file(
        self, path: str, content: str, create_parents: bool = False
    ) -> str:
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
        self.logger.info(f"Writing to file: {path}, create_parents: {create_parents}")
        try:
            file_path = self._resolve_path_and_ensure_within_allowed(path)

            if file_path.is_dir():  # Explicit check, though write_text would also fail
                self.logger.warning(f"Write file: Path '{path}' is a directory.")
                return f"{ERROR_PREFIX}Path '{path}' is a directory. Cannot write file content to a directory."

            parent_dir = file_path.parent
            if not parent_dir.exists():
                if create_parents:
                    # Ensure parent_dir is also within ALLOWED_DIRECTORY (implicitly checked by file_path)
                    self.logger.info(
                        f"Parent directory '{parent_dir}' for '{file_path}' does not exist. Creating."
                    )
                    parent_dir.mkdir(parents=True, exist_ok=True)
                else:
                    self.logger.warning(
                        f"Write file: Parent directory for '{path}' does not exist and create_parents is False."
                    )
                    return f"{ERROR_PREFIX}Parent directory for '{path}' does not exist. Use create_parents=True to create it."
            elif not parent_dir.is_dir():  # Parent path exists but is not a directory
                self.logger.warning(
                    f"Write file: Parent path '{parent_dir.relative_to(self.settings.ALLOWED_DIRECTORY)}' for '{path}' is not a directory."
                )
                return f"{ERROR_PREFIX}Parent path '{parent_dir.relative_to(self.settings.ALLOWED_DIRECTORY)}' for '{path}' is not a directory."

            file_path.write_text(content, encoding=STR_ENCODING)
            self.logger.info(
                f"Successfully wrote {len(content)} bytes to file '{file_path}'."
            )
            return f"Successfully wrote to file '{path}'."
        except ValueError as e:
            self.logger.warning(f"ValueError in for path '{path}': {e}")
            return str(e)
        except Exception as e:
            self.logger.error(f"Error writing to file '{path}': {e}", exc_info=True)
            return f"{ERROR_PREFIX}Could not write to file '{path}': {e}"

    async def _move_item(self, source_path: str, destination_path: str) -> str:
        """
        Moves or renames a file or directory from source_path to destination_path.
        Both paths are relative to the allowed working directory.

        Args:
            source_path (str): The relative path of the source file/directory.
            destination_path (str): The relative path of the destination.

        Returns:
            A success message or an error string.
        """
        self.logger.info(f"Moving item from {source_path} to {destination_path}")
        try:
            source_abs = self._resolve_path_and_ensure_within_allowed(source_path)
            dest_abs = self._resolve_path_and_ensure_within_allowed(destination_path)

            if not source_abs.exists():
                self.logger.warning(
                    f"Move item: Source path '{source_path}' does not exist."
                )
                return f"{ERROR_PREFIX}Source path '{source_path}' does not exist."

            # Prevent moving allowed_directory itself
            if source_abs == self.settings.ALLOWED_DIRECTORY:
                self.logger.warning(
                    f"Move item: Cannot move the root allowed directory '{source_path}'."
                )
                return f"{ERROR_PREFIX}Cannot move the root allowed directory."

            # Handle case: moving a file into an existing directory
            if dest_abs.is_dir() and source_abs.is_file():
                final_dest_abs = dest_abs / source_abs.name
                # Re-validate the final destination path to be absolutely sure
                # This should already be safe if dest_abs was validated, but belt-and-suspenders.
                final_dest_abs_validated = self._resolve_path_and_ensure_within_allowed(
                    str(final_dest_abs.relative_to(self.settings.ALLOWED_DIRECTORY))
                )
                if (
                    final_dest_abs_validated.is_dir()
                ):  # Cannot overwrite a directory with a file implicitly
                    self.logger.warning(
                        f"Move item: Cannot overwrite directory '{final_dest_abs_validated.relative_to(self.settings.ALLOWED_DIRECTORY)}' with file '{source_path}'."
                    )
                    return f"{ERROR_PREFIX}Cannot overwrite directory '{final_dest_abs_validated.relative_to(self.settings.ALLOWED_DIRECTORY)}' with file '{source_path}'. Perform delete first."
                dest_abs = final_dest_abs_validated

            if dest_abs.exists() and source_abs.is_dir() and dest_abs.is_file():
                self.logger.warning(
                    f"Move item: Cannot overwrite file '{destination_path}' with directory '{source_path}'."
                )
                return f"{ERROR_PREFIX}Cannot overwrite file '{destination_path}' with directory '{source_path}'. Perform delete first."

            # Prevent moving a directory into itself or a subdirectory of itself.
            if source_abs.is_dir() and dest_abs.is_relative_to(source_abs):
                self.logger.warning(
                    f"Move item: Cannot move directory '{source_path}' into itself or one of its subdirectories ('{destination_path}')."
                )
                return f"{ERROR_PREFIX}Cannot move directory '{source_path}' into itself or one of its subdirectories ('{destination_path}')."

            if source_abs == dest_abs:
                self.logger.info(
                    f"Move item: Source and destination '{source_path}' are the same."
                )
                return f"Source and destination '{source_path}' are the same. No action taken."

            shutil.move(str(source_abs), str(dest_abs))
            self.logger.info(f"Successfully moved '{source_abs}' to '{dest_abs}'.")
            return f"Successfully moved '{source_path}' to '{destination_path}'."
        except ValueError as e:
            self.logger.warning(
                f"ValueError in move from '{source_path}' to '{destination_path}': {e}"
            )
            return str(e)
        except shutil.Error as e:  # Catches SameFileError etc.
            self.logger.warning(
                f"Shutil error moving '{source_path}' to '{destination_path}': {e}",
                exc_info=True,
            )
            return f"{ERROR_PREFIX}Failed to move '{source_path}' to '{destination_path}': {e}"
        except Exception as e:
            self.logger.error(
                f"Error moving '{source_path}' to '{destination_path}': {e}",
                exc_info=True,
            )
            return f"{ERROR_PREFIX}Could not move '{source_path}' to '{destination_path}': {e}"

    async def _delete_file(self, path: str) -> str:
        """
        Delete a file at the given path, relative to the allowed working directory.

        Args:
            path (str): The relative path to the file to delete.

        Returns:
            A success message or an error string.
        """
        self.logger.info(f"Deleting file: {path}")
        try:
            file_path = self._resolve_path_and_ensure_within_allowed(path)
            if not file_path.exists():
                self.logger.warning(f"Delete file: File '{path}' not found.")
                return f"{ERROR_PREFIX}File '{path}' not found."
            if file_path.is_dir():
                self.logger.warning(f"Delete file: Path '{path}' is a directory.")
                return f"{ERROR_PREFIX}Path '{path}' is a directory. Use '_delete_directory' to delete directories."

            file_path.unlink()
            self.logger.info(f"Successfully deleted file '{file_path}'.")
            return f"Successfully deleted file '{path}'."
        except ValueError as e:
            self.logger.warning(f"ValueError while deleting path '{path}': {e}")
            return str(e)
        except Exception as e:
            self.logger.error(f"Error deleting file '{path}': {e}", exc_info=True)
            return f"{ERROR_PREFIX}Could not delete file '{path}': {e}"

    async def _create_directory(self, path: str) -> str:
        """
        Create a directory at the given path, relative to the allowed working directory.
        Create parent directories if they don't exist (like mkdir -p).

        Args:
            path (str): The relative path of the directory to create.

        Returns:
            A success message or an error string.
        """
        self.logger.info(f"Creating directory: {path}")
        try:
            dir_path = self._resolve_path_and_ensure_within_allowed(path)
            if dir_path.exists() and not dir_path.is_dir():
                self.logger.warning(f"Path '{path}' exists and is not a directory.")
                return f"{ERROR_PREFIX}Path '{path}' exists and is not a directory. Cannot create directory."

            dir_path.mkdir(parents=True, exist_ok=True)
            self.logger.info(
                f"Successfully created directory '{dir_path}' (or it already existed)."
            )
            return f"Successfully created directory '{path}' (or it already existed)."
        except ValueError as e:
            self.logger.warning(f"ValueError creating path '{path}': {e}")
            return str(e)
        except Exception as e:
            self.logger.error(f"Error creating directory '{path}': {e}", exc_info=True)
            return f"{ERROR_PREFIX}Could not create directory '{path}': {e}"

    async def _delete_directory(self, path: str, recursive: bool = False) -> str:
        """
        Delete a directory at the given path, relative to the allowed working directory.

        Args:
            path (str): The relative path of the directory to delete.
            recursive (bool, optional): If True, delete the directory and its contents (like rm -rf).
                                        If False, only delete if empty. Defaults to False.

        Returns:
            A success message or an error string.
        """
        self.logger.info(f"Deleting directory: {path}, recursive: {recursive}")
        try:
            dir_path = self._resolve_path_and_ensure_within_allowed(path)

            if not dir_path.exists():
                self.logger.warning(f"Delete directory: Directory '{path}' not found.")
                return f"{ERROR_PREFIX}Directory '{path}' not found."
            if not dir_path.is_dir():
                self.logger.warning(
                    f"Delete directory: Path '{path}' is not a directory."
                )
                return f"{ERROR_PREFIX}Path '{path}' is not a directory."
            if dir_path == self.settings.ALLOWED_DIRECTORY:
                self.logger.warning(
                    f"Delete directory: Cannot delete the root allowed directory '{path}'."
                )
                return f"{ERROR_PREFIX}Cannot delete the root allowed directory '{path}'. Perform clean up inside of it, or restart server."

            if recursive:
                shutil.rmtree(dir_path)
                self.logger.info(
                    f"Successfully deleted directory '{dir_path}' and its contents."
                )
                return f"Successfully deleted directory '{path}' and its contents."
            else:
                if any(dir_path.iterdir()):  # Check if directory is empty
                    self.logger.warning(
                        f"Delete directory: Directory '{path}' not empty and recursive is False."
                    )
                    return f"{ERROR_PREFIX}Directory '{path}' is not empty. Use recursive=True to delete non-empty directories."
                dir_path.rmdir()
                self.logger.info(f"Successfully deleted empty directory '{dir_path}'.")
                return f"Successfully deleted empty directory '{path}'."
        except ValueError as e:
            self.logger.warning(f"ValueError in while deleting path '{path}': {e}")
            return str(e)
        except Exception as e:
            self.logger.error(f"Error deleting directory '{path}': {e}", exc_info=True)
            return f"{ERROR_PREFIX}Could not delete directory '{path}': {e}"

    async def _get_item_metadata(self, path: str) -> Union[Dict[str, Any], str]:
        """
        Retrieve metadata for a file or directory at the given path.

        Args:
            path (str): The relative path to the file or directory.

        Returns:
            A dictionary containing metadata (name, path, type, size, modified_time, created_time, absolute_path)
            or an error string if the operation fails. Times are in ISO 8601 format.
        """
        self.logger.info(f"Getting metadata for: {path}")
        try:
            target_path = self._resolve_path_and_ensure_within_allowed(path)
            if not target_path.exists():
                self.logger.warning(f"Get metadata: Path '{path}' does not exist.")
                return f"{ERROR_PREFIX}Path '{path}' does not exist."

            stat_info = target_path.stat()
            item_type = "directory" if target_path.is_dir() else "file"

            # Convert timestamps to human-readable ISO format
            # Note: ctime behavior varies by OS (creation time on Windows, metadata change time on Unix)
            modified_time = datetime.datetime.fromtimestamp(
                stat_info.st_mtime, tz=datetime.timezone.utc
            ).isoformat()
            created_time = datetime.datetime.fromtimestamp(
                stat_info.st_ctime, tz=datetime.timezone.utc
            ).isoformat()
            # For more accurate creation time on Unix, os.stat().st_birthtime (macOS, FreeBSD) might be available
            # but st_ctime is more portable as a "change time" / "birth time on Windows"
            try:
                # Python 3.7+ on some OSes
                birth_time_ts = stat_info.st_birthtime
                created_time = datetime.datetime.fromtimestamp(
                    birth_time_ts, tz=datetime.timezone.utc
                ).isoformat()
            except AttributeError:
                pass  # st_birthtime not available, use st_ctime as fallback

            metadata = {
                "name": target_path.name,
                "relative_path": path,  # The user-provided relative path
                "absolute_path": str(target_path),
                "type": item_type,
                "size_bytes": stat_info.st_size,
                "modified_time_utc": modified_time,
                "created_time_utc": created_time,  # Or metadata_changed_time_utc on some Unix
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
            self.logger.warning(
                f"ValueError in get_item_metadata for path '{path}': {e}"
            )
            return str(e)
        except Exception as e:
            self.logger.error(
                f"Error getting metadata for '{path}': {e}", exc_info=True
            )
            return f"{ERROR_PREFIX}Could not get metadata for '{path}': {e}"

    async def _register_tools(self) -> None:
        """Registers filesystem tools with the FastMCP server instance."""
        self.logger.debug(f"Registering tools for {self.settings.SERVER_NAME}.")

        self._register_mcp_server_tool(
            self._get_current_working_directory,
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=False,
        )
        self._register_mcp_server_tool(
            self._list_directory,
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=False,
        )
        self._register_mcp_server_tool(
            self._find_files_in_current_working_directory,
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=False,
        )
        self._register_mcp_server_tool(
            self._find_directories_in_current_working_directory,
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=False,
        )
        self._register_mcp_server_tool(
            self._grep_text_in_current_working_directory,
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=False,
        )
        self._register_mcp_server_tool(
            self._get_directory_tree,
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=False,
        )
        self._register_mcp_server_tool(
            self._read_file,
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=False,
        )
        self._register_mcp_server_tool(
            self._write_file,
            read_only=False,
            destructive=True,
            idempotent=False,
            open_world=False,
        )
        self._register_mcp_server_tool(
            self._move_item,
            read_only=False,
            destructive=True,
            idempotent=False,
            open_world=False,
        )
        self._register_mcp_server_tool(
            self._delete_file,
            read_only=False,
            destructive=True,
            idempotent=False,
            open_world=False,
        )
        self._register_mcp_server_tool(
            self._create_directory,
            read_only=False,
            destructive=True,
            idempotent=False,
            open_world=False,
        )
        self._register_mcp_server_tool(
            self._delete_directory,
            read_only=False,
            destructive=True,
            idempotent=False,
            open_world=False,
        )
        self._register_mcp_server_tool(
            self._get_item_metadata,
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=False,
        )

        self.logger.debug(
            f"Successfully registered tools for {self.settings.SERVER_NAME}."
        )
