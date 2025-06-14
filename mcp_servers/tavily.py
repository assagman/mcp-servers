import os
from typing import List, Optional, Dict, Any, Union, cast

from pydantic import BaseModel, Field, HttpUrl, field_validator, AliasChoices

from mcp.server.fastmcp import FastMCP

from mcp_servers.base import MCPServerHttpBase, MCPServerHttpBaseSettings
from mcp_servers.logger import MCPServersLogger


class TavilyResultItem(BaseModel):
    title: str
    url: HttpUrl
    content: str
    score: float
    raw_content: Optional[str] = None
    images: Optional[List[str]] = None


class TavilyApiResponse(BaseModel):
    query: str
    answer: Optional[str] = None
    results: List[TavilyResultItem] = Field(default_factory=list)
    response_time: Optional[float] = None
    images: Optional[List[str]] = None


class TavilyExtractResultItem(
    BaseModel
):
    """Represents a single successfully extracted URL's content."""

    url: HttpUrl
    content: str
    images: Optional[List[str]] = None

    @field_validator("url", mode="before")
    @classmethod
    def ensure_url_is_string(cls, value):
        if isinstance(value, HttpUrl):
            return str(value)
        return value


class TavilyExtractFailedItem(BaseModel):
    """Represents a single URL that failed extraction."""

    url: str
    error: str


class TavilyExtractApiResponse(BaseModel):
    """
    Models the response from Tavily's /extract endpoint.
    The API returns a list of result objects if `urls` is a list,
    or a single result object if `urls` was a single string.
    We will always send a list of URLs, so we expect `results` to be a list.
    """

    results: List[
        Union[TavilyExtractResultItem, TavilyExtractFailedItem]
    ]
    response_time: Optional[float] = None

    @field_validator("results", mode="before")
    @classmethod
    def parse_extract_results(cls, v: Any) -> List[Dict[str, Any]]:
        """
        The Tavily /extract endpoint can return:
        1. A single object if a single URL string was passed.
        2. A list of objects if a list of URLs was passed.
        Each object in the list can be a success or a failure.
        This validator ensures we always process a list of dictionaries.
        """
        if not isinstance(v, list):
            if isinstance(v, dict):
                v = [v]
            else:
                raise ValueError(
                    "Tavily /extract results must be a list or a single dictionary."
                )

        parsed_results = []
        for item in v:
            if not isinstance(item, dict):
                MCPServersLogger.get_logger(__name__).warning(
                    f"Unexpected item type in Tavily /extract results: {type(item)}. Skipping."
                )
                continue

            # Tavily API returns 'raw_content' for the main content in /extract
            # We map it to 'content' in our TavilyExtractResultItem model for consistency
            if "raw_content" in item and "content" not in item:
                item["content"] = item.pop("raw_content")

            if "error" in item:
                parsed_results.append(item)
            else:
                parsed_results.append(item)
        return parsed_results


class TavilyCrawlResultItem(BaseModel):
    url: str
    raw_content: str
    # Potentially other fields if the API adds them, like 'title', 'metadata'


class TavilyCrawlApiResponse(BaseModel):
    base_url: str
    results: List[TavilyCrawlResultItem] = Field(default_factory=list)
    response_time: Optional[float] = None
    # Potentially 'failed_results' or error indicators for specific crawled URLs if API supports it


class TavilyServerSettings(MCPServerHttpBaseSettings):
    SERVER_NAME: str = "MCP_SERVER_TAVILY"
    HOST: str = Field(default="0.0.0.0", validation_alias=AliasChoices("MCP_SERVER_TAVILY_HOST"))
    PORT: int = Field(default=8768, validation_alias=AliasChoices("MCP_SERVER_TAVILY_PORT"))
    RATE_LIMIT_PER_SECOND: int = Field(default=20, validation_alias=AliasChoices("TAVILY_RATE_LIMIT_PER_SECOND", "MCP_SERVER_TAVILY_RATE_LIMIT_PER_SECOND"))
    BASE_URL: HttpUrl = Field(default=HttpUrl("https://api.tavily.com"), validation_alias=AliasChoices("TAVILY_API_BASE_URL"))
    TAVILY_API_KEY: str = Field(default_factory=lambda: os.environ["TAVILY_API_KEY"], validation_alias=AliasChoices("TAVILY_API_KEY"))

    model_config = MCPServerHttpBaseSettings.model_config


