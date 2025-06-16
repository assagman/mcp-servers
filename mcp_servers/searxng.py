from typing import List, Optional, Dict, Union, Any, cast

from pydantic import HttpUrl, Field, AliasChoices
from pydantic import BaseModel
from mcp.server.fastmcp import FastMCP

from mcp_servers.base import MCPServerHttpBase, MCPServerHttpBaseSettings
from mcp_servers.exceptions import MCPUpstreamServiceError, MCPRateLimitError


class SearXNGResult(BaseModel):
    url: HttpUrl
    title: str
    content: Optional[str] = None
    thumbnail: Optional[str] = None
    engine: Optional[str] = None
    parsed_url: Optional[List[str]] = None
    img_src: Optional[str] = None
    score: Optional[float] = None
    category: Optional[str] = None

class SearXNGInfobox(BaseModel):
    infobox: Optional[str] = None
    id: Optional[str] = None
    content: Optional[str] = None
    links: Optional[List[Dict[str, str]]] = None
    img_src: Optional[str] = None

class SearXNGResponse(BaseModel):
    query: Optional[str] = None
    results: List[SearXNGResult] = Field(default_factory=list)
    infoboxes: List[SearXNGInfobox] = Field(default_factory=list)
    suggestions: Union[List[str], Dict[str, List[str]]] = Field(default_factory=list)
    answers: List[str] = Field(default_factory=list)
    corrections: List[str] = Field(default_factory=list)
    unresponsive_engines: List[List[Any]] = Field(default_factory=list)

class SearXNGServerSettings(MCPServerHttpBaseSettings):
    SERVER_NAME: str = "MCP_SERVER_SEARXNG"
    HOST: str = Field(default="0.0.0.0", validation_alias=AliasChoices("MCP_SERVER_SEARXNG_HOST"))
    PORT: int = Field(default=8767, validation_alias=AliasChoices("MCP_SERVER_SEARXNG_PORT"))

    BASE_URL: HttpUrl = Field(default=HttpUrl("http://0.0.0.0:8001"), validation_alias=AliasChoices("SEARXNG_BASE_URL"))
    # USERNAME: Optional[str] = Field(default=None, validation_alias=AliasChoices("SEARXNG_USERNAME", "MCP_SERVER_SEARXNG_USERNAME"))
    # PASSWORD: Optional[str] = Field(default=None, validation_alias=AliasChoices("SEARXNG_PASSWORD", "MCP_SERVER_SEARXNG_PASSWORD"))
    RATE_LIMIT_PER_SECOND: int | None = Field(default=20, validation_alias=AliasChoices("SEARXNG_RATE_LIMIT_PER_SECOND", "MCP_SERVER_SEARXNG_RATE_LIMIT_PER_SECOND"))

    model_config = MCPServerHttpBaseSettings.model_config


