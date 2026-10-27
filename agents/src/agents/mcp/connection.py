"""One server's connection: the state machine, the catalog and the listener.

Port of upstream ``client/connection.ts`` (``MCPClientConnection``) on top of
`McpSession` (``.design/mcp_engine.md`` §5).
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine, Mapping, Sequence
from typing import Any

from ..core.timing import to_seconds
from ..core.types import Duration
from .errors import (
    MCPAuthorizationRequiredError,
    MCPError,
    MCPRpcError,
    MCPServerNotReadyError,
)
from .http import HttpTransport
from .models import CallToolResult, GetPromptResult, ReadResourceResult
from .session import McpSession, SessionAuth
from .types import MCPConnectionState, SessionSnapshot

__all__ = ("MCPClientConnection",)

_log = logging.getLogger(__name__)

_TRANSPORT = "streamable-http"
_METHOD_NOT_FOUND = -32601

# Catalog list -> (method, result key, the capability that advertises it).
_LISTS = {
    "tools": ("tools/list", "tools", "tools"),
    "prompts": ("prompts/list", "prompts", "prompts"),
    "resources": ("resources/list", "resources", "resources"),
    "resource_templates": (
        "resources/templates/list",
        "resourceTemplates",
        "resources",
    ),
}
_LIST_CHANGED = {
    "notifications/tools/list_changed": ("tools",),
    "notifications/prompts/list_changed": ("prompts",),
    "notifications/resources/list_changed": ("resources", "resource_templates"),
}


class MCPClientConnection:
    """A connection to one MCP server.

    ``init()`` connects (or reaches ``AUTHENTICATING``), ``discover()`` loads
    the catalog, and the call methods use it once it's ``READY``.

    Parameters
    ----------
    server_id
        The server's id.
    url
        The server's endpoint.
    http
        The transport every request goes through.
    client_info
        This client's name and version.
    headers
        Extra headers sent on every request.
    transport_type
        The stored transport type; only Streamable HTTP is supported.
    auth
        OAuth, when the server has a callback URL.
    prior
        A stored session to restore without a new handshake.
    emit
        Publishes an observability event (type, payload).
    on_change
        Called after every state or catalog change.
    on_authorize
        Called when the connection needs the user to authorize.
    """

    def __init__(
        self,
        server_id: str,
        url: str,
        *,
        http: HttpTransport,
        client_info: Mapping[str, str],
        headers: Mapping[str, str],
        transport_type: str = "auto",
        auth: SessionAuth | None = None,
        prior: SessionSnapshot | None = None,
        emit: Callable[[str, dict[str, Any]], None],
        on_change: Callable[[], None],
        on_authorize: Callable[["MCPClientConnection"], None],
    ) -> None:
        self.server_id = server_id
        self.url = url
        self._http = http
        self._client_info = dict(client_info)
        self._headers = dict(headers)
        self._transport_type = transport_type
        self._auth = auth
        self._prior = prior
        self._emit = emit
        self._on_change = on_change
        self._on_authorize = on_authorize
        self.state = MCPConnectionState.CONNECTING
        self.error: str | None = None
        """Why the connection failed or discovery didn't finish."""
        self.auth_url: str | None = None
        """Where the user must authorize, while ``AUTHENTICATING``."""
        self._session: McpSession | None = None
        self._catalog: dict[str, list[dict[str, Any]]] = {name: [] for name in _LISTS}
        self._listener: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    def __repr__(self) -> str:
        """Return ``<MCPClientConnection id='x' state=ready>``."""
        return f"<MCPClientConnection id={self.server_id!r} state={self.state.value}>"

    # What the server told us

    @property
    def capabilities(self) -> dict[str, Any] | None:
        """``dict | None``: the server's capabilities, once connected."""
        return self._session.capabilities if self._session is not None else None

    @property
    def instructions(self) -> str | None:
        """``str | None``: the server's instructions for the model."""
        return self._session.instructions if self._session is not None else None

    @property
    def snapshot(self) -> SessionSnapshot | None:
        """``SessionSnapshot | None``: what restoring this connection needs."""
        return self._session.snapshot if self._session is not None else None

    @property
    def tools(self) -> Sequence[dict[str, Any]]:
        """``Sequence[dict]``: the server's tools, as it sent them."""
        return self._catalog["tools"]

    @property
    def prompts(self) -> Sequence[dict[str, Any]]:
        """``Sequence[dict]``: the server's prompts, as it sent them."""
        return self._catalog["prompts"]

    @property
    def resources(self) -> Sequence[dict[str, Any]]:
        """``Sequence[dict]``: the server's resources, as it sent them."""
        return self._catalog["resources"]

    @property
    def resource_templates(self) -> Sequence[dict[str, Any]]:
        """``Sequence[dict]``: the server's resource templates, as it sent them."""
        return self._catalog["resource_templates"]

    def is_auth_accepted(self) -> bool:
        """Return whether the connection is past authorization (``index.ts:818``)."""
        return self.state in (
            MCPConnectionState.READY,
            MCPConnectionState.CONNECTED,
            MCPConnectionState.CONNECTING,
            MCPConnectionState.DISCOVERING,
        )

    # The state machine

    def _set_state(self, state: MCPConnectionState, error: str | None = None) -> None:
        self.state = state
        self.error = error
        self._on_change()

    def _authenticate(self, pending: MCPAuthorizationRequiredError) -> None:
        self.auth_url = pending.auth_url
        self._set_state(MCPConnectionState.AUTHENTICATING)
        self._on_authorize(self)

    async def init(self) -> MCPConnectionState:
        """Connect: negotiate the protocol, or restore the stored session.

        Never raises: failures become the ``FAILED`` state, and a server that
        needs OAuth leaves the connection ``AUTHENTICATING``.
        """
        await self._stop()
        self._set_state(MCPConnectionState.CONNECTING)
        if self._transport_type == "sse":
            self._fail("The legacy SSE transport isn't supported yet")
            return self.state

        self._session = McpSession(
            self.url,
            http=self._http,
            client_info=self._client_info,
            headers=self._headers,
            auth=self._auth,
            on_notification=self._on_notification,
        )
        try:
            await self._session.connect(prior=self._prior)
        except MCPAuthorizationRequiredError as pending:
            self._authenticate(pending)
        except MCPRpcError as error:
            if error.code == _METHOD_NOT_FOUND:
                self._fail(
                    f"{error.message}: the server doesn't speak Streamable HTTP "
                    "(legacy SSE servers aren't supported yet)"
                )
            else:
                self._fail(str(error))
        except MCPError as error:
            self._fail(str(error))
        else:
            self.auth_url = None
            self._set_state(MCPConnectionState.CONNECTED)
        self._emit_connect()
        return self.state

    def _fail(self, error: str) -> None:
        self._prior = None  # a retry negotiates afresh
        self._set_state(MCPConnectionState.FAILED, error)

    def _emit_connect(self) -> None:
        payload: dict[str, Any] = {
            "url": self.url,
            "transport": _TRANSPORT,
            "state": self.state.value,
        }
        if self.error is not None:
            payload["error"] = self.error
        self._emit("mcp:client:connect", payload)

    # Discovery

    async def discover(self, *, timeout: Duration) -> None:
        """Load the catalog the server advertises, then become ``READY``.

        Never raises: on an error or timeout the connection goes back to
        ``CONNECTED`` with the error kept, so it can be retried (upstream
        ``connection.ts:527``).
        """
        session = self._session
        if session is None or self.state not in (
            MCPConnectionState.CONNECTED,
            MCPConnectionState.READY,
        ):
            self._emit(
                "mcp:client:discover", {"url": self.url, "state": self.state.value}
            )
            return

        self._set_state(MCPConnectionState.DISCOVERING)
        try:
            await self._load_catalog(session, timeout)
        except MCPAuthorizationRequiredError as pending:
            self._authenticate(pending)
            return
        except (MCPError, TimeoutError) as error:
            message = str(error) or f"Discovery timed out after {to_seconds(timeout)}s"
            self._set_state(MCPConnectionState.CONNECTED, message)
            self._emit("mcp:client:discover", {"url": self.url, "error": message})
            return

        self._set_state(MCPConnectionState.READY)
        self._emit("mcp:client:discover", {"url": self.url})
        self._listener = self._spawn(self._listen(session))

    async def _load_catalog(self, session: McpSession, timeout: Duration) -> None:
        capabilities = session.capabilities
        wanted = [name for name, (*_, cap) in _LISTS.items() if cap in capabilities]
        try:
            async with (
                asyncio.timeout(to_seconds(timeout)),
                asyncio.TaskGroup() as group,
            ):
                for name in wanted:
                    group.create_task(self._refresh(session, name))
        except ExceptionGroup as failures:  # the first list that failed
            raise failures.exceptions[0] from None

    async def _refresh(self, session: McpSession, name: str) -> None:
        method, key, _ = _LISTS[name]
        try:
            self._catalog[name] = await session.list_all(method, key)
        except MCPRpcError as error:
            # A server that advertises a capability but not its list method.
            if error.code != _METHOD_NOT_FOUND:
                raise
            self._catalog[name] = []
            self._emit(
                "mcp:client:discover",
                {
                    "url": self.url,
                    "capability": method.split("/")[0],
                    "error": str(error),
                },
            )

    # Notifications

    def _on_notification(self, method: str, params: dict[str, Any]) -> None:
        names = _LIST_CHANGED.get(method)
        session = self._session
        if names is not None and session is not None:
            self._spawn(self._refresh_lists(session, names))

    async def _refresh_lists(self, session: McpSession, names: Sequence[str]) -> None:
        try:
            for name in names:
                await self._refresh(session, name)
        except MCPError:
            _log.warning(
                "Refreshing %s for %s failed", names, self.server_id, exc_info=True
            )
            return
        self._on_change()

    async def _listen(self, session: McpSession) -> None:
        try:
            await session.listen()
        except MCPError:
            _log.info(
                "The notification stream for %s ended", self.server_id, exc_info=True
            )

    def _spawn(self, work: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # Calls

    def _ready_session(self) -> McpSession:
        if self.state != MCPConnectionState.READY or self._session is None:
            raise MCPServerNotReadyError(self.server_id, self.state.value)
        return self._session

    async def _guard[T](self, call: Coroutine[Any, Any, T]) -> T:
        try:
            return await call
        except MCPAuthorizationRequiredError as pending:
            self._authenticate(pending)
            raise

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        timeout: Duration | None = 60,
    ) -> CallToolResult:
        """Call a tool. A tool error is ``is_error=True``, not an exception.

        Raises
        ------
        MCPServerNotReadyError
            If the connection isn't ``READY``.
        MCPRpcError
            If the server rejects the call or it times out.
        MCPTransportError
            If the server can't be reached.
        MCPAuthorizationRequiredError
            If the server now needs OAuth (the connection is
            ``AUTHENTICATING`` afterwards).
        """
        session = self._ready_session()
        raw = await self._guard(
            session.call_tool(name, arguments or {}, timeout=timeout)
        )
        return CallToolResult.from_wire(raw)

    async def get_prompt(
        self, name: str, arguments: Mapping[str, str] | None = None
    ) -> GetPromptResult:
        """Render a prompt (raises as `call_tool`)."""
        session = self._ready_session()
        return GetPromptResult.from_wire(
            await self._guard(session.get_prompt(name, arguments))
        )

    async def read_resource(self, uri: str) -> ReadResourceResult:
        """Read a resource (raises as `call_tool`)."""
        session = self._ready_session()
        return ReadResourceResult.from_wire(
            await self._guard(session.read_resource(uri))
        )

    # Closing

    async def _stop(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._listener = None

    async def close(self) -> None:
        """Stop the listener and end the session; never raises."""
        await self._stop()
        session, self._session = self._session, None
        if session is not None:
            try:
                await session.close()
            except MCPError as error:
                self._emit(
                    "mcp:client:close",
                    {
                        "url": self.url,
                        "transport": _TRANSPORT,
                        "state": "error",
                        "error": str(error),
                        "phase": "terminate-session",
                    },
                )
        self._emit(
            "mcp:client:close",
            {"url": self.url, "transport": _TRANSPORT, "state": "closed"},
        )