class MCPServerTavily(MCPServerHttpBase):
    TAVILY_ENDPOINT = "/search"
    TAVILY_EXTRACT_ENDPOINT = "/extract"
    TAVILY_CRAWL_ENDPOINT = "/crawl"
    SEARCH_DEPTH_BASIC = "basic"
    SEARCH_DEPTH_ADVANCED = "advanced"

    """
    MCP Server for interacting with the Tavily AI Search API.
    Provides tools for web search and content extraction.
    """
    @property
    def settings(self):
        return cast(TavilyServerSettings, self._settings)

    def _load_and_validate_settings(self) -> TavilyServerSettings:
        """Load Brave Search specific MCP server settings"""
        return TavilyServerSettings()

    def _log_initial_config(self):
        super()._log_initial_config()

        self.logger.info("--- MCPServerTavily Configuration ---")
        self.logger.info(f"  SERVER_NAME:       {self.settings.SERVER_NAME}")
        self.logger.info(f"  HOST:              {self.settings.HOST}")
        self.logger.info(f"  PORT:              {self.settings.PORT}")
        self.logger.info(f"  BASE_URL:          {self.settings.BASE_URL}")
        self.logger.info("--- End MCPServerTavily Configuration ---")

    def _get_http_client_config(self) -> Dict[str, Any]:
        """Configures the HTTP client for Brave Search API."""
        settings: TavilyServerSettings = self.settings # type: ignore

        return {
            "base_url": str(settings.BASE_URL),
            "headers": {
                "Authorization": f"Bearer {settings.TAVILY_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        }

    def _format_search_results(self, response: TavilyApiResponse) -> str:
        """Formats Tavily search API response into a human-readable string."""
        output_parts = []
        if response.answer:
            output_parts.append(f"Tavily Answer: {response.answer}")

        if not response.results and not response.answer:
            return "No search results or answer found from Tavily."

        if response.results:
            output_parts.append("\nSearch Results:")
            for i, result in enumerate(response.results):
                output_parts.append(f"\n--- Result {i + 1} ---")
                output_parts.append(f"  Title: {result.title}")
                output_parts.append(f"  URL: {result.url}")
                output_parts.append(f"  Content Snippet: {result.content}")
                output_parts.append(f"  Relevance Score: {result.score}")
                if result.raw_content:
                    output_parts.append(
                        f"  Raw Content Snippet (first 200 chars): {result.raw_content[:200]}..."
                    )
                if result.images:
                    output_parts.append(f"  Images: {', '.join(result.images)}")
        return "\n".join(output_parts)

    def _format_extract_results(self, response: TavilyExtractApiResponse) -> str:
        """Formats Tavily extract API response into a human-readable string."""
        output_parts = []

        if not response.results:
            empty_results_err_msg = "No content extracted by Tavily."
            self.logger.warning(empty_results_err_msg)
            return empty_results_err_msg

        try:
            output_parts.append("Tavily Extraction Results:")
            for i, item in enumerate(response.results):
                output_parts.append(f"\n--- Item {i + 1} ---")
                if isinstance(item, TavilyExtractResultItem):
                    output_parts.append(f"  URL: {item.url}")
                    output_parts.append(
                        f"  Extracted Content: {item.content}"
                    )
                    if item.images:
                        output_parts.append(f"  Images: {', '.join(item.images)}")
                elif isinstance(item, TavilyExtractFailedItem):
                    output_parts.append(f"  URL: {item.url}")
                    output_parts.append(f"  Error: {item.error}")
                else:  # Should not happen with Pydantic validation
                    output_parts.append("  URL: Unknown (parsing issue)")
                    output_parts.append(f"  Content: Unexpected item type: {type(item)}")
        except Exception as exp:
            extract_result_parse_err_msg = f"Error while parsing extraction results: {exp}"
            self.logger.info(extract_result_parse_err_msg)
            return extract_result_parse_err_msg


        return "\n".join(output_parts)

    def _format_crawl_results(self, response: TavilyCrawlApiResponse) -> str:
        """Formats Tavily crawl API response into a human-readable string."""
        if not response.results:
            return f"No content crawled by Tavily for base URL: {response.base_url}."

        output_parts = [f"Tavily Crawl Results for Base URL: {response.base_url}:"]
        for i, item in enumerate(response.results):
            output_parts.append(f"\n--- Crawled Page {i + 1} ---")
            output_parts.append(f"  URL: {item.url}")
            output_parts.append(
                f"  Raw Content (first 300 chars): {item.raw_content[:300]}..."
            )
        if response.response_time:
            output_parts.append(f"\nResponse Time: {response.response_time:.2f}s")
        return "\n".join(output_parts)

    async def _register_tools(self, mcp_server: FastMCP):
        @mcp_server.tool()
        async def tavily(
            query: str,
            search_depth: str = self.SEARCH_DEPTH_BASIC,
            include_answer: bool = False,
            include_raw_content: bool = False,
            max_results: int = 5,
            include_domains: Optional[List[str]] = None,
            exclude_domains: Optional[List[str]] = None,
            include_images: bool = False,
            days_published_ago: Optional[int] = None,
        ) -> str:
            """
            Performs a search using the Tavily AI API.
            Ideal for comprehensive research, finding factual information, and getting direct answers.

            Args:
                query (str): The search query.
                search_depth (str): "basic" for quick results, "advanced" for thorough research. Defaults to "basic".
                include_answer (bool): If True, attempts to provide a direct answer. Defaults to False.
                include_raw_content (bool): If True, includes snippets of raw web content. Defaults to False.
                max_results (int): Max number of results (1-20, API default is 5). Defaults to 5.
                include_domains (Optional[List[str]]): Domains to focus on (e.g., ["wikipedia.org", "bbc.com"]).
                exclude_domains (Optional[List[str]]): Domains to exclude.
                include_images (bool): If True, includes image URLs in results. Defaults to False.
                days_published_ago (Optional[int]): Filters results published within the last N days (e.g., 7 for last week).

            Returns:
                str: Formatted search results or an error message.
            """
            if not isinstance(query, str) or not query.strip():
                return "Error: Query must be a non-empty string."
            if search_depth not in [
                self.SEARCH_DEPTH_BASIC,
                self.SEARCH_DEPTH_ADVANCED,
            ]:
                return f"Error: search_depth must be '{self.SEARCH_DEPTH_BASIC}' or '{self.SEARCH_DEPTH_ADVANCED}'."
            if not (1 <= max_results <= 20):
                return "Error: max_results must be between 1 and 20."
            if days_published_ago is not None and not (
                isinstance(days_published_ago, int) and days_published_ago > 0
            ):
                return "Error: days_published_ago must be a positive integer."

            payload: Dict[str, Any] = {
                "query": query,
                "search_depth": search_depth,
                "include_answer": include_answer,
                "include_raw_content": include_raw_content,
                "max_results": max_results,
                "include_domains": include_domains or [],
                "exclude_domains": exclude_domains or [],
                "include_images": include_images,
            }
            if days_published_ago is not None:
                payload["days_published_ago"] = days_published_ago

            response_dict = await self._make_post_request_with_retry(
                self.TAVILY_ENDPOINT, payload
            )
            validated_response = TavilyApiResponse.model_validate(
                response_dict
            )
            return self._format_search_results(validated_response)

        @mcp_server.tool()
        async def tavily_extract_content(
            url_to_extract: str,
            extract_depth: str = self.SEARCH_DEPTH_BASIC,
            include_images_in_extract: bool = False,
        ) -> str:
            """
            Fetches and returns the main textual content from a given URL using Tavily's /extract endpoint.

            Args:
                url_to_extract (str): The URL of the webpage to fetch content from. Must be a valid HTTP/HTTPS URL.
                extract_depth (str): "basic" for core content, "advanced" for more comprehensive extraction. Defaults to "basic".
                include_images_in_extract (bool): Whether to include image URLs from the extracted content. Defaults to False.

            Returns:
                str: The extracted textual content of the webpage, or an error message.
            """
            try:
                validated_url = HttpUrl(url_to_extract)
                self.logger.info(f"Extracting URL: {validated_url}")
            except ValueError:
                err_msg = f"Error: Invalid URL format provided: {url_to_extract}"f"Error: Invalid URL format provided: {url_to_extract}"
                self.logger.warning(err_msg)
                return err_msg

            if extract_depth not in [
                self.SEARCH_DEPTH_BASIC,
                self.SEARCH_DEPTH_ADVANCED,
            ]:
                extract_depth_err_msg = f"Error: extract_depth must be '{self.SEARCH_DEPTH_BASIC}' or '{self.SEARCH_DEPTH_ADVANCED}'."
                self.logger.warning(extract_depth_err_msg)
                return extract_depth_err_msg

            payload = {
                "urls": [
                    str(validated_url)
                ],
                "extract_depth": extract_depth,
                "include_images": include_images_in_extract,
            }
            response_dict = await self._make_post_request_with_retry(
                self.TAVILY_EXTRACT_ENDPOINT, payload
            )

            try:
                validated_response = TavilyExtractApiResponse.model_validate(
                    response_dict
                )
            except Exception as exp:
                self.logger.warning(f"TavilyExtractApiResponse.model_validate failed: {exp}")
                return str(exp)

            return self._format_extract_results(validated_response)

        @mcp_server.tool()
        async def tavily_crawl_url(
            url_to_crawl: str,
            max_depth: int = 1,
            max_breadth: int = 20,
            limit: int = 50,
            instructions: Optional[str] = None,
            select_paths: Optional[List[str]] = None,
            select_domains: Optional[List[str]] = None,
            exclude_paths: Optional[List[str]] = None,
            exclude_domains: Optional[List[str]] = None,
            allow_external: bool = False,
            include_images_in_crawl: bool = False,
            categories: Optional[List[str]] = None,
            extract_depth_for_crawl: str = self.SEARCH_DEPTH_BASIC,
        ) -> str:
            """
            Crawls a given base URL up to a specified depth and breadth, extracting content.

            Args:
                url_to_crawl (str): The base URL to start crawling from (e.g., "docs.tavily.com").
                max_depth (int): Max depth of crawling from the base URL (0 for base URL only, 1 for links on base URL, etc.). Defaults to 1.
                max_breadth (int): Max number of links to follow from each page. Defaults to 20.
                limit (int): Max total number of pages to crawl. Defaults to 50.
                instructions (Optional[str]): Specific instructions for the crawler.
                select_paths (Optional[List[str]]): List of URL paths to prioritize (e.g., ["/blog/", "/docs/"]).
                select_domains (Optional[List[str]]): List of domains to prioritize if allow_external is True.
                exclude_paths (Optional[List[str]]): List of URL paths to exclude.
                exclude_domains (Optional[List[str]]): List of domains to exclude.
                allow_external (bool): Whether to allow crawling external domains linked from the base URL. Defaults to False.
                include_images_in_crawl (bool): Whether to include image URLs in the crawled content. Defaults to False.
                categories (Optional[List[str]]): Specific content categories to focus on.
                extract_depth_for_crawl (str): Extraction depth ("basic" or "advanced") for each crawled page. Defaults to "basic".

            Returns:
                str: Formatted crawled content or an error message.
            """
            try:
                if not isinstance(url_to_crawl, str) or not url_to_crawl.strip():
                    return "Error: url_to_crawl must be a non-empty string."
            except ValueError:
                return (
                    f"Error: Invalid URL format provided for crawling: {url_to_crawl}"
                )

            if not (isinstance(max_depth, int) and max_depth >= 0):
                return "Error: max_depth must be a non-negative integer."
            if not (isinstance(max_breadth, int) and max_breadth > 0):
                return "Error: max_breadth must be a positive integer."
            if not (isinstance(limit, int) and limit > 0):
                return "Error: limit must be a positive integer."
            if extract_depth_for_crawl not in [
                self.SEARCH_DEPTH_BASIC,
                self.SEARCH_DEPTH_ADVANCED,
            ]:
                return f"Error: extract_depth_for_crawl must be '{self.SEARCH_DEPTH_BASIC}' or '{self.SEARCH_DEPTH_ADVANCED}'."

            payload: Dict[str, Any] = {
                "url": url_to_crawl,
                "max_depth": max_depth,
                "max_breadth": max_breadth,
                "limit": limit,
                "allow_external": allow_external,
                "include_images": include_images_in_crawl,
                "extract_depth": extract_depth_for_crawl,
            }

            if instructions:
                payload["instructions"] = instructions
            if select_paths:
                payload["select_paths"] = select_paths
            if select_domains:
                payload["select_domains"] = select_domains
            if exclude_paths:
                payload["exclude_paths"] = exclude_paths
            if exclude_domains:
                payload["exclude_domains"] = exclude_domains
            if categories:
                payload["categories"] = categories

            response_dict = await self._make_post_request_with_retry(
                self.TAVILY_CRAWL_ENDPOINT, payload
            )
            validated_response = TavilyCrawlApiResponse.model_validate(
                response_dict
            )
            return self._format_crawl_results(validated_response)
