"""Hosting an MCP server on a Worker (upstream ``mcp/server``).

Needs the MCP SDK: ``cf-agents[mcp-server]``, on Python 3.14 Workers.
``agents.mcp`` never imports this package.
"""

try:
    import mcp  # noqa: F401
except ModuleNotFoundError as error:
    raise ImportError(
        "agents.mcp.server needs the MCP SDK: install cf-agents[mcp-server] "
        "(it runs on Python 3.14 Workers)"
    ) from error

from .auth_context import get_mcp_auth_context
from .errors import MCPServerConfigError
from .handler import McpHandler, create_mcp_handler
from .types import CORSOptions, McpAuthContext

__all__ = (
    "CORSOptions",
    "MCPServerConfigError",
    "McpAuthContext",
    "McpHandler",
    "create_mcp_handler",
    "get_mcp_auth_context",
)
