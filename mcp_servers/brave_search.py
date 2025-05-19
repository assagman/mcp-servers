#!/usr/bin/env python

import os
import sys
import time
import asyncio
import httpx
import uvicorn
from typing import List, Optional, Dict

from dotenv import load_dotenv
from pydantic import BaseModel, HttpUrl, Field

from mcp.server.fastmcp import FastMCP


# Load environment variables from .env file if present
load_dotenv()

# --- Pydantic Models for Brave API Responses ---

class WebResult(BaseModel):
    title: str
    description: str
    url: HttpUrl

class WebSearchResults(BaseModel):
    results: Optional[List[WebResult]] = Field(default_factory=list)

class LocationResultHeader(BaseModel):
    id: str
    title: Optional[str] = None

class LocationHeaders(BaseModel):
    results: Optional[List[LocationResultHeader]] = Field(default_factory=list)

class BraveWebResponse(BaseModel):
    web: Optional[WebSearchResults] = None
    locations: Optional[LocationHeaders] = None

class AddressDetail(BaseModel):
    streetAddress: Optional[str] = None
    addressLocality: Optional[str] = None
    addressRegion: Optional[str] = None
    postalCode: Optional[str] = None

class Coordinates(BaseModel):
    latitude: float
    longitude: float

class Rating(BaseModel):
    ratingValue: Optional[float] = None
    ratingCount: Optional[int] = None

class BraveLocationDetail(BaseModel):
    id: str
    name: str
    address: Optional[AddressDetail] = None
    coordinates: Optional[Coordinates] = None
    phone: Optional[str] = None
    rating: Optional[Rating] = None
    openingHours: Optional[List[str]] = Field(default_factory=list)
    priceRange: Optional[str] = None

class BravePoiResponse(BaseModel):
    results: List[BraveLocationDetail] = Field(default_factory=list)

class BraveDescriptionResponse(BaseModel):
    descriptions: Dict[str, str] = Field(default_factory=dict)


# --- Brave Search Server Class ---

