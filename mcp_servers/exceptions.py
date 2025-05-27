# mcp_servers/exceptions.py
class MCPServerError(Exception):
    """Base exception for MCP server errors."""
    pass

class MCPRateLimitError(MCPServerError):
    """Exception raised when a rate limit is exceeded."""
    pass

class MCPToolConfigurationError(MCPServerError):
    """Exception raised for configuration issues related to a tool."""
    pass

class MCPUpstreamServiceError(MCPServerError):
    """Exception raised for errors from an upstream API/service."""
    def __init__(self, message, status_code=None, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details

    def __str__(self):
        base = super().__str__()
        if self.status_code:
            base += f" (Status: {self.status_code})"
        if self.details:
            base += f" Details: {str(self.details)[:200]}..." # Truncate long details
        return base
