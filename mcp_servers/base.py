import time
import asyncio
import logging
import httpx
from mcp.types import ToolAnnotations
import uvicorn
from abc import ABC, abstractmethod
from typing import Callable, Optional, Dict, Any
from asyncio import Task

from uvicorn import Server
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_ai.mcp import MCPServerStreamableHTTP
from mcp.server.fastmcp import FastMCP

from mcp_servers.exceptions import (
    MCPRateLimitError,
    MCPToolConfigurationError,
    MCPUpstreamServiceError,
)
from mcp_servers.logger import MCPServersLogger


class MCPServer(FastMCP):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.server: Optional[Server] = None
        self.server_task: Optional[Task] = None

    async def run_streamable_http_async(self) -> None:
        """Run the server using StreamableHTTP transport."""

        starlette_app = self.streamable_http_app()

        config = uvicorn.Config(
            starlette_app,
            host=self.settings.host,
            port=self.settings.port,
            log_level=self.settings.log_level.lower(),
        )
        self.server = uvicorn.Server(config)
        self.server_task = asyncio.create_task(self.server.serve())


class BaseMCPServerSettings(BaseSettings):
    """Base settings for all MCP servers."""

    SERVER_NAME: str
    HOST: str = "127.0.0.1"
    PORT: int
    LOG_LEVEL: int = logging.INFO
    HTTP_CLIENT_TIMEOUT: float = 60.0
    RATE_LIMIT_PER_SECOND: Optional[int] = 50

    model_config = SettingsConfigDict(
        extra="allow",
        case_sensitive=False,
    )


class AbstractMCPServer(ABC):
    """
    Abstract Base Class for MCP Servers.
    Provides a common structure for lifecycle management, HTTP client handling,
    rate limiting, and Uvicorn server setup.
    """

    SETTINGS_TYPE = BaseMCPServerSettings

    def __init__(
        self,
        host: str,
        port: int,
        **kwargs,
    ):
        """
        Initializes the server. Derived classes are expected to load their
        specific settings in their __init__ and pass them to super().__init__(settings=...).
        Alternatively, this __init__ can call an abstract method to load settings.
        """
        self.logger = MCPServersLogger.get_logger(self.__class__.__name__)

        self.http_client: Optional[httpx.AsyncClient] = None

        self.rate_limit_state: Dict[str, Any] = {
            "last_second_reset_ts": time.time(),
            "second_count": 0,
        }

        self._settings = self._load_and_validate_settings(host, port, **kwargs)

        self.mcp_server = MCPServer(
            name=self.settings.SERVER_NAME,
            port=self.settings.PORT,
            host=self.settings.HOST,
            log_level="WARNING",
        )

        self._log_initial_config()

    @property
    @abstractmethod
    def settings(self) -> BaseMCPServerSettings:
        return self._settings

    def _log_initial_config(self):
        settings_dict = self.settings.model_dump()
        max_field_length = max([len(f) for f in settings_dict.keys()])
        max_field_val_length = max([len(str(f)) for f in settings_dict.values()])
        title = f"{self.settings.SERVER_NAME} Configuration"
        table_width = (
            max(max_field_length + max_field_val_length, len(title)) + 2
        )  # buffer
        if table_width % 2 != 0:
            table_width -= 1
        decorated_title = f"{'=' * ((table_width - len(title)) // 2)} {title} {'=' * ((table_width - len(title)) // 2)}"
        self.logger.info(decorated_title)
        for field in sorted(settings_dict.keys()):
            if "API_KEY" in field:
                continue
            field_val = settings_dict.get(field)
            table_record = f"|  {field}: {field_val}"
            self.logger.info(
                f"{table_record} {' ' * (len(decorated_title) - len(table_record) - 1)}|"
            )
        self.logger.info("=" * len(decorated_title))

    @abstractmethod
    def _load_and_validate_settings(
        self,
        host: str,
        port: int,
        **kwargs,
    ) -> BaseMCPServerSettings:
        """
        Derived classes must implement this to load their specific Pydantic settings model.
        This model should inherit from BaseMCPServerSettings.
        Example: return MySpecificServerSettings()
        """
        pass

    @abstractmethod
    async def _register_tools(self) -> None:
        """
        Derived classes must implement this to register their specific tools
        with the FastMCP instance.
        Example:
            @mcp_server.tool()
            async def my_tool(arg1: str) -> str:
                # ... tool logic ...
                return "result"
        """
        pass

    async def start(self):
        await self._register_tools()

        if not self.mcp_server or not self.mcp_server.streamable_http_app:
            self.logger.critical(
                "FastMCP server application not initialized correctly."
            )
            raise MCPToolConfigurationError(
                "FastMCP and/or streamable_http_app not available."
            )

        _ = asyncio.create_task(self.mcp_server.run_streamable_http_async())
        while not self.mcp_server.server:
            await asyncio.sleep(0.1)
        while not self.mcp_server.server.started:
            await asyncio.sleep(0.1)

    async def stop(self):
        try:
            if (
                self.mcp_server
                and self.mcp_server.server
                and self.mcp_server.server_task
            ):
                self.mcp_server.server.should_exit = True
                await self.mcp_server.server.shutdown()
                self.logger.info(f"Shutdown {self.settings.SERVER_NAME}")
        except Exception as exp:
            print("unknown exception occured while stopping Streamable HTTP server")
            self.logger.exception(exp)

    async def await_server_task(self):
        if self.mcp_server.server_task:
            await self.mcp_server.server_task
        else:
            self.logger.warning("There is no active server task to await")

    def get_mcp_server_streamable_http(self) -> MCPServerStreamableHTTP:
        """Returns an MCPServerStreamableHTTP."""
        if not self.settings:
            raise MCPToolConfigurationError(
                "Settings not loaded, cannot generate MCPServerHTTP URL."
            )
        return MCPServerStreamableHTTP(
            url=f"http://{self.settings.HOST}:{self.settings.PORT}/mcp"
        )

    def _register_mcp_server_tool(
        self,
        fn: Callable,
        read_only: bool = False,
        destructive: bool = True,
        idempotent: bool = False,
        open_world: bool = True,
    ) -> None:
        self.mcp_server.add_tool(
            fn=fn,
            name=fn.__name__,
            description=fn.__doc__,
            annotations=ToolAnnotations(
                title=fn.__name__.replace("_", " ").strip(),
                readOnlyHint=read_only,
                destructiveHint=destructive,
                idempotentHint=idempotent,
                openWorldHint=open_world,
            ),
        )


