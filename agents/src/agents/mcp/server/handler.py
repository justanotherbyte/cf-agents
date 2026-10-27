"""Hosting an MCP server on a Worker.

Port of upstream ``handler-stateless.ts`` (``createMcpHandler``): each
request is served on its own by a stateless ``StreamableHTTPSessionManager``,
behind route, Host / Origin and CORS handling.
"""

import json
import logging
from collections.abc import Callable, Sequence
from typing import Any, Literal
from urllib.parse import SplitResult, urlsplit

from mcp.server import MCPServer, Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.types import Receive, Scope, Send
from workers import Request, Response

from ... import _ffi
from . import asgi
from .auth_context import auth_context as use_auth_context
from .errors import MCPServerConfigError
from .types import CORSOptions, McpAuthContext

__all__ = ("McpHandler", "create_mcp_handler")

_log = logging.getLogger(__name__)

# Upstream's localhostAllowedHostnames(), as urlsplit spells them.
_LOCALHOST = frozenset({"localhost", "127.0.0.1", "::1"})

# A stateless endpoint has no channel to deliver subscription updates on.
_SUBSCRIPTION_METHODS = (
    "subscriptions/listen",
    "resources/subscribe",
    "resources/unsubscribe",
)

type _AnyServer = MCPServer[Any] | Server[Any]


def create_mcp_handler(
    server: _AnyServer | Callable[[], _AnyServer],
    *,
    route: str = "/mcp",
    cors: CORSOptions | None = CORSOptions(),  # noqa: B008  (frozen)
    allowed_hostnames: Sequence[str] | None = None,
    allowed_origin_hostnames: Sequence[str] | Literal["*"] | None = None,
    auth_context: McpAuthContext | None = None,
    json_response: bool = False,
) -> "McpHandler":
    """Serve an MCP server at ``route`` (upstream ``createMcpHandler``).

    The endpoint is stateless and serves both protocol eras: 2026-07-28
    requests, and handshake-era (2025) clients through the SDK's stateless
    path.

    Parameters
    ----------
    server
        An ``MCPServer`` or low-level ``Server``, reused for every request,
        or a function returning one, called once per request. A factory is
        how tools reach the Worker's ``env``.
    route
        The exact path served; any other path gets a 404.
    cors
        The CORS headers added to every response, preflights included.
        ``None`` adds none and leaves ``OPTIONS`` to the server.
    allowed_hostnames
        The ``Host`` header's allowed hostnames. By default, a
        ``localhost`` endpoint accepts localhost names, a ``workers.dev``
        endpoint its own hostname, and any other endpoint any ``Host``.
    allowed_origin_hostnames
        The allowed hostnames of an ``Origin`` header, when one is present
        (non-browser clients send none). By default: localhost names, a
        ``workers.dev`` endpoint's own hostname, and the hostname of a
        concrete ``cors.origin``. ``"*"`` turns the check off; use it only
        when middleware in front does the same check.
    auth_context
        Returned by `get_mcp_auth_context` inside the server's tools.
    json_response
        Answer with JSON instead of SSE streams.

    Returns
    -------
    McpHandler
        Call it from ``fetch`` with ``(request, env, ctx)``, or use
        `McpHandler.fetch` from an agent's ``on_request``.

    Raises
    ------
    MCPServerConfigError
        If ``server`` was built with the SDK's OAuth settings.
    """
    return McpHandler(
        server,
        route=route,
        cors=cors,
        allowed_hostnames=allowed_hostnames,
        allowed_origin_hostnames=allowed_origin_hostnames,
        auth_context=auth_context,
        json_response=json_response,
    )


