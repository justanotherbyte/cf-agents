"""Exceptions raised when hosting an MCP server."""

from ..errors import MCPError

__all__ = ("MCPServerConfigError",)


class MCPServerConfigError(MCPError):
    """A server was built with settings the Workers handler can't serve.

    Raised for an ``MCPServer`` built with the SDK's OAuth settings
    (``auth``, ``auth_server_provider`` or ``token_verifier``): those install
    routes the handler doesn't serve. Put auth in front of the handler and
    pass ``auth_context`` instead.
    """
