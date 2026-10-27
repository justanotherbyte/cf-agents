"""Exceptions raised by the MCP client and server handler.

Every one subclasses `MCPError`, so ``except MCPError`` catches them all.
``MCPNotEnabledError`` lives in ``agents.agent.errors`` instead, because
``Agent`` raises it without importing this package.
"""

from typing import Any

from ..core.errors import AgentsException

__all__ = (
    "MCPAuthorizationRequiredError",
    "MCPBlockedUrlError",
    "MCPConnectionError",
    "MCPError",
    "MCPOAuthError",
    "MCPProtocolError",
    "MCPRpcError",
    "MCPServerIdConflictError",
    "MCPServerNotFoundError",
    "MCPServerNotReadyError",
    "MCPTransportError",
)


class MCPError(AgentsException):
    """Base class for every MCP exception."""


class MCPTransportError(MCPError):
    """The HTTP request to a server failed: network, DNS, TLS, or a dropped body."""


class MCPRpcError(MCPError):
    """A server answered a request with a JSON-RPC error.

    Parameters
    ----------
    code
        The JSON-RPC error code (-32601 is "method not found").
    message
        The server's message.
    data
        The error's ``data`` member, if any.
    """

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"MCP error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class MCPProtocolError(MCPError):
    """A server broke the protocol, or needs a feature the client lacks."""


class MCPAuthorizationRequiredError(MCPError):
    """The server needs OAuth; the user must visit ``auth_url`` first.

    Parameters
    ----------
    auth_url
        The authorization URL to send the user to.
    """

    def __init__(self, auth_url: str) -> None:
        super().__init__(f"Authorization required: {auth_url}")
        self.auth_url = auth_url


class MCPOAuthError(MCPError):
    """An OAuth step failed: discovery, registration, the code exchange or a refresh."""


class MCPConnectionError(MCPError):
    """A server couldn't be connected; it stays stored and can be retried.

    Parameters
    ----------
    server_id
        The server's id.
    message
        Why the connection failed.
    """

    def __init__(self, server_id: str, message: str) -> None:
        super().__init__(f"Failed to connect to MCP server {server_id!r}: {message}")
        self.server_id = server_id


class MCPServerIdConflictError(MCPError):
    """``add_server`` was given an id that belongs to a different name or URL."""

    def __init__(self, server_id: str) -> None:
        super().__init__(
            f"MCP server id {server_id!r} is already used by a different server"
        )
        self.server_id = server_id


class MCPBlockedUrlError(MCPError):
    """A server URL points at a private or metadata address."""

    def __init__(self, url: str) -> None:
        super().__init__(f"Blocked MCP server URL (private address): {url}")
        self.url = url


class MCPServerNotFoundError(MCPError):
    """No server with this id is registered."""

    def __init__(self, server_id: str) -> None:
        super().__init__(f"No MCP server with id {server_id!r}")
        self.server_id = server_id


class MCPServerNotReadyError(MCPError):
    """A call needs a server that isn't ready yet."""

    def __init__(self, server_id: str, state: str) -> None:
        super().__init__(f"MCP server {server_id!r} isn't ready (state: {state})")
        self.server_id = server_id
        self.state = state