class McpHandler:
    """A Worker ``fetch`` handler for an MCP server; see `create_mcp_handler`."""

    __slots__ = (
        "_allowed_hostnames",
        "_allowed_origin_hostnames",
        "_auth_context",
        "_cors_headers",
        "_default_origin_hostnames",
        "_json_response",
        "_route",
        "_server",
    )

    def __init__(
        self,
        server: _AnyServer | Callable[[], _AnyServer],
        *,
        route: str,
        cors: CORSOptions | None,
        allowed_hostnames: Sequence[str] | None,
        allowed_origin_hostnames: Sequence[str] | Literal["*"] | None,
        auth_context: McpAuthContext | None,
        json_response: bool,
    ) -> None:
        self._server: Server[Any] | Callable[[], _AnyServer] = (
            _lowlevel_server(server)
            if isinstance(server, MCPServer | Server)
            else server
        )
        self._route = route
        self._cors_headers = _cors_headers(cors) if cors is not None else None
        self._allowed_hostnames = _lower(allowed_hostnames)
        self._allowed_origin_hostnames: frozenset[str] | Literal["*"] | None = (
            "*" if allowed_origin_hostnames == "*" else _lower(allowed_origin_hostnames)
        )
        self._default_origin_hostnames = _LOCALHOST | _cors_origin_hostname(cors)
        self._auth_context = auth_context
        self._json_response = json_response

    async def __call__(self, request: Request, env: Any, ctx: Any) -> Response:
        """Serve ``request`` (a ``WorkerEntrypoint.fetch``'s arguments).

        Raises
        ------
        MCPServerConfigError
            If a factory returned a server built with the SDK's OAuth
            settings.
        """
        return await self.fetch(request)

    async def fetch(self, request: Request) -> Response:
        """Serve ``request``, for example from an agent's ``on_request``.

        Tools then run inside the agent, so ``get_current_agent()`` works
        in them.

        Raises
        ------
        MCPServerConfigError
            If a factory returned a server built with the SDK's OAuth
            settings.
        """
        response = await self._serve(request)
        if self._cors_headers is None:
            return response
        return _ffi.with_headers(response, self._cors_headers)

    async def _serve(self, request: Request) -> Response:
        url = urlsplit(request.url)
        if url.path != self._route:
            return Response("Not Found", status=404)
        rejection = self._check_host(request, url) or self._check_origin(request, url)
        if rejection is not None:
            return rejection
        if request.method == "OPTIONS" and self._cors_headers is not None:
            return Response(None)

        server = self._server
        if not isinstance(server, Server):
            try:
                built = server()
            except Exception:
                _log.exception("The MCP server factory failed")
                return _jsonrpc_error(500, -32603, "Internal server error")
            server = _lowlevel_server(built)

        # run() can only be entered once, so every request gets a manager.
        manager = StreamableHTTPSessionManager(
            server, stateless=True, json_response=self._json_response
        )

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            async with manager.run():
                await manager.handle_request(scope, receive, send)

        with use_auth_context(self._auth_context):
            return await asgi.serve(app, request)

    def _check_host(self, request: Request, url: SplitResult) -> Response | None:
        # Port of upstream's acceptedHostnames: a bare Worker can't tell its
        # own address from an attacker's Host, so only known endpoints are checked.
        allowed = self._allowed_hostnames
        hostname = url.hostname or ""
        if allowed is None and hostname in _LOCALHOST:
            allowed = _LOCALHOST
        elif allowed is None and hostname.endswith(".workers.dev"):
            allowed = frozenset({hostname})
        if allowed is None:
            return None

        host = request.headers.get("host")
        if host is not None and _hostname(host) in allowed:
            return None
        return _jsonrpc_error(403, -32000, f"Invalid Host header: {host}")

    def _check_origin(self, request: Request, url: SplitResult) -> Response | None:
        origin = request.headers.get("origin")
        allowed = self._allowed_origin_hostnames
        if origin is None or allowed == "*":
            return None
        if allowed is None:
            allowed = self._default_origin_hostnames
            if url.hostname is not None and url.hostname.endswith(".workers.dev"):
                allowed |= {url.hostname}

        if _origin_hostname(origin) in allowed:
            return None
        return _jsonrpc_error(403, -32000, f"Invalid Origin header: {origin}")


def _lowlevel_server(server: _AnyServer) -> Server[Any]:
    """Return the low-level server to serve, with subscriptions turned off.

    Raises
    ------
    MCPServerConfigError
        If ``server`` was built with the SDK's OAuth settings.
    """
    if isinstance(server, MCPServer):
        # auth_server_provider and token_verifier both require auth.
        if server.settings.auth is not None:
            raise MCPServerConfigError(
                "This MCPServer uses the SDK's OAuth settings (auth, "
                "auth_server_provider, token_verifier), which the Workers handler "
                "doesn't serve; put auth in front of the handler and pass "
                "auth_context instead"
            )
        server = server._lowlevel_server
    # The SDK has no public way to drop a method once the server is built.
    for method in _SUBSCRIPTION_METHODS:
        server._request_handlers.pop(method, None)
    return server


def _cors_headers(cors: CORSOptions) -> dict[str, str]:
    return {
        "Access-Control-Allow-Headers": cors.headers,
        "Access-Control-Allow-Methods": cors.methods,
        "Access-Control-Allow-Origin": cors.origin,
        "Access-Control-Expose-Headers": cors.expose_headers,
        "Access-Control-Max-Age": str(cors.max_age),
    }


def _cors_origin_hostname(cors: CORSOptions | None) -> frozenset[str]:
    """Return a concrete CORS origin's hostname; ``*`` adds nothing."""
    hostname = _origin_hostname(cors.origin) if cors is not None else None
    return frozenset({hostname}) if hostname is not None else frozenset()


def _lower(names: Sequence[str] | None) -> frozenset[str] | None:
    return frozenset(name.lower() for name in names) if names is not None else None


def _hostname(authority: str) -> str | None:
    """Return the hostname of a ``host[:port]``; ``None`` if it's anything else."""
    parsed = urlsplit(f"//{authority}")
    if parsed.netloc != authority or "@" in authority:
        return None
    return parsed.hostname


def _origin_hostname(origin: str) -> str | None:
    """Return an ``http(s)`` origin's hostname; ``None`` for any other origin."""
    parsed = urlsplit(origin)
    if parsed.scheme not in ("http", "https") or origin != (
        f"{parsed.scheme}://{parsed.netloc}"
    ):
        return None
    return _hostname(parsed.netloc)


def _jsonrpc_error(status: int, code: int, message: str) -> Response:
    """Return a JSON-RPC error with no request id (upstream ``handler-errors.ts``)."""
    body = {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": None}
    return Response(
        json.dumps(body), status=status, headers={"Content-Type": "application/json"}
    )