class MCPServerSearxng(MCPServerHttpBase):

    @property
    def settings(self):
        return cast(SearXNGServerSettings, self._settings)

    def _load_and_validate_settings(self) -> SearXNGServerSettings:
        """Load Searxng Search specific MCP server settings"""
        return SearXNGServerSettings()

    def _log_initial_config(self):
        super()._log_initial_config()

        self.logger.info("--- MCPServerSearxng Configuration ---")
        self.logger.info(f"  SERVER_NAME:       {self.settings.SERVER_NAME}")
        self.logger.info(f"  HOST:              {self.settings.HOST}")
        self.logger.info(f"  PORT:              {self.settings.PORT}")
        self.logger.info(f"  BASE_URL:          {self.settings.BASE_URL}")
        self.logger.info("--- End MCPServerSearxng Configuration ---")

    def _get_http_client_config(self) -> Dict[str, Any]:
        """Configures the HTTP client for SearXNG."""
        # auth = None
        # if self.settings.USERNAME and self.settings.PASSWORD:
        #     auth = httpx.BasicAuth(self.settings.USERNAME, self.settings.PASSWORD)

        return {
            "base_url": str(self.settings.BASE_URL), # Convert HttpUrl to string for httpx
            "headers": {"Accept": "application/json"},
            # "auth": auth,
        }

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
                if info.infobox:
                    box_parts.append(f"Type: {info.infobox}")
                if info.content:
                    box_parts.append(f"Content: {info.content}")
                if info.img_src:
                    box_parts.append(f"Image: {info.img_src}")
                if info.links:
                    link_strs = [f"{link_dict['text']}: {link_dict['href']}" for link_dict in info.links if link_dict.get("text") and link_dict.get("href")]
                    if link_strs:
                        box_parts.append("Links:\n  - " + "\n  - ".join(link_strs))
                if box_parts:
                    output_parts.append("\n".join(box_parts))
        if data.results:
            output_parts.append("\n--- Search Results ---")
            for i, result in enumerate(data.results, 1):
                res_parts = [f"Result {i}:", f"  Title: {result.title}", f"  URL: {result.url}"]
                if result.content:
                    res_parts.append(f"  Snippet: {result.content}")
                if result.engine:
                    res_parts.append(f"  Engine: {result.engine}")
                if result.category:
                    res_parts.append(f"  Category: {result.category}")
                if result.thumbnail:
                    res_parts.append(f"  Thumbnail: {result.thumbnail}")
                if result.score:
                    res_parts.append(f"  Score: {result.score}")
                output_parts.append("\n".join(res_parts))
        if data.suggestions:
            output_parts.append("\n--- Suggestions ---")
            if isinstance(data.suggestions, dict):
                for engine, sug_list in data.suggestions.items():
                    if sug_list:
                        output_parts.append(f"  From {engine}: {', '.join(sug_list)}")
            elif isinstance(data.suggestions, list) and data.suggestions:
                output_parts.append(f"  General: {', '.join(data.suggestions)}")

        if not output_parts and not data.results and not data.infoboxes and not data.answers:
             return "No results, infoboxes, answers, or suggestions found."

        return "\n\n".join(output_parts).strip() if output_parts else "No information found."

    async def _perform_search(
        self,
        query: str,
        pageno: int = 1,
        categories: Optional[str] = None,
        language: str = "en",
    ) -> str:
        search_endpoint = "search" # Relative to base_url
        params = {"q": query, "format": "json", "pageno": str(pageno)}
        if categories:
            params["categories"] = categories
        if language:
            params["language"] = language

        json_data = await self._make_get_request_with_retry(search_endpoint, params)
        data = SearXNGResponse.model_validate(json_data)
        return self._format_searxng_results(data)


    async def _register_tools(self, mcp_server: FastMCP) -> None:
        """Registers the searxng tool."""

        @mcp_server.tool()
        async def searxng(
            query: str,
            pageno: int = 1,
            categories: Optional[str] = None,
            language: str = "en",
        ) -> str:
            """
            Performs a search using a self-hosted SearXNG instance.
            Args:
                query (str): The search query.
                pageno (int): Page number for results (default: 1).
                categories (Optional[str]): Comma-separated SearXNG categories (e.g., "general,news").
                language (str): Language code for search (e.g., "en", default: "en").
            Returns:
                str: Formatted search results or an error message.
            """
            # Basic input validation
            if not isinstance(query, str) or not query.strip():
                raise ValueError("Query must be a non-empty string.")
            if not isinstance(pageno, int) or pageno < 1:
                raise ValueError("Page number (pageno) must be a positive integer.")
            if categories is not None and not isinstance(categories, str):
                raise ValueError("Categories must be a comma-separated string if provided.")
            if not isinstance(language, str) or not language: # Basic check
                raise ValueError("Language must be a non-empty string.")

            try:
                # The `builtins.str` issue was very specific to a debug print where `str` was shadowed.
                # For general code, `str()` is fine. If `str` is a parameter name (bad practice), then use `builtins.str()`.
                # Here, `str` is not shadowed.
                return await self._perform_search(query, pageno, categories, language)
            except MCPUpstreamServiceError as e:
                self.logger.error(f"Upstream service error in searxng for query '{query}': {e}")
                return f"Error: Search failed due to an issue with the SearXNG service. Status: {e.status_code or 'N/A'}. Details: {e}"
            except MCPRateLimitError as e:
                self.logger.warning(f"Rate limit hit in searxng for query '{query}': {e}")
                return f"Error: Client-side rate limit hit. Please try again shortly. {e}"
            except ValueError as e: # From input validation
                 self.logger.warning(f"Validation error in searxng for query '{query}': {e}")
                 return f"Error: Invalid input provided. {e}"
            except Exception as e:
                self.logger.error(f"Unexpected error in searxng tool for query '{query}': {e}")
                return f"Error: An unexpected error occurred during search. Please check server logs. Type: {type(e).__name__}"
