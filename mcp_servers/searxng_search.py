#!/usr/bin/env python

import os
import sys
import time
import asyncio
import httpx
import uvicorn
from typing import List, Optional, Dict, Union, Any
import builtins # Import builtins for robust access to str, type, etc.

from dotenv import load_dotenv
from pydantic import BaseModel, HttpUrl, Field

from mcp.server.fastmcp import FastMCP


# Load environment variables from .env file if present
load_dotenv()

# --- Pydantic Models for SearxNG API Responses ---

class SearXNGResult(BaseModel):
    url: HttpUrl
    title: str
    content: Optional[str] = None
    engine: Optional[str] = None
    template: Optional[str] = None
    category: Optional[str] = None
    img_src: Optional[str] = None
    thumbnail: Optional[str] = None

class SearXNGInfobox(BaseModel):
    infobox: Optional[str] = None
    id: Optional[str] = None
    content: Optional[str] = None
    links: Optional[List[Dict[str, str]]] = None
    img_src: Optional[str] = None

class SearXNGResponse(BaseModel):
    query: Optional[str] = None # SearxNG sometimes omits query if it's an exact match for an answer/infobox
    results: List[SearXNGResult] = Field(default_factory=list)
    infoboxes: List[SearXNGInfobox] = Field(default_factory=list)
    suggestions: Union[List[str], Dict[str, List[str]]] = Field(default_factory=list)
    answers: List[str] = Field(default_factory=list)
    corrections: List[str] = Field(default_factory=list)
    unresponsive_engines: List[List[Any]] = Field(default_factory=list)

# --- SearxNG Search Server Class ---