class MCPServerHttpBaseSettings(BaseMCPServerSettings):
    """Base settings for all MCP servers using HTTP."""

    HTTP_CLIENT_TIMEOUT: float = 60.0
    # Default rate limit: 5 requests per second. Servers can override.
    RATE_LIMIT_PER_SECOND: Optional[int] = 50

    model_config = BaseMCPServerSettings.model_config


class MCPServerHttpBase(AbstractMCPServer):

    async def start(self):
        await self._init_http_client()
        return await super().start()

    async def stop(self):
        await self._close_http_client()
        return await super().stop()

    def _get_http_client_config(self) -> Dict[str, Any]:
        """
        Derived classes can override this to provide specific configuration
        for the httpx.AsyncClient (e.g., base_url, auth, headers).
        If this returns an empty dict or None, no HTTP client will be initialized.
        """
        return {}

    async def _init_http_client(self) -> None:
        """Initializes the httpx.AsyncClient if configured."""
        if self.http_client:  # Already initialized
            return

        client_config = self._get_http_client_config()
        if client_config:
            # Ensure base_url, if present, ends with a slash for httpx
            if (
                "base_url" in client_config
                and client_config["base_url"]
                and not client_config["base_url"].endswith("/")
            ):
                client_config["base_url"] += "/"

            self.http_client = httpx.AsyncClient(
                timeout=self.settings.HTTP_CLIENT_TIMEOUT,
                follow_redirects=True,  # Common default
                **client_config,
            )
            self.logger.debug(
                f"HTTP client initialized for {self.settings.SERVER_NAME} with config: {client_config.get('base_url', 'N/A')}."
            )
        else:
            self.logger.warning(
                f"No HTTP client configuration provided for {self.settings.SERVER_NAME}."
            )

    async def _close_http_client(self) -> None:
        """Closes the httpx.AsyncClient if it exists."""
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None
            self.logger.debug(f"HTTP client closed for {self.settings.SERVER_NAME}.")

    def _check_rate_limit(self) -> None:
        """
        Checks client-side rate limits. Base implementation handles per-second limit
        if `settings.RATE_LIMIT_PER_SECOND` is configured.
        Derived classes can override or extend this for more complex limits.
        Raises MCPRateLimitError if a limit is exceeded.
        """
        now = time.time()

        # Per-second check
        if (
            self.settings.RATE_LIMIT_PER_SECOND
            and self.settings.RATE_LIMIT_PER_SECOND > 0
        ):
            if now - self.rate_limit_state.get("last_second_reset_ts", 0) >= 1.0:
                self.rate_limit_state["second_count"] = 0
                self.rate_limit_state["last_second_reset_ts"] = now

            current_second_count = self.rate_limit_state.get("second_count", 0)
            if current_second_count >= self.settings.RATE_LIMIT_PER_SECOND:
                msg = f"Client-side per-second rate limit ({self.settings.RATE_LIMIT_PER_SECOND}) exceeded."
                self.logger.warning(msg)
                raise MCPRateLimitError(msg)
            self.rate_limit_state["second_count"] = current_second_count + 1

    async def _make_get_request_with_retry(self, endpoint: str, params: Dict[str, Any]):
        self._check_rate_limit()
        if not self.http_client:  # Should be initialized by start()
            self.logger.error("HTTP client not initialized before search.")
            raise MCPToolConfigurationError("HTTP client not initialized.")

        max_retries = 1
        base_retry_delay = 2.0
        raw_response_text_for_debug = ""

        query = params.get("q")
        if not query:
            # fallback to try `query`
            query = params.get("query")
        if not query:
            query = "<query-not-provided>"

        for attempt in range(max_retries + 1):
            self._check_rate_limit()

            try:
                if attempt > 0:
                    actual_delay = base_retry_delay * (2 ** (attempt - 1))
                    self.logger.info(
                        f"Retrying Brave Search API request (attempt {attempt + 1}/{max_retries + 1}) after {actual_delay:.1f}s delay."
                    )

                    self.logger.info(f"Retry query: {query}")

                    await asyncio.sleep(actual_delay)

                self.logger.info(
                    f"Querying (Attempt {attempt + 1}): {endpoint} with params {params}"
                )
                response = await self.http_client.get(endpoint, params=params)

                try:
                    await response.aread()
                    response_bytes = response.content
                    encoding_to_try = (
                        response.charset_encoding or response.encoding or "utf-8"
                    )
                    try:
                        raw_response_text_for_debug = response_bytes.decode(
                            encoding_to_try
                        )
                    except (UnicodeDecodeError, LookupError) as decode_err:
                        self.logger.warning(
                            f"Decoding with '{encoding_to_try}' failed: {decode_err}. Falling back to utf-8 with 'replace'."
                        )
                        raw_response_text_for_debug = response_bytes.decode(
                            "utf-8", errors="replace"
                        )
                except Exception as text_ex:
                    raw_response_text_for_debug = f"<Could not read/decode response content: {type(text_ex).__name__} - {text_ex}>"
                    self.logger.warning(
                        f"Error reading/decoding response content: {text_ex}"
                    )

                response.raise_for_status()

                content_type = response.headers.get("content-type", "").lower()
                if "application/json" not in content_type:
                    error_detail = f"Status: {response.status_code}, Content-Type: {content_type}. Body: {raw_response_text_for_debug[:200]}"
                    raise MCPUpstreamServiceError(
                        f"Request did not return JSON as expected. {error_detail}",
                        status_code=response.status_code,
                    )

                return response.json()

            except httpx.HTTPStatusError as e:
                reason_phrase_val = e.response.reason_phrase
                if isinstance(reason_phrase_val, bytes):
                    reason_phrase_val = reason_phrase_val.decode(
                        "utf-8", errors="replace"
                    )

                error_message = (
                    f"Request error: {e.response.status_code} {reason_phrase_val}"
                )
                self.logger.error(
                    f"{error_message} - URL: {e.request.url} - Response: {raw_response_text_for_debug[:500]}"
                )

                if (
                    e.response.status_code == 429 and attempt < max_retries
                ):  # Too Many Requests
                    self.logger.warning(f"Returned 429 for '{query}'. Will retry.")
                    continue
                else:
                    raise MCPUpstreamServiceError(
                        error_message,
                        status_code=e.response.status_code,
                        details=raw_response_text_for_debug[:500],
                    ) from e

            except httpx.RequestError as e:  # Network errors, timeouts
                error_message = (
                    f"'{query}' failed with network error: {type(e).__name__} - {e}"
                )
                if attempt < max_retries:
                    self.logger.warning(f"{error_message}. Will retry.")
                    continue
                else:
                    self.logger.error(
                        f"{error_message} after {max_retries + 1} attempts."
                    )
                    raise MCPUpstreamServiceError(
                        f"{error_message} after {max_retries + 1} attempts."
                    ) from e

            except (
                ValueError
            ) as e:  # Includes JSONDecodeError, Pydantic ValidationError
                error_message = f"Error processing response: {type(e).__name__} - {e}"
                self.logger.error(
                    f"{error_message}\nRaw response snippet: {raw_response_text_for_debug[:500]}"
                )
                raise MCPUpstreamServiceError(
                    error_message, details=raw_response_text_for_debug[:500]
                ) from e

            except MCPRateLimitError:  # Re-raise our own rate limit error
                raise

            except Exception as e:  # Other unexpected errors
                error_message = f"Unexpected error: {type(e).__name__} - {e}"
                self.logger.error(error_message)  # Log with stack trace
                if raw_response_text_for_debug:
                    self.logger.debug(
                        f"Raw text during unexpected error for '{query}': {raw_response_text_for_debug[:1000]}"
                    )
                raise MCPUpstreamServiceError(error_message) from e

        # Should only be reached if all retries for specific errors were exhausted
        final_error_message = f"Request failed after {max_retries + 1} attempts."
        self.logger.error(final_error_message)
        raise MCPUpstreamServiceError(final_error_message)

    async def _make_post_request_with_retry(
        self, endpoint: str, payload: Dict[str, Any]
    ):
        self._check_rate_limit()
        if not self.http_client:  # Should be initialized by start()
            self.logger.error("HTTP client not initialized before search.")
            raise MCPToolConfigurationError("HTTP client not initialized.")

        max_retries = 1
        base_retry_delay = 2.0
        raw_response_text_for_debug = ""

        for attempt in range(max_retries + 1):
            self._check_rate_limit()

            try:
                if attempt > 0:
                    actual_delay = base_retry_delay * (2 ** (attempt - 1))
                    self.logger.info(
                        f"Retrying Brave Search API request (attempt {attempt + 1}/{max_retries + 1}) after {actual_delay:.1f}s delay."
                    )

                    self.logger.info("Retry request")

                    await asyncio.sleep(actual_delay)

                self.logger.info(
                    f"Querying (Attempt {attempt + 1}): {endpoint} with params {payload}"
                )
                response = await self.http_client.post(endpoint, json=payload)

                try:
                    await response.aread()
                    response_bytes = response.content
                    encoding_to_try = (
                        response.charset_encoding or response.encoding or "utf-8"
                    )
                    try:
                        raw_response_text_for_debug = response_bytes.decode(
                            encoding_to_try
                        )
                    except (UnicodeDecodeError, LookupError) as decode_err:
                        self.logger.warning(
                            f"Decoding with '{encoding_to_try}' failed: {decode_err}. Falling back to utf-8 with 'replace'."
                        )
                        raw_response_text_for_debug = response_bytes.decode(
                            "utf-8", errors="replace"
                        )
                except Exception as text_ex:
                    raw_response_text_for_debug = f"<Could not read/decode response content: {type(text_ex).__name__} - {text_ex}>"
                    self.logger.warning(
                        f"Error reading/decoding response content: {text_ex}"
                    )

                response.raise_for_status()

                content_type = response.headers.get("content-type", "").lower()
                if "application/json" not in content_type:
                    error_detail = f"Status: {response.status_code}, Content-Type: {content_type}. Body: {raw_response_text_for_debug[:200]}"
                    raise MCPUpstreamServiceError(
                        f"Request did not return JSON as expected. {error_detail}",
                        status_code=response.status_code,
                    )

                return response.json()

            except httpx.HTTPStatusError as e:
                reason_phrase_val = e.response.reason_phrase
                if isinstance(reason_phrase_val, bytes):
                    reason_phrase_val = reason_phrase_val.decode(
                        "utf-8", errors="replace"
                    )

                error_message = (
                    f"Request error: {e.response.status_code} {reason_phrase_val}"
                )
                self.logger.error(
                    f"{error_message} - URL: {e.request.url} - Response: {raw_response_text_for_debug[:500]}"
                )

                if (
                    e.response.status_code == 429 and attempt < max_retries
                ):  # Too Many Requests
                    self.logger.warning("Returned 429. Will retry.")
                    continue
                else:
                    raise MCPUpstreamServiceError(
                        error_message,
                        status_code=e.response.status_code,
                        details=raw_response_text_for_debug[:500],
                    ) from e

            except httpx.RequestError as e:  # Network errors, timeouts
                error_message = (
                    f"Request ailed with network error: {type(e).__name__} - {e}"
                )
                if attempt < max_retries:
                    self.logger.info(f"{error_message}. Will retry.")
                    continue
                else:
                    self.logger.error(
                        f"{error_message} after {max_retries + 1} attempts."
                    )
                    raise MCPUpstreamServiceError(
                        f"{error_message} after {max_retries + 1} attempts."
                    ) from e

            except (
                ValueError
            ) as e:  # Includes JSONDecodeError, Pydantic ValidationError
                error_message = f"Error processing response: {type(e).__name__} - {e}"
                self.logger.error(
                    f"{error_message}\nRaw response snippet: {raw_response_text_for_debug[:500]}"
                )
                raise MCPUpstreamServiceError(
                    error_message, details=raw_response_text_for_debug[:500]
                ) from e

            except MCPRateLimitError:  # Re-raise our own rate limit error
                raise

            except Exception as e:  # Other unexpected errors
                error_message = f"Unexpected error: {type(e).__name__} - {e}"
                self.logger.error(error_message)  # Log with stack trace
                if raw_response_text_for_debug:
                    self.logger.debug(
                        f"Raw text during unexpected error: {raw_response_text_for_debug[:1000]}"
                    )
                raise MCPUpstreamServiceError(error_message) from e

        # Should only be reached if all retries for specific errors were exhausted
        final_error_message = f"Request failed after {max_retries + 1} attempts."
        self.logger.error(final_error_message)
        raise MCPUpstreamServiceError(final_error_message)
