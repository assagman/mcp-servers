import time
import asyncio
import logging
import httpx
import uvicorn
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from mcp.server.fastmcp import FastMCP
from pydantic_ai.mcp import MCPServerHTTP

from mcp_servers.exceptions import MCPRateLimitError, MCPToolConfigurationError, MCPUpstreamServiceError
from mcp_servers.logger import MCPServersLogger
from mcp_servers import DEFAULT_ENV_FILE


class BaseMCPServerSettings(BaseSettings):
    """Base settings for all MCP servers."""
    SERVER_NAME: str
    HOST: str = "0.0.0.0"
    PORT: int
    LOG_LEVEL: int = logging.INFO
    HTTP_CLIENT_TIMEOUT: float = 60.0
    # Default rate limit: 5 requests per second. Servers can override.
    RATE_LIMIT_PER_SECOND: Optional[int] = 50

    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        extra="ignore", # Ignore extra fields from .env
        case_sensitive=False
    )


class AbstractMCPServer(ABC):
    """
    Abstract Base Class for MCP (Multi-Capability Provider) Servers.
    Provides a common structure for lifecycle management, HTTP client handling,
    rate limiting, and Uvicorn server setup.
    """

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None):
        """
        Initializes the server. Derived classes are expected to load their
        specific settings in their __init__ and pass them to super().__init__(settings=...).
        Alternatively, this __init__ can call an abstract method to load settings.
        """
        self.host_override = host
        self.port_override = port

        self.logger = MCPServersLogger.get_logger(self.__class__.__name__)

        self.http_client: Optional[httpx.AsyncClient] = None
        self.mcp_server_instance: Optional[FastMCP] = None
        self.uvicorn_server: Optional[uvicorn.Server] = None
        self.serve_task: Optional[asyncio.Task] = None

        self.rate_limit_state: Dict[str, Any] = {
            "last_second_reset_ts": time.time(),
            "second_count": 0,
        }

        self._settings = self._load_and_validate_settings()
        self.override_settings()

        self._log_initial_config()

    def override_settings(self):
        if self.host_override:
            self._settings.HOST = self.host_override

        if self.port_override:
            self._settings.PORT = self.port_override


    @property
    @abstractmethod
    def settings(self) -> BaseMCPServerSettings:
        return self._settings

    @abstractmethod
    def _log_initial_config(self):
        pass

    @abstractmethod
    def _load_and_validate_settings(self) -> BaseMCPServerSettings:
        """
        Derived classes must implement this to load their specific Pydantic settings model.
        This model should inherit from BaseMCPServerSettings.
        Example: return MySpecificServerSettings()
        """
        pass

    @abstractmethod
    async def _register_tools(self, mcp_server: FastMCP) -> None:
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


    async def start(self) -> asyncio.Task:
        """
        Starts the MCP server, including initializing the HTTP client (if any),
        registering tools, and launching the Uvicorn server.
        """
        self.logger.info(f"Starting {self.settings.SERVER_NAME}...")

        self.mcp_server_instance = FastMCP(
            name=self.settings.SERVER_NAME,
            port=self.settings.PORT, # FastMCP uses this to know its port, but Uvicorn actually binds
            host=self.settings.HOST  # Same as above
        )

        await self._register_tools(self.mcp_server_instance)

        if not self.mcp_server_instance or not self.mcp_server_instance.sse_app:
            self.logger.critical("FastMCP server application not initialized correctly.")
            raise MCPToolConfigurationError("FastMCP sse_app not available.")

        uviconfig = uvicorn.Config(
            self.mcp_server_instance.sse_app,
            host=self.settings.HOST,
            port=self.settings.PORT,
            log_level=self.settings.LOG_LEVEL,
            factory=True,
        )
        self.uvicorn_server = uvicorn.Server(uviconfig)

        self.serve_task = asyncio.create_task(self._run_uvicorn_server_wrapper())

        # Wait for Uvicorn to actually start
        if self.uvicorn_server:
            while not self.uvicorn_server.started:
                await asyncio.sleep(0.01) # Short sleep to yield control
            self.logger.info(
                f"{self.settings.SERVER_NAME} (Uvicorn) started and listening on http://{self.settings.HOST}:{self.settings.PORT}"
            )
        else:
            # This case should ideally not be reached if logic is correct
            self.logger.error(f"Uvicorn server for {self.settings.SERVER_NAME} not initialized after start attempt.")
            raise MCPToolConfigurationError(f"Uvicorn server for {self.settings.SERVER_NAME} failed to initialize.")

        return self.serve_task

    async def _run_uvicorn_server_wrapper(self):
        """Helper to run and await uvicorn server, catching potential errors during serve()."""
        if not self.uvicorn_server:
            self.logger.error(f"Uvicorn server not initialized for {self.settings.SERVER_NAME} in _run_uvicorn_server_wrapper.")
            return
        try:
            await self.uvicorn_server.serve()
        except Exception as e:
            self.logger.error(
                f"Uvicorn server for {self.settings.SERVER_NAME} encountered an error during serve: {e}"
            )
        finally:
            self.logger.info(f"Uvicorn server for {self.settings.SERVER_NAME} has shut down.")

    async def stop(self) -> None:
        """Gracefully stops the MCP server and Uvicorn."""
        self.logger.info(f"Attempting to shut down {self.settings.SERVER_NAME}...")

        if self.uvicorn_server and self.uvicorn_server.started:
            self.logger.info(f"Requesting Uvicorn server ({self.settings.SERVER_NAME}) to exit.")
            self.uvicorn_server.should_exit = True

        if self.serve_task and not self.serve_task.done():
            self.logger.info(f"Waiting for {self.settings.SERVER_NAME} Uvicorn task to complete...")
            try:
                await asyncio.wait_for(self.serve_task, timeout=10.0)
            except asyncio.TimeoutError:
                self.logger.warning(
                    f"{self.settings.SERVER_NAME} Uvicorn task timed out during shutdown, cancelling."
                )
                self.serve_task.cancel()
                try:
                    await self.serve_task # Await cancellation
                except asyncio.CancelledError:
                    self.logger.info(
                        f"{self.settings.SERVER_NAME} Uvicorn task successfully cancelled."
                    )
                except Exception as e:
                    self.logger.error(
                        f"Error during {self.settings.SERVER_NAME} Uvicorn task cancellation: {e}"
                    )
            except Exception as e:
                self.logger.error(
                    f"Exception during {self.settings.SERVER_NAME} serve_task await on stop: {e}"
                )
        else:
            self.logger.info(
                f"{self.settings.SERVER_NAME} Uvicorn task already completed or not started."
            )

        self.logger.info(f"{self.settings.SERVER_NAME} stop sequence completed.")

    def get_mcp_server_http(self) -> MCPServerHTTP:
        """Returns an MCPServerHTTP client configuration for this server."""
        if not self.settings:
             raise MCPToolConfigurationError("Settings not loaded, cannot generate MCPServerHTTP URL.")
        return MCPServerHTTP(url=f"http://{self.settings.HOST}:{self.settings.PORT}/sse")


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
        if self.http_client: # Already initialized
            return

        client_config = self._get_http_client_config()
        if client_config:
            # Ensure base_url, if present, ends with a slash for httpx
            if "base_url" in client_config and client_config["base_url"] and not client_config["base_url"].endswith('/'):
                client_config["base_url"] += '/'

            self.http_client = httpx.AsyncClient(
                timeout=self.settings.HTTP_CLIENT_TIMEOUT,
                follow_redirects=True, # Common default
                **client_config
            )
            self.logger.info(f"HTTP client initialized for {self.settings.SERVER_NAME} with config: {client_config.get('base_url', 'N/A')}.")
        else:
            self.logger.info(f"No HTTP client configuration provided for {self.settings.SERVER_NAME}.")

    async def _close_http_client(self) -> None:
        """Closes the httpx.AsyncClient if it exists."""
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None
            self.logger.info(f"HTTP client closed for {self.settings.SERVER_NAME}.")

    def _check_rate_limit(self) -> None:
        """
        Checks client-side rate limits. Base implementation handles per-second limit
        if `settings.RATE_LIMIT_PER_SECOND` is configured.
        Derived classes can override or extend this for more complex limits.
        Raises MCPRateLimitError if a limit is exceeded.
        """
        now = time.time()

        # Per-second check
        if self.settings.RATE_LIMIT_PER_SECOND and self.settings.RATE_LIMIT_PER_SECOND > 0:
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

                self.logger.debug(
                    f"Querying (Attempt {attempt + 1}): {endpoint} with params {params}"
                )
                response = await self.http_client.get(endpoint, params=params)

                try:
                    await response.aread()
                    response_bytes = response.content
                    encoding_to_try = response.charset_encoding or response.encoding or "utf-8"
                    try:
                        raw_response_text_for_debug = response_bytes.decode(encoding_to_try)
                    except (UnicodeDecodeError, LookupError) as decode_err:
                        self.logger.warning(
                            f"Decoding with '{encoding_to_try}' failed: {decode_err}. Falling back to utf-8 with 'replace'."
                        )
                        raw_response_text_for_debug = response_bytes.decode("utf-8", errors="replace")
                except Exception as text_ex:
                    raw_response_text_for_debug = f"<Could not read/decode response content: {type(text_ex).__name__} - {text_ex}>"
                    self.logger.warning(f"Error reading/decoding response content: {text_ex}")

                response.raise_for_status()

                content_type = response.headers.get("content-type", "").lower()
                if "application/json" not in content_type:
                    error_detail = f"Status: {response.status_code}, Content-Type: {content_type}. Body: {raw_response_text_for_debug[:200]}"
                    raise MCPUpstreamServiceError(f"Request did not return JSON as expected. {error_detail}", status_code=response.status_code)

                return response.json()

            except httpx.HTTPStatusError as e:
                reason_phrase_val = e.response.reason_phrase
                if isinstance(reason_phrase_val, bytes):
                    reason_phrase_val = reason_phrase_val.decode("utf-8", errors="replace")

                error_message = f"Request error: {e.response.status_code} {reason_phrase_val}"
                self.logger.error(f"{error_message} - URL: {e.request.url} - Response: {raw_response_text_for_debug[:500]}")

                if e.response.status_code == 429 and attempt < max_retries: # Too Many Requests
                    self.logger.info(f"Returned 429 for '{query}'. Will retry.")
                    continue
                else:
                    raise MCPUpstreamServiceError(error_message, status_code=e.response.status_code, details=raw_response_text_for_debug[:500]) from e

            except httpx.RequestError as e: # Network errors, timeouts
                error_message = f"'{query}' failed with network error: {type(e).__name__} - {e}"
                if attempt < max_retries:
                    self.logger.info(f"{error_message}. Will retry.")
                    continue
                else:
                    self.logger.error(f"{error_message} after {max_retries + 1} attempts.")
                    raise MCPUpstreamServiceError(f"{error_message} after {max_retries + 1} attempts.") from e

            except ValueError as e: # Includes JSONDecodeError, Pydantic ValidationError
                error_message = f"Error processing response: {type(e).__name__} - {e}"
                self.logger.error(f"{error_message}\nRaw response snippet: {raw_response_text_for_debug[:500]}")
                raise MCPUpstreamServiceError(error_message, details=raw_response_text_for_debug[:500]) from e

            except MCPRateLimitError: # Re-raise our own rate limit error
                raise

            except Exception as e: # Other unexpected errors
                error_message = f"Unexpected error: {type(e).__name__} - {e}"
                self.logger.error(error_message) # Log with stack trace
                if raw_response_text_for_debug:
                     self.logger.debug(f"Raw text during unexpected error for '{query}': {raw_response_text_for_debug[:1000]}")
                raise MCPUpstreamServiceError(error_message) from e

        # Should only be reached if all retries for specific errors were exhausted
        final_error_message = f"Request failed after {max_retries + 1} attempts."
        self.logger.error(final_error_message)
        raise MCPUpstreamServiceError(final_error_message)


    async def _make_post_request_with_retry(self, endpoint: str, payload: Dict[str, Any]):
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

                self.logger.debug(
                    f"Querying (Attempt {attempt + 1}): {endpoint} with params {payload}"
                )
                response = await self.http_client.post(endpoint, json=payload)

                try:
                    await response.aread()
                    response_bytes = response.content
                    encoding_to_try = response.charset_encoding or response.encoding or "utf-8"
                    try:
                        raw_response_text_for_debug = response_bytes.decode(encoding_to_try)
                    except (UnicodeDecodeError, LookupError) as decode_err:
                        self.logger.warning(
                            f"Decoding with '{encoding_to_try}' failed: {decode_err}. Falling back to utf-8 with 'replace'."
                        )
                        raw_response_text_for_debug = response_bytes.decode("utf-8", errors="replace")
                except Exception as text_ex:
                    raw_response_text_for_debug = f"<Could not read/decode response content: {type(text_ex).__name__} - {text_ex}>"
                    self.logger.warning(f"Error reading/decoding response content: {text_ex}")

                response.raise_for_status()

                content_type = response.headers.get("content-type", "").lower()
                if "application/json" not in content_type:
                    error_detail = f"Status: {response.status_code}, Content-Type: {content_type}. Body: {raw_response_text_for_debug[:200]}"
                    raise MCPUpstreamServiceError(f"Request did not return JSON as expected. {error_detail}", status_code=response.status_code)

                return response.json()

            except httpx.HTTPStatusError as e:
                reason_phrase_val = e.response.reason_phrase
                if isinstance(reason_phrase_val, bytes):
                    reason_phrase_val = reason_phrase_val.decode("utf-8", errors="replace")

                error_message = f"Request error: {e.response.status_code} {reason_phrase_val}"
                self.logger.error(f"{error_message} - URL: {e.request.url} - Response: {raw_response_text_for_debug[:500]}")

                if e.response.status_code == 429 and attempt < max_retries: # Too Many Requests
                    self.logger.info("Returned 429. Will retry.")
                    continue
                else:
                    raise MCPUpstreamServiceError(error_message, status_code=e.response.status_code, details=raw_response_text_for_debug[:500]) from e

            except httpx.RequestError as e: # Network errors, timeouts
                error_message = f"Request ailed with network error: {type(e).__name__} - {e}"
                if attempt < max_retries:
                    self.logger.info(f"{error_message}. Will retry.")
                    continue
                else:
                    self.logger.error(f"{error_message} after {max_retries + 1} attempts.")
                    raise MCPUpstreamServiceError(f"{error_message} after {max_retries + 1} attempts.") from e

            except ValueError as e: # Includes JSONDecodeError, Pydantic ValidationError
                error_message = f"Error processing response: {type(e).__name__} - {e}"
                self.logger.error(f"{error_message}\nRaw response snippet: {raw_response_text_for_debug[:500]}")
                raise MCPUpstreamServiceError(error_message, details=raw_response_text_for_debug[:500]) from e

            except MCPRateLimitError: # Re-raise our own rate limit error
                raise

            except Exception as e: # Other unexpected errors
                error_message = f"Unexpected error: {type(e).__name__} - {e}"
                self.logger.error(error_message) # Log with stack trace
                if raw_response_text_for_debug:
                     self.logger.debug(f"Raw text during unexpected error: {raw_response_text_for_debug[:1000]}")
                raise MCPUpstreamServiceError(error_message) from e

        # Should only be reached if all retries for specific errors were exhausted
        final_error_message = f"Request failed after {max_retries + 1} attempts."
        self.logger.error(final_error_message)
        raise MCPUpstreamServiceError(final_error_message)