class MCPServerSearXNG:
    def __init__(self):
        self.SEARXNG_BASE_URL = os.getenv("SEARXNG_BASE_URL")
        if not self.SEARXNG_BASE_URL:
            print("Error: SEARXNG_BASE_URL environment variable is required.", file=sys.stderr)
            sys.exit(1)
        if not self.SEARXNG_BASE_URL.endswith('/'):
            self.SEARXNG_BASE_URL += '/'

        self.SEARXNG_USERNAME = os.getenv("SEARXNG_USERNAME")
        self.SEARXNG_PASSWORD = os.getenv("SEARXNG_PASSWORD")

        self.SERVER_NAME = "MCP_SERVER_SEARXNG_SEARCH"
        self.SERVER_HOST = os.getenv("SEARXNG_MCP_HOST", "0.0.0.0")
        self.SERVER_PORT = int(os.getenv("SEARXNG_MCP_PORT", 8767))

        self.rate_limit_config = {
            "per_second": int(os.getenv("SEARXNG_RATE_LIMIT_PER_SECOND", "2")),
        }
        self.rate_limit_state = {
            "second_count": 0,
            "last_second_reset_ts": time.time()
        }

        self.http_client: Optional[httpx.AsyncClient] = None
        self.server: Optional[FastMCP] = None
        self.uvicorn_server: Optional[uvicorn.Server] = None
        self.serve_task: Optional[asyncio.Task] = None

        print(f"INFO: {self.SERVER_NAME}")
        print(f"INFO: Targeting SearxNG instance: {self.SEARXNG_BASE_URL}")
        if self.SEARXNG_USERNAME:
            print("INFO: Using HTTP Basic Auth for SearxNG.")
        print(f"INFO: Server will run on http://{self.SERVER_HOST}:{self.SERVER_PORT}")
        print(f"INFO: Client-side rate limit: {self.rate_limit_config['per_second']} req/sec. Note that the SearxNG instance may have its own stricter limits.")


    def _check_rate_limit(self):
        now = time.time()
        if now - self.rate_limit_state["last_second_reset_ts"] >= 1.0:
            self.rate_limit_state["second_count"] = 0
            self.rate_limit_state["last_second_reset_ts"] = now

        if self.rate_limit_state["second_count"] >= self.rate_limit_config["per_second"]:
            raise Exception(f"Client-side rate limit per second ({self.rate_limit_config['per_second']}) exceeded. Try again shortly.")
        self.rate_limit_state["second_count"] += 1

    async def _init_client(self):
        if not self.http_client:
            auth = None
            if self.SEARXNG_USERNAME and self.SEARXNG_PASSWORD:
                auth = httpx.BasicAuth(self.SEARXNG_USERNAME, self.SEARXNG_PASSWORD)
            self.http_client = httpx.AsyncClient(
                base_url=self.SEARXNG_BASE_URL,
                headers={'Accept': 'application/json'},
                auth=auth,
                timeout=20.0,
                follow_redirects=True
            )

    async def _close_client(self):
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None

    def _format_searxng_results(self, data: SearXNGResponse) -> str:
        output_parts = []

        if data.query:
            output_parts.append(f"Search Query: {data.query}")

        if data.answers:
            output_parts.append("\n--- Answers ---")
            for ans in data.answers:
                output_parts.append(f"- {ans}")

        if data.infoboxes:
            output_parts.append("\n--- Infoboxes ---")
            for info in data.infoboxes:
                box_parts = []
                if info.infobox: box_parts.append(f"Type: {info.infobox}")
                if info.content: box_parts.append(f"Content: {info.content}")
                if info.img_src: box_parts.append(f"Image: {info.img_src}")
                if info.links:
                    link_strs = []
                    for link_dict in info.links:
                        if link_dict.get("text") and link_dict.get("href"):
                             link_strs.append(f"{link_dict['text']}: {link_dict['href']}")
                    if link_strs:
                        box_parts.append("Links:\n  - " + "\n  - ".join(link_strs))
                if box_parts:
                    output_parts.append("\n".join(box_parts))


        if data.results:
            output_parts.append("\n--- Search Results ---")
            for i, result in enumerate(data.results, 1):
                res_parts = [f"Result {i}:"]
                res_parts.append(f"  Title: {result.title}")
                res_parts.append(f"  URL: {result.url}")
                if result.content:
                    res_parts.append(f"  Snippet: {result.content}")
                if result.engine:
                    res_parts.append(f"  Engine: {result.engine}")
                if result.category:
                    res_parts.append(f"  Category: {result.category}")
                if result.thumbnail:
                    res_parts.append(f"  Thumbnail: {result.thumbnail}")
                output_parts.append("\n".join(res_parts))

        if data.suggestions:
            output_parts.append("\n--- Suggestions ---")
            if isinstance(data.suggestions, dict):
                for engine, sug_list in data.suggestions.items():
                    if sug_list:
                        output_parts.append(f"  From {engine}: {', '.join(sug_list)}")
            elif isinstance(data.suggestions, list):
                 if data.suggestions:
                    output_parts.append(f"  General: {', '.join(data.suggestions)}")

        if not data.results and not data.infoboxes and not data.answers and not output_parts:
             output_parts.append("No results, infoboxes, answers, or suggestions found.")
        elif not data.results and not data.infoboxes and not data.answers and data.suggestions:
            # If only suggestions were found, but no main content
            pass # Suggestions already added

        return "\n\n".join(output_parts).strip() if output_parts else "No information found."

    async def _perform_search(self, query: str, pageno: int = 1, categories: Optional[str] = None, language: str = 'en') -> str:
        if not self.http_client:
            await self._init_client()
        if not self.http_client: # Re-check after init attempt
            raise Exception("HTTP client not initialized and could not be initialized.")

        search_endpoint = "search"
        params = {
            'q': query,
            'format': 'json',
            'pageno': builtins.str(pageno) # Use builtins.str to avoid potential shadowing
        }
        if categories:
            params['categories'] = categories
        if language:
            params['language'] = language

        max_retries = 1  # Try once, then retry once more (total 2 attempts for specified errors)
        base_retry_delay = 2.0  # seconds

        raw_response_text_for_debug = "" # Store raw response for debugging if parsing fails

        for attempt in range(max_retries + 1):
            self._check_rate_limit()  # Our own client-side check first

            try:
                if attempt > 0:  # This means it's a retry
                    # Basic exponential backoff, though with max_retries=1, it's just base_retry_delay
                    actual_delay = base_retry_delay * (2 ** (attempt - 1))
                    print(f"INFO: Retrying SearxNG request for '{query}' (attempt {attempt + 1}/{max_retries + 1}) after {actual_delay:.1f}s delay.", file=sys.stderr)
                    await asyncio.sleep(actual_delay)

                print(f"DEBUG: Querying SearxNG (Attempt {attempt + 1}/{max_retries + 1}): {search_endpoint} with params {params}")
                response = await self.http_client.get(search_endpoint, params=params)

                # Get raw text for debugging *before* raise_for_status or json parsing
                # This is crucial if the response is not valid JSON or if an error page is returned
                try:
                    # Ensure the full response body is read into memory
                    await response.aread() # Use aread() for async context
                    response_bytes = response.content # This is now populated bytes

                    # Determine encoding:
                    # 1. From Content-Type header (response.charset_encoding)
                    # 2. httpx's guess if not in header (response.encoding includes this if successful)
                    # 3. Fallback to UTF-8 (common for JSON)
                    encoding_to_try = response.charset_encoding or response.encoding or 'utf-8'

                    try:
                        raw_response_text_for_debug = response_bytes.decode(encoding_to_try)
                    except (UnicodeDecodeError, LookupError) as decode_err:
                        # If primary attempt fails, fall back to UTF-8 with error replacement
                        print(f"WARNING: Decoding with '{encoding_to_try}' failed: {decode_err}. Falling back to utf-8 with 'replace'.", file=sys.stderr)
                        raw_response_text_for_debug = response_bytes.decode('utf-8', errors='replace')

                except Exception as text_ex:
                    # This catch is for errors during response.aread() or the manual .decode() attempts
                    raw_response_text_for_debug = f"<Could not read/decode response content: {builtins.type(text_ex).__name__} - {text_ex}>"
                    # This print will now reflect errors from reading/decoding bytes, not from response.text()
                    print(f"WARNING: Error reading/decoding response content from SearxNG on attempt {attempt + 1}: {builtins.type(text_ex).__name__} - {text_ex}", file=sys.stderr)
                    # Depending on the error, you might want to re-raise or continue to raise_for_status
                    # For now, we'll let it proceed to raise_for_status or JSON parsing,
                    # as raw_response_text_for_debug will contain the error context.

                response.raise_for_status()  # Raises HTTPStatusError for 4xx/5xx

                content_type = response.headers.get('content-type', '').lower()
                if 'application/json' not in content_type:
                    error_detail = f"Status: {response.status_code}, Content-Type: {content_type}. Body: {raw_response_text_for_debug[:200]}"
                    # This is a non-retryable error for this specific request attempt if the server misbehaves
                    raise Exception(f"SearxNG did not return JSON as expected. {error_detail}")

                # If we reach here, status is 2xx and content-type seems okay
                json_data = response.json() # This can raise JSONDecodeError (subclass of ValueError)
                # print(f"DEBUG: Raw JSON from SearxNG: {json_data}") # Uncomment for very verbose debugging

                data = SearXNGResponse.model_validate(json_data) # This can raise Pydantic's ValidationError
                return self._format_searxng_results(data)

            except httpx.HTTPStatusError as e:
                # Raw text was already captured above from e.response if possible
                reason_phrase_val = e.response.reason_phrase
                if isinstance(reason_phrase_val, bytes):
                    reason_phrase_val = reason_phrase_val.decode('utf-8', errors='replace')

                error_message = f"SearxNG API error: {e.response.status_code} {reason_phrase_val}\nURL: {e.request.url}\nResponse: {raw_response_text_for_debug[:500]}"

                if e.response.status_code == 429 and attempt < max_retries:
                    print(f"INFO: SearxNG returned 429 (Too Many Requests) for '{query}'. Will retry. Details: {error_message}", file=sys.stderr)
                    continue  # Go to next iteration for retry
                else:
                    # Not a 429 we can retry, or 429 on last attempt, or other HTTP error
                    raise Exception(error_message) from e

            except httpx.RequestError as e:  # Network errors, timeouts (excluding HTTPStatusError)
                error_message = f"SearxNG request for '{query}' failed with network error: {builtins.type(e).__name__} - {e}"
                if attempt < max_retries:
                    print(f"INFO: {error_message}. Will retry.", file=sys.stderr)
                    # No need for specific sleep here if the main loop's attempt > 0 handles it
                    continue
                else:
                    raise Exception(f"{error_message} after {max_retries + 1} attempts.") from e

            except ValueError as e: # Includes JSONDecodeError
                # This means the server responded with 2xx status, but the body was not valid JSON.
                # This is generally not retryable for the same request.
                error_message = f"Error decoding JSON from SearxNG for '{query}': {builtins.type(e).__name__} - {e}\nRaw response snippet: {raw_response_text_for_debug[:500]}"
                print(f"ERROR: {error_message}", file=sys.stderr) # Log details
                raise Exception(error_message) from e

            except Exception as e:  # Other unexpected errors (e.g., Pydantic validation)
                error_detail_str = builtins.str(e)
                # Check if it's a Pydantic ValidationError to provide more details
                if hasattr(e, 'errors') and callable(e.errors): # Pydantic v2 ValidationError
                    try:
                        # e.errors() returns a list of dicts, make it more readable
                        error_list = e.errors()
                        error_detail_str = f"Pydantic Validation Errors: {error_list}"
                    except Exception as pydantic_err_ex:
                        error_detail_str = f"Pydantic ValidationError (could not format errors list): {builtins.str(e)}"

                error_message = f"Error during SearxNG search processing for '{query}': {builtins.type(e).__name__} - {error_detail_str}"

                # Log the raw JSON that might have caused the validation error
                if raw_response_text_for_debug: # Check if it was populated
                     print(f"DEBUG: Raw text that may have caused processing error for '{query}': {raw_response_text_for_debug[:1000]}", file=sys.stderr)

                print(f"ERROR: {error_message}", file=sys.stderr)
                # These are generally not retryable in the same way.
                raise Exception(error_message) from e

        # This line should only be reached if all retries for 429/RequestError were exhausted without returning or raising other specific errors.
        # This implies the loop completed without success.
        final_error_message = f"SearxNG search for '{query}' failed after {max_retries + 1} attempts (likely due to repeated 429s or network errors that were retried)."
        print(f"ERROR: {final_error_message}", file=sys.stderr)
        raise Exception(final_error_message)

    async def start(self):
        await self._init_client()
        self.server = FastMCP(
            name=self.SERVER_NAME,
            port=self.SERVER_PORT
        )

        @self.server.tool()
        async def searxng_search(query: str, pageno: int = 1, categories: Optional[str] = None, language: str = 'en') -> str:
            if not isinstance(query, str) or not query.strip():
                raise ValueError("Query must be a non-empty string.")
            if not isinstance(pageno, int) or pageno < 1:
                raise ValueError("Page number (pageno) must be a positive integer.")
            if categories is not None and not isinstance(categories, str):
                raise ValueError("Categories must be a comma-separated string if provided.")
            if not isinstance(language, str) or not language:
                raise ValueError("Language must be a non-empty string.")

            try:
                # DEBUG: Check if 'str' is shadowed at the start of the tool
                if str is not builtins.str:
                    print(f"WARNING: built-in 'str' (type: {builtins.type(str)}) is shadowed at the beginning of searxng_search tool. This is unexpected.", file=sys.stderr)

                return await self._perform_search(query, pageno, categories, language)
            except Exception as e:
                # Safely log the error. The 'raise e' will propagate the actual exception to FastMCP.
                try:
                    original_exception_type_name = builtins.type(e).__name__
                    error_message_for_log = builtins.str(e) # Use builtins.str for safety

                    if str is not builtins.str:
                         print(f"WARNING: built-in 'str' (current type: {builtins.type(str)}) is shadowed in searxng_search's except block. This might have impacted previous logging attempts if 'str' was called.", file=sys.stderr)

                    print(f"ERROR in searxng_search (Type: {original_exception_type_name}): {error_message_for_log}", file=sys.stderr)
                except Exception as log_ex:
                    # Fallback if even robust logging fails
                    print(f"ERROR: Exception during error logging in searxng_search tool: {builtins.str(log_ex)}", file=sys.stderr)
                    print(f"ERROR: Original exception type in searxng_search tool was: {builtins.type(e).__name__}", file=sys.stderr)

                raise e # Re-raise the originally caught exception 'e'

        if not self.server or not self.server.sse_app:
            print("Error: FastMCP server application not initialized correctly.", file=sys.stderr)
            sys.exit(1)
        uviconfig = uvicorn.Config(self.server.sse_app, host=self.SERVER_HOST, port=self.SERVER_PORT, log_level="info")
        self.uvicorn_server = uvicorn.Server(uviconfig)
        self.serve_task = asyncio.create_task(self.uvicorn_server.serve())
        while not self.uvicorn_server.started:
            await asyncio.sleep(0.1)
        print(f"INFO: Uvicorn server started on http://{self.SERVER_HOST}:{self.SERVER_PORT}")
        return self.serve_task

    async def stop(self):
        print(f"INFO: Attempting to shut down {self.SERVER_NAME}...")
        if self.uvicorn_server: self.uvicorn_server.should_exit = True
        if self.serve_task and not self.serve_task.done():
            try:
                await asyncio.wait_for(self.serve_task, timeout=5.0)
            except asyncio.TimeoutError:
                print("INFO: Uvicorn serve task timed out, cancelling.")
                self.serve_task.cancel()
                try: await self.serve_task
                except asyncio.CancelledError: print(f"INFO: {self.SERVER_NAME} serve task successfully cancelled.")
            except Exception as e: print(f"ERROR: Exception during serve_task await: {e}", file=sys.stderr)
        await self._close_client()
        print(f"INFO: {self.SERVER_NAME} stop sequence completed.")

async def main():
    server_instance = MCPServerSearXNG()
    main_server_task = None
    try:
        main_server_task = await server_instance.start()
        if main_server_task: await main_server_task
    except KeyboardInterrupt: print("INFO: Keyboard interrupt received, shutting down...")
    except Exception as e:
        print(f"FATAL: Server failed to start or run: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
    finally:
        await server_instance.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"CRITICAL: Unhandled error in main execution: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
