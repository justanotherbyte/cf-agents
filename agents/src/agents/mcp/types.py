"""MCP client records, enums and the JSON shapes the client stores.

The OAuth shapes are the RFC JSON documents themselves (``TypedDict``s),
because they're stored verbatim and a row written by a TypeScript agent must
survive a Python rewrite.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, NotRequired, TypedDict

from ..core.types import RetryOptions

__all__ = (
    "AddMcpServerResult",
    "MCPConnectionState",
    "MCPOAuthResult",
    "MCPServerInfo",
    "MCPServerRecord",
    "MCPServersState",
    "OAuthClientInformation",
    "OAuthClientMetadata",
    "OAuthTokens",
    "SessionSnapshot",
    "StateCheck",
    "TransportType",
)


class MCPConnectionState(StrEnum):
    """Where a server connection is (upstream ``MCPConnectionState``)."""

    AUTHENTICATING = "authenticating"
    """Waiting for the user to finish OAuth."""
    CONNECTING = "connecting"
    CONNECTED = "connected"
    """Connected; the catalog isn't loaded (yet, or discovery failed)."""
    DISCOVERING = "discovering"
    READY = "ready"
    FAILED = "failed"


type TransportType = Literal["auto", "streamable-http"]
"""``auto`` is Streamable HTTP; the legacy SSE transport isn't supported yet."""


@dataclass(slots=True, kw_only=True)
class AddMcpServerResult:
    """What ``add_server`` / ``add_mcp_server`` returns.

    Parameters
    ----------
    id
        The server's id.
    state
        ``ready``, or ``authenticating`` when the user must visit
        ``auth_url`` first.
    auth_url
        The authorization URL, while authenticating.
    """

    id: str
    state: Literal["ready", "authenticating"]
    auth_url: str | None = None


@dataclass(slots=True, kw_only=True)
class SessionSnapshot:
    """What restoring a negotiated session needs without a new handshake.

    Parameters
    ----------
    protocol_version
        The negotiated protocol version.
    session_id
        The ``Mcp-Session-Id`` the server assigned, if it did.
    discover_result
        The ``server/discover`` result (2026-07-28 servers only).
    """

    protocol_version: str
    session_id: str | None = None
    discover_result: dict[str, Any] | None = None


@dataclass(slots=True, kw_only=True)
class MCPServerRecord:
    """One stored server (a ``cf_agents_mcp_servers`` row).

    Parameters
    ----------
    id, name, server_url
        The server's identity.
    callback_url
        The OAuth callback URL (``""`` for none, as upstream stores it).
    client_id
        The OAuth client id, once registered.
    auth_url
        The pending authorization URL, while authenticating.
    options
        The decoded ``server_options`` JSON (upstream's camelCase shape),
        including keys this client doesn't read.
    """

    id: str
    name: str
    server_url: str
    callback_url: str = ""
    client_id: str | None = None
    auth_url: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def _transport(self) -> dict[str, Any]:
        return self.options.get("transport") or {}

    @property
    def transport_type(self) -> str:
        """``str``: the stored transport type (``auto`` when unset)."""
        return self._transport.get("type") or "auto"

    @property
    def headers(self) -> dict[str, str]:
        """``dict[str, str]``: headers sent on every request."""
        return dict(self._transport.get("headers") or {})

    @property
    def retry(self) -> RetryOptions | None:
        """``RetryOptions | None``: the stored connection retries."""
        stored = self.options.get("retry")
        if not stored:
            return None
        defaults = DEFAULT_CONNECT_RETRY
        return RetryOptions(
            max_attempts=stored.get("maxAttempts", defaults.max_attempts),
            base_delay=stored.get("baseDelayMs", 500) / 1000,
            max_delay=stored.get("maxDelayMs", 5000) / 1000,
        )

    @property
    def snapshot(self) -> SessionSnapshot | None:
        """``SessionSnapshot | None``: the stored session, if any."""
        transport = self._transport
        version = transport.get("protocolVersion")
        if not version:
            return None
        return SessionSnapshot(
            protocol_version=version,
            session_id=transport.get("sessionId"),
            discover_result=self.options.get("discoverResult"),
        )


DEFAULT_CONNECT_RETRY = RetryOptions(max_attempts=3, base_delay=0.5, max_delay=5)
"""Upstream's connection retries (``client/index.ts:1140``); the stored
defaults below are the same values in milliseconds."""


@dataclass(slots=True, kw_only=True)
class MCPServerInfo:
    """One server in the ``cf_agent_mcp_servers`` frame.

    ``state`` is ``not-connected`` for a stored server with no live
    connection. ``error`` may contain untrusted text from an OAuth provider:
    escape it before showing it.
    """

    name: str
    server_url: str
    auth_url: str | None
    state: str
    error: str | None
    instructions: str | None
    capabilities: dict[str, Any] | None


@dataclass(slots=True, kw_only=True)
class MCPServersState:
    """What the ``cf_agent_mcp_servers`` frame carries.

    ``tools``, ``prompts`` and ``resources`` are the servers' own JSON
    objects with a ``serverId`` added, as upstream sends them.
    """

    servers: dict[str, MCPServerInfo] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)
    prompts: list[dict[str, Any]] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class MCPOAuthResult:
    """The outcome of an OAuth callback, passed to ``on_mcp_oauth_callback``.

    ``error`` may contain untrusted text from the OAuth provider.
    """

    server_id: str | None
    success: bool
    error: str | None = None


@dataclass(slots=True, kw_only=True)
class StateCheck:
    """Whether an OAuth ``state`` parameter is one this provider issued."""

    valid: bool
    error: str | None = None


class OAuthTokens(TypedDict):
    """An RFC 6749 token response."""

    access_token: str
    token_type: str
    expires_in: NotRequired[int]
    scope: NotRequired[str]
    refresh_token: NotRequired[str]


class OAuthClientMetadata(TypedDict):
    """An RFC 7591 client registration request."""

    redirect_uris: list[str]
    client_name: NotRequired[str]
    client_uri: NotRequired[str]
    grant_types: NotRequired[list[str]]
    response_types: NotRequired[list[str]]
    token_endpoint_auth_method: NotRequired[str]
    scope: NotRequired[str]


class OAuthClientInformation(TypedDict):
    """An RFC 7591 registration response: the client's credentials.

    ``issuer`` is added by the client, to drop credentials registered with a
    different authorization server.
    """

    client_id: str
    client_secret: NotRequired[str]
    client_id_issued_at: NotRequired[int]
    client_secret_expires_at: NotRequired[int]
    token_endpoint_auth_method: NotRequired[str]
    redirect_uris: NotRequired[list[str]]
    issuer: NotRequired[str]
