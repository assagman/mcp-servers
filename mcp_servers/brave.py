import os
from typing import List, Optional, Dict, Any, cast

from pydantic import BaseModel, HttpUrl, Field, AliasChoices

from mcp_servers.base import MCPServerHttpBase, MCPServerHttpBaseSettings


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


class BraveServerSettings(MCPServerHttpBaseSettings):
    SERVER_NAME: str = "MCP_SERVER_BRAVE"
    HOST: str = Field(
        default="127.0.0.1", validation_alias=AliasChoices("MCP_SERVER_BRAVE_HOST")
    )
    PORT: int = Field(
        default=8766, validation_alias=AliasChoices("MCP_SERVER_BRAVE_PORT")
    )
    RATE_LIMIT_PER_SECOND: int | None = Field(
        default=20,
        validation_alias=AliasChoices(
            "BRAVE_RATE_LIMIT_PER_SECOND", "MCP_SERVER_BRAVE_RATE_LIMIT_PER_SECOND"
        ),
    )
    BASE_URL: HttpUrl = Field(
        default=HttpUrl("https://api.search.brave.com/res/v1"),
        validation_alias=AliasChoices("BRAVE_API_BASE_URL"),
    )
    BRAVE_API_KEY: str = Field(
        default_factory=lambda: os.environ["BRAVE_API_KEY"],
        validation_alias=AliasChoices("BRAVE_API_KEY"),
    )

    model_config = MCPServerHttpBaseSettings.model_config


class MCPServerBrave(MCPServerHttpBase):

    @property
    def settings(self):
        return cast(BraveServerSettings, self._settings)

    def _load_and_validate_settings(
        self, host: Optional[str] = None, port: Optional[int] = None, **_
    ) -> BraveServerSettings:
        """Load Brave Search specific MCP server settings"""
        settings = BraveServerSettings()
        if host:
            settings.HOST = host
        if port:
            settings.PORT = port
        return settings

    def _get_http_client_config(self) -> Dict[str, Any]:
        """Configures the HTTP client for Brave Search API."""
        settings: BraveServerSettings = self.settings  # type: ignore

        return {
            "base_url": str(settings.BASE_URL),
            "headers": {
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": settings.BRAVE_API_KEY,
            },
        }

    async def _perform_web_search(
        self,
        query: str,
        count: int = 10,
        offset: int = 0,
        language: str = "en",
        freshness: str = "",
    ) -> str:
        """

        freshness: Filters search results by when they were discovered.

            The following values are supported:

            pd: Discovered within the last 24 hours.
            pw: Discovered within the last 7 Days.
            pm: Discovered within the last 31 Days.
            py: Discovered within the last 365 Days.
            YYYY-MM-DDtoYYYY-MM-DD: timeframe is also supported by specifying the date range e.g. 2022-04-01to2022-07-30.
        """
        search_endpoint = "web/search"
        params = {
            "q": query,
            "count": str(min(count, 20)),
            "offset": str(offset),
            "search_lang": language,
            "safesearch": "strict",
            "freshness": freshness,
        }
        json_data = await self._make_get_request_with_retry(search_endpoint, params)
        data = BraveWebResponse.model_validate(json_data)
        return self._format_web_results(data)

    def _format_web_results(self, data: BraveWebResponse):
        if not data.web or not data.web.results:
            return "No web results found."

        results_str = []
        for result in data.web.results:
            results_str.append(
                f"Title: {result.title}\nDescription: {result.description}\nURL: {result.url}"
            )

        return "\n\n".join(results_str) if results_str else "No web results found."

    async def _search_web_via_brave(
        self,
        query: str,
        count: int = 10,
        offset: int = 0,
        search_lang: str = "en",
        freshness: str = "",
    ) -> str:
        """
        Perform a web search using the Brave Search API, ideal for general queries, news, articles, and online content.
        Use this for broad information gathering, recent events, or when you need diverse web sources.
        Supports pagination, content filtering, and freshness controls.
        Maximum 20 results per request, with offset for pagination.

        Args:
            query (str): Search query (max 400 chars, 50 words).
            count (int): Number of results (1-20, default 10).
            offset (int): Pagination offset (default 0, Brave API docs suggest max of 9 for some contexts but API may support more).
            search_lang (str): Search language
            freshness: Filters search results by when they were discovered.
                The following values are supported:
                    pd: Discovered within the last 24 hours.
                    pw: Discovered within the last 7 Days.
                    pm: Discovered within the last 31 Days.
                    py: Discovered within the last 365 Days.
                    YYYY-MM-DDtoYYYY-MM-DD: timeframe is also supported by specifying the date range e.g. 2022-04-01to2022-07-30.

        Returns:
            str: A string containing the formatted search results, or an error message.
        """
        self.logger.debug(f"Brave web search tool called with query: {query}")
        if not isinstance(query, str) or not query.strip():
            self.logger.warning("Brave search: Query must be a non-empty string.")
            raise ValueError("Query must be a non-empty string.")
        if not isinstance(count, int) or not (1 <= count <= 20):
            self.logger.warning(
                "Brave search: Count must be an integer between 1 and 20."
            )
            raise ValueError("Count must be an integer between 1 and 20.")
        if not isinstance(offset, int) or offset < 0:
            self.logger.warning("Brave search: Offset must be a non-negative integer.")
            raise ValueError("Offset must be a non-negative integer.")

        try:
            result = await self._perform_web_search(
                query, count, offset, search_lang, freshness
            )
            self.logger.info(
                f"Brave web search tool returned result for query: {query}"
            )
            return result
        except Exception as e:
            self.logger.error(
                f"ERROR in brave_web_search for query '{query}': {e}", exc_info=True
            )
            raise

    async def _register_tools(self):
        """Register the brave tool."""
        self._register_mcp_server_tool(
            self._search_web_via_brave,
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=True,
        )