class MCPServerBraveSearch:
    def __init__(self):
        self.BRAVE_API_KEY = os.getenv("BRAVE_API_KEY")
        if not self.BRAVE_API_KEY:
            print("Error: BRAVE_API_KEY environment variable is required.", file=sys.stderr)
            sys.exit(1)

        self.SERVER_NAME = "MCP_SERVER_BRAVE_SEARCH"
        self.SERVER_HOST = os.getenv("MCP_SERVER_BRAVE_SEARCH_HOST", "0.0.0.0")
        self.SERVER_PORT = int(os.getenv("MCP_SERVER_BRAVE_SEARCH_PORT", 8766)) # Example port

        self.BRAVE_API_BASE_URL = "https://api.search.brave.com/res/v1"

        self.rate_limit_config = {
            "per_second": 1,
            "per_month": 15000 # Monthly limit, simple counter
        }
        self.rate_limit_state = {
            "second_count": 0,
            "month_count": 0, # This count is never reset in the TS example, only checked.
            "last_second_reset_ts": time.time()
        }

        self.http_client: Optional[httpx.AsyncClient] = None
        self.server: Optional[FastMCP] = None
        self.uvicorn_server: Optional[uvicorn.Server] = None
        self.serve_task: Optional[asyncio.Task] = None

        print(f"INFO: {self.SERVER_NAME}")
        print(f"INFO: Server will run on http://{self.SERVER_HOST}:{self.SERVER_PORT}")
        print(f"INFO: Ensure BRAVE_API_KEY is set in your environment.")

    def _check_rate_limit(self):
        now = time.time()

        # Per-second check
        if now - self.rate_limit_state["last_second_reset_ts"] >= 1.0:
            self.rate_limit_state["second_count"] = 0
            self.rate_limit_state["last_second_reset_ts"] = now

        if self.rate_limit_state["second_count"] >= self.rate_limit_config["per_second"]:
            raise Exception(f"Rate limit per second ({self.rate_limit_config['per_second']}) exceeded.")

        # Per-month check (simple increment, not a sliding window or calendar month reset)
        if self.rate_limit_state["month_count"] >= self.rate_limit_config["per_month"]:
            raise Exception(f"Rate limit per month ({self.rate_limit_config['per_month']}) exceeded.")

        self.rate_limit_state["second_count"] += 1
        self.rate_limit_state["month_count"] += 1
        # print(f"DEBUG: Rate limit state: {self.rate_limit_state}") # For debugging

    async def _init_client(self):
        if not self.http_client:
            self.http_client = httpx.AsyncClient(
                headers={
                    'Accept': 'application/json',
                    'Accept-Encoding': 'gzip',
                    'X-Subscription-Token': self.BRAVE_API_KEY
                },
                timeout=20.0 # Default timeout for requests
            )

    async def _close_client(self):
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None

    async def _perform_web_search(self, query: str, count: int = 10, offset: int = 0) -> str:
        self._check_rate_limit()
        if not self.http_client: # Should be initialized by start()
            await self._init_client()
            if not self.http_client : # Check again, if _init_client failed it would be None
                raise Exception("HTTP client not initialized")


        url = f"{self.BRAVE_API_BASE_URL}/web/search"
        params = {
            'q': query,
            'count': str(min(count, 20)), # API limit is 20
            'offset': str(offset)
        }

        try:
            response = await self.http_client.get(url, params=params)
            response.raise_for_status() # Raise an exception for bad status codes
            data = BraveWebResponse.model_validate(response.json())
        except httpx.HTTPStatusError as e:
            error_text = await e.response.text()
            raise Exception(f"Brave API error: {e.response.status_code} {e.response.reason_phrase}\n{error_text}") from e
        except Exception as e:
            raise Exception(f"Error during web search: {str(e)}") from e

        if not data.web or not data.web.results:
            return "No web results found."

        results_str = []
        for result in data.web.results:
            results_str.append(
                f"Title: {result.title}\nDescription: {result.description}\nURL: {result.url}"
            )

        return "\n\n".join(results_str) if results_str else "No web results found."

    async def _get_pois_data(self, ids: List[str]) -> BravePoiResponse:
        self._check_rate_limit()
        if not self.http_client: await self._init_client()
        if not self.http_client : raise Exception("HTTP client not initialized")

        url = f"{self.BRAVE_API_BASE_URL}/local/pois"
        # Brave API expects 'ids' param to be repeated: ids=id1&ids=id2
        params = [('ids', id_val) for id_val in ids if id_val]

        try:
            response = await self.http_client.get(url, params=params)
            response.raise_for_status()
            return BravePoiResponse.model_validate(response.json())
        except httpx.HTTPStatusError as e:
            error_text = await e.response.text()
            raise Exception(f"Brave POI API error: {e.response.status_code} {e.response.reason_phrase}\n{error_text}") from e
        except Exception as e:
            raise Exception(f"Error fetching POI data: {str(e)}") from e


    async def _get_descriptions_data(self, ids: List[str]) -> BraveDescriptionResponse:
        self._check_rate_limit()
        if not self.http_client: await self._init_client()
        if not self.http_client : raise Exception("HTTP client not initialized")


        url = f"{self.BRAVE_API_BASE_URL}/local/descriptions"
        params = [('ids', id_val) for id_val in ids if id_val]

        try:
            response = await self.http_client.get(url, params=params)
            response.raise_for_status()
            return BraveDescriptionResponse.model_validate(response.json())
        except httpx.HTTPStatusError as e:
            error_text = await e.response.text()
            raise Exception(f"Brave Descriptions API error: {e.response.status_code} {e.response.reason_phrase}\n{error_text}") from e
        except Exception as e:
            raise Exception(f"Error fetching descriptions data: {str(e)}") from e

    def _format_local_results(self, pois_data: BravePoiResponse, desc_data: BraveDescriptionResponse) -> str:
        if not pois_data.results:
            return "No local results found."

        formatted_results = []
        for poi in pois_data.results:
            address_parts = []
            if poi.address:
                if poi.address.streetAddress: address_parts.append(poi.address.streetAddress)
                if poi.address.addressLocality: address_parts.append(poi.address.addressLocality)
                if poi.address.addressRegion: address_parts.append(poi.address.addressRegion)
                if poi.address.postalCode: address_parts.append(poi.address.postalCode)

            address_str = ", ".join(address_parts) if address_parts else "N/A"
            phone_str = poi.phone or "N/A"
            rating_str = f"{poi.rating.ratingValue if poi.rating else 'N/A'} ({poi.rating.ratingCount if poi.rating else 0} reviews)"
            price_range_str = poi.priceRange or "N/A"
            hours_str = ", ".join(poi.openingHours) if poi.openingHours else "N/A"
            description_str = desc_data.descriptions.get(poi.id, "No description available.")

            formatted_results.append(
                f"Name: {poi.name}\n"
                f"Address: {address_str}\n"
                f"Phone: {phone_str}\n"
                f"Rating: {rating_str}\n"
                f"Price Range: {price_range_str}\n"
                f"Hours: {hours_str}\n"
                f"Description: {description_str}"
            )
        return "\n\n---\n\n".join(formatted_results) if formatted_results else "No local results found."


    async def _perform_local_search(self, query: str, count: int = 5) -> str:
        self._check_rate_limit() # Initial check for the first call
        if not self.http_client: await self._init_client()
        if not self.http_client : raise Exception("HTTP client not initialized")

        # 1. Initial search to get location IDs
        web_search_url = f"{self.BRAVE_API_BASE_URL}/web/search"
        web_search_params = {
            'q': query,
            'search_lang': 'en',
            'result_filter': 'locations',
            'count': str(min(count, 20)) # Use original count, capped at 20
        }
        try:
            response = await self.http_client.get(web_search_url, params=web_search_params)
            response.raise_for_status()
            web_data = BraveWebResponse.model_validate(response.json())
        except httpx.HTTPStatusError as e:
            error_text = await e.response.text()
            raise Exception(f"Brave API error (local search pre-fetch): {e.response.status_code} {e.response.reason_phrase}\n{error_text}") from e
        except Exception as e:
            raise Exception(f"Error during local search pre-fetch: {str(e)}") from e


        location_ids: List[str] = []
        if web_data.locations and web_data.locations.results:
            location_ids = [loc.id for loc in web_data.locations.results if loc.id]

        if not location_ids:
            print(f"INFO: No local results found for '{query}', falling back to web search.")
            # Fallback to web search, using the original 'count' for local search.
            return await self._perform_web_search(query, count=count)

        # 2. Get POI details and descriptions in parallel
        try:
            pois_data_task = self._get_pois_data(location_ids)
            descriptions_data_task = self._get_descriptions_data(location_ids)

            pois_response, descriptions_response = await asyncio.gather(
                pois_data_task, descriptions_data_task
            )
        except Exception as e:
            # If any of the parallel tasks fail, propagate the error
            raise Exception(f"Error fetching local details: {str(e)}") from e

        return self._format_local_results(pois_response, descriptions_response)


    async def start(self):
        await self._init_client()

        self.server = FastMCP(
            name=self.SERVER_NAME,
            port=self.SERVER_PORT
        )

        # --- Tool Definitions ---
        @self.server.tool()
        async def brave_web_search(query: str, count: int = 10, offset: int = 0) -> str:
            """
            Performs a web search using the Brave Search API, ideal for general queries, news, articles, and online content.
            Use this for broad information gathering, recent events, or when you need diverse web sources.
            Supports pagination, content filtering, and freshness controls.
            Maximum 20 results per request, with offset for pagination.

            Args:
                query (str): Search query (max 400 chars, 50 words).
                count (int): Number of results (1-20, default 10).
                offset (int): Pagination offset (default 0, Brave API docs suggest max of 9 for some contexts but API may support more).

            Returns:
                str: A string containing the formatted search results, or an error message.
            """
            if not isinstance(query, str) or not query.strip():
                raise ValueError("Query must be a non-empty string.")
            if not isinstance(count, int) or not (1 <= count <= 20):
                # FastMCP might do this validation based on type hints/Pydantic, but explicit check is safer.
                # The schema description says 1-20, default 10. min(count,20) is applied later.
                # Let's ensure count is reasonable before API call.
                raise ValueError("Count must be an integer between 1 and 20.")
            if not isinstance(offset, int) or offset < 0:
                raise ValueError("Offset must be a non-negative integer.")

            try:
                return await self._perform_web_search(query, count, offset)
            except Exception as e:
                print(f"ERROR in brave_web_search: {e}", file=sys.stderr)
                # Re-raise to let FastMCP handle it and format the error response
                raise

        @self.server.tool()
        async def brave_local_search(query: str, count: int = 5) -> str:
            """
            Searches for local businesses and places using Brave's Local Search API.
            Best for queries related to physical locations, businesses, restaurants, services, etc.
            Returns detailed information including:
            - Business names and addresses
            - Ratings and review counts
            - Phone numbers and opening hours
            Use this when the query implies 'near me' or mentions specific locations.
            Automatically falls back to web search if no local results are found.

            Args:
                query (str): Local search query (e.g. 'pizza near Central Park').
                count (int): Number of results (1-20, default 5 for local, but will be capped by API).

            Returns:
                str: A string containing the formatted local search results, or an error message.
            """
            if not isinstance(query, str) or not query.strip():
                raise ValueError("Query must be a non-empty string.")
            if not isinstance(count, int) or not (1 <= count <= 20):
                 raise ValueError("Count must be an integer between 1 and 20.")

            try:
                return await self._perform_local_search(query, count)
            except Exception as e:
                print(f"ERROR in brave_local_search: {e}", file=sys.stderr)
                raise

        # --- Uvicorn Server Setup ---
        if not self.server or not self.server.sse_app:
            print("Error: FastMCP server application not initialized correctly.", file=sys.stderr)
            sys.exit(1)

        uviconfig = uvicorn.Config(
            self.server.sse_app,
            host=self.SERVER_HOST,
            port=self.SERVER_PORT,
            log_level="info"
        )
        self.uvicorn_server = uvicorn.Server(uviconfig)

        # Create task for Uvicorn server, similar to the filesystem example
        self.serve_task = asyncio.create_task(self.uvicorn_server.serve())

        # Wait for Uvicorn to start (optional, but good for knowing it's up)
        # This loop is from the example, ensures start() doesn't return before server is confirmed running.
        # Note: uvicorn.Server.started is an internal flag and might change.
        # A more robust way might involve custom Uvicorn lifecycle events if needed.
        while not self.uvicorn_server.started:
            await asyncio.sleep(0.1)
        print(f"INFO: Uvicorn server started on http://{self.SERVER_HOST}:{self.SERVER_PORT}")

        return self.serve_task # Return the task for main loop to await if needed

    async def stop(self):
        print(f"INFO: Attempting to shut down {self.SERVER_NAME}...")
        if self.uvicorn_server:
            self.uvicorn_server.should_exit = True

        if self.serve_task and not self.serve_task.done():
            # Wait for the serve task to complete shutdown or cancel it
            try:
                # Give it a bit of time to shut down gracefully
                await asyncio.wait_for(self.serve_task, timeout=5.0)
            except asyncio.TimeoutError:
                print("INFO: Uvicorn serve task timed out during shutdown, cancelling.")
                self.serve_task.cancel()
                try:
                    await self.serve_task
                except asyncio.CancelledError:
                    print(f"INFO: {self.SERVER_NAME} serve task successfully cancelled.")
            except Exception as e: # Catch other potential exceptions during await
                print(f"ERROR: Exception during serve_task await: {e}", file=sys.stderr)

        await self._close_client()
        print(f"INFO: {self.SERVER_NAME} stop sequence completed.")

# --- Main Execution ---
async def main():
    server_instance = MCPServerBraveSearch()
    main_server_task = None
    try:
        main_server_task = await server_instance.start() # This now returns the uvicorn task
        if main_server_task:
            await main_server_task # Keep the main function alive by awaiting the server task
    except KeyboardInterrupt:
        print("INFO: Keyboard interrupt received, shutting down...")
    except Exception as e:
        print(f"FATAL: Server failed to start or run: {e}", file=sys.stderr)
    finally:
        await server_instance.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e: # Catch-all for errors during asyncio.run if main() itself fails badly
        print(f"CRITICAL: Unhandled error in main execution: {e}", file=sys.stderr)
