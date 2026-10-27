"""Records for hosting an MCP server (``agents.mcp.server``)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ("CORSOptions", "McpAuthContext")


@dataclass(frozen=True, slots=True, kw_only=True)
class CORSOptions:
    """The CORS headers `create_mcp_handler` adds to every response.

    The defaults are upstream's (``handler-stateless.ts``).

    Parameters
    ----------
    origin
        ``Access-Control-Allow-Origin``. A concrete ``http(s)`` origin is
        also added to the default ``Origin`` allowlist.
    headers
        ``Access-Control-Allow-Headers``.
    methods
        ``Access-Control-Allow-Methods``.
    expose_headers
        ``Access-Control-Expose-Headers``.
    max_age
        ``Access-Control-Max-Age``, in seconds.
    """

    origin: str = "*"
    headers: str = (
        "Content-Type, Accept, Authorization, mcp-session-id, "
        "MCP-Protocol-Version, Mcp-Method, Mcp-Name"
    )
    methods: str = "GET, POST, DELETE, OPTIONS"
    expose_headers: str = "mcp-session-id"
    max_age: int = 86400


@dataclass(frozen=True, slots=True, kw_only=True)
class McpAuthContext:
    """What auth in front of the handler tells the server's tools.

    Tools read it with `get_mcp_auth_context`.

    Parameters
    ----------
    props
        Application data, such as the signed-in user.
    """

    props: Mapping[str, Any]
