"""The MCP client capability: every server an object is connected to.

Port of upstream ``client/index.ts`` (``MCPClientManager``): registering,
restoring and removing servers, the aggregated catalog, calls, and the
OAuth callback route (``.design/mcp_engine.md`` §6, §7.3).
"""

import asyncio
import logging
import re
import secrets
from collections.abc import Awaitable, Callable, Coroutine, Iterator, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, override
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from workers import Response

from ..core.events import Disposable, Emitter
from ..core.retry import run_with_retries
from ..core.timing import to_seconds
from ..core.types import Duration, RetryOptions
from ..lifecycle.capability import LifecycleCapability
from .connection import MCPClientConnection
from .errors import (
    MCPBlockedUrlError,
    MCPConnectionError,
    MCPError,
    MCPOAuthError,
    MCPServerIdConflictError,
    MCPServerNotFoundError,
)
from .http import FetchTransport, HttpTransport
from .models import (
    CallToolResult,
    GetPromptResult,
    Prompt,
    ReadResourceResult,
    Resource,
    ResourceTemplate,
    ServerPrompt,
    ServerResource,
    ServerResourceTemplate,
    ServerTool,
    Tool,
)
from .oauth import DurableObjectOAuthProvider, MCPOAuthProvider
from .oauth_flow import OAuthFlow
from .storage import McpServerStore, encode_options
from .types import (
    DEFAULT_CONNECT_RETRY,
    AddMcpServerResult,
    MCPConnectionState,
    MCPOAuthResult,
    MCPServerInfo,
    MCPServerRecord,
    MCPServersState,
    TransportType,
)
from .urls import is_blocked_url, normalize_server_id

if TYPE_CHECKING:
    from workers import Request

__all__ = ("MCPClientManager",)

_log = logging.getLogger(__name__)

_MAX_ERROR_LENGTH = 500
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

type _StateFilter = MCPConnectionState | Sequence[MCPConnectionState] | None


class _ConnectFailedError(Exception):
    """An attempt ended ``FAILED``; retried by `run_with_retries`."""


class MCPClientManager(LifecycleCapability):
    """Connects a Durable Object to MCP servers and keeps them across wakes.

    Installed by ``Agent`` when ``AgentOptions(mcp=...)`` is set; works on any
    Lifecycle (``lifecycle.use(MCPClientManager(...))``).

    Parameters
    ----------
    client_name, client_version
        Sent to servers as this client's identity.
    create_oauth_provider
        Builds the OAuth provider for a server's callback URL. The default
        stores everything in Durable Object storage.
    on_oauth_callback
        Runs after every OAuth callback; a returned ``Response`` replaces the
        default redirect.
    oauth_success_redirect, oauth_error_redirect
        Where the callback redirects, resolved against the request's origin;
        the error redirect gets ``?error=<message>``.
    discovery_timeout
        How long loading a server's catalog may take.
    keep_alive
        Takes a keep-alive lease, held while a background connect runs.
    http
        The transport for every request (tests pass their own).
    """

    def __init__(
        self,
        *,
        client_name: str,
        client_version: str,
        create_oauth_provider: Callable[[str], MCPOAuthProvider] | None = None,
        on_oauth_callback: (
            Callable[[MCPOAuthResult, "Request"], Awaitable[Response | None]] | None
        ) = None,
        oauth_success_redirect: str | None = None,
        oauth_error_redirect: str | None = None,
        discovery_timeout: Duration = 15,
        keep_alive: Callable[[], Awaitable[Disposable]] | None = None,
        http: HttpTransport | None = None,
    ) -> None:
        super().__init__("mcp")
        self._client_info = {"name": client_name, "version": client_version}
        self._create_oauth_provider = create_oauth_provider
        self._on_oauth_callback = on_oauth_callback
        self._success_redirect = oauth_success_redirect
        self._error_redirect = oauth_error_redirect
        self._discovery_timeout = discovery_timeout
        self._keep_alive = keep_alive
        self.http: HttpTransport = http or FetchTransport()
        """The transport every request goes through."""
        self._connections: dict[str, MCPClientConnection] = {}
        self._flows: dict[str, OAuthFlow] = {}
        self._pending: dict[str, asyncio.Task[None]] = {}
        self._store_instance: McpServerStore | None = None
        self._changed = Emitter[None]()
        self.on_server_state_changed = self._changed.subscribe
        """Subscribe to every server state or catalog change."""

    @property
    def connections(self) -> Mapping[str, MCPClientConnection]:
        """``Mapping[str, MCPClientConnection]``: live connections by server id."""
        return MappingProxyType(self._connections)

    @property
    def _store(self) -> McpServerStore:
        if self._store_instance is None:
            self._store_instance = McpServerStore(self.lifecycle.sql)
        return self._store_instance

    # Lifecycle hooks

    @override
    async def on_start(self) -> None:
        """Create the table, then restore every stored server in the background."""
        await self._store.ensure_schema(self.lifecycle.storage)
        for record in self._store.records():
            connection = self._open(record)
            if record.auth_url:
                # OAuth is in progress: wait for the callback.
                connection.state = MCPConnectionState.AUTHENTICATING
                connection.auth_url = record.auth_url
                continue
            self._track(record.id, self._establish(record.id))
        self._fire()

    @override
    async def on_request(self, request: "Request") -> Response | None:
        """Claim an OAuth callback for one of this object's servers."""
        server_id = self._callback_server(request)
        if server_id is None:
            return None
        result = await self._handle_callback(request, server_id)
        if result.success:
            self._track(server_id, self._establish(server_id))
        return await self._callback_response(result, request)

    @override
    async def dispose(self) -> None:
        """Close every connection."""
        for task in self._pending.values():
            task.cancel()
        self._pending.clear()
        connections = list(self._connections.values())
        self._connections.clear()
        await asyncio.gather(*(connection.close() for connection in connections))
        self._changed.dispose()

    # Servers

    async def add_server(
        self,
        name: str,
        url: str,
        *,
        id: str | None = None,
        callback_url: str | None = None,
        headers: Mapping[str, str] | None = None,
        transport: TransportType = "auto",
        retry: RetryOptions | None = None,
    ) -> AddMcpServerResult:
        """Register a server, connect to it and load its catalog.

        Adding the same name and URL again returns the existing server.

        Parameters
        ----------
        name
            A display name.
        url
            The server's Streamable HTTP endpoint.
        id
            A stable id (normalized); by default a random one.
        callback_url
            The OAuth callback URL; without one, OAuth servers fail.
        headers
            Sent on every request, and stored with the server.
        transport
            ``auto`` or ``streamable-http`` (the same thing for now).
        retry
            Connection retries when the server is restored.

        Returns
        -------
        AddMcpServerResult
            ``ready``, or ``authenticating`` with the URL the user must visit.

        Raises
        ------
        MCPServerIdConflictError
            If ``id`` belongs to a different name or URL.
        MCPBlockedUrlError
            If ``url`` points at a private address.
        MCPConnectionError
            If connecting or discovery fails (the server stays stored).
        """
        url = _normalize_url(url)
        requested = normalize_server_id(id) if id is not None else None
        existing = self._find(name, url, requested)
        if existing is not None and existing.id in self._connections:
            return await self._readd(existing)
        if is_blocked_url(url):
            raise MCPBlockedUrlError(url)

        record = MCPServerRecord(
            id=requested or (existing.id if existing else secrets.token_urlsafe(6)),
            name=name,
            server_url=url,
            callback_url=callback_url or "",
            options=encode_options(
                transport_type=transport, headers=dict(headers or {}), retry=retry
            ),
        )
        self._store.save(record)
        self._open(record)
        self._fire()
        return await self._connect_and_report(record.id)

    def _find(
        self, name: str, url: str, requested: str | None
    ) -> MCPServerRecord | None:
        records = self._store.records()
        if requested is None:
            return next(
                (r for r in records if r.name == name and r.server_url == url), None
            )
        existing = next((r for r in records if r.id == requested), None)
        if existing is not None and (
            existing.name != name or existing.server_url != url
        ):
            raise MCPServerIdConflictError(requested)
        return existing

    async def _readd(self, record: MCPServerRecord) -> AddMcpServerResult:
        pending = self._pending.get(record.id)
        if pending is not None:
            await asyncio.wait([pending])  # a restore or callback still connecting
        connection = self._connections[record.id]
        if connection.state == MCPConnectionState.AUTHENTICATING:
            auth_url = connection.auth_url or record.auth_url
            if auth_url is not None and await self._redeemable(record.id, auth_url):
                return AddMcpServerResult(
                    id=record.id, state="authenticating", auth_url=auth_url
                )
            return await self._connect_and_report(record.id)
        if connection.state == MCPConnectionState.READY:
            return AddMcpServerResult(id=record.id, state="ready")
        return await self._connect_and_report(record.id)

    async def _redeemable(self, server_id: str, auth_url: str) -> bool:
        # A stored URL is offered again only while its state can still be redeemed.
        state = dict(parse_qsl(urlsplit(auth_url).query)).get("state")
        flow = self._flows.get(server_id)
        if state is None:
            return True
        return flow is not None and (await flow.provider.check_state(state)).valid

    async def connect_server(self, id: str) -> AddMcpServerResult:
        """Connect a stored server again (after a failure), and load its catalog.

        Raises
        ------
        MCPServerNotFoundError
            If no server has this id.
        MCPConnectionError
            If connecting or discovery fails.
        """
        self._get(id)
        return await self._connect_and_report(id)

    async def _connect_and_report(self, server_id: str) -> AddMcpServerResult:
        connection = self._connections[server_id]
        state = await self._connect(server_id, retry=False)
        if state == MCPConnectionState.CONNECTED:
            await connection.discover(timeout=self._discovery_timeout)
            state = connection.state
        if state == MCPConnectionState.AUTHENTICATING:
            return AddMcpServerResult(
                id=server_id, state="authenticating", auth_url=connection.auth_url
            )
        if state != MCPConnectionState.READY:
            raise MCPConnectionError(server_id, connection.error or state.value)
        return AddMcpServerResult(id=server_id, state="ready")

    async def remove_server(self, id: str) -> None:
        """Close the server's connection and delete it and its OAuth state.

        This doesn't revoke tokens at the OAuth provider.
        """
        task = self._pending.pop(id, None)
        if task is not None:
            task.cancel()
        connection = self._connections.pop(id, None)
        if connection is not None:
            await connection.close()
        flow = self._flows.pop(id, None)
        record = self._store.get(id)
        if flow is None and record is not None and record.callback_url:
            flow = self._flow(record)
        if flow is not None:
            await flow.provider.clear()
        self._store.delete(id)
        self._fire()

    async def close_connection(self, id: str) -> None:
        """Close the server's connection, keeping it stored.

        Raises
        ------
        MCPServerNotFoundError
            If there's no connection with this id.
        """
        connection = self._get(id)
        task = self._pending.pop(id, None)
        if task is not None:
            task.cancel()
        del self._connections[id]
        await connection.close()
        self._fire()

    async def discover(self, id: str, *, timeout: Duration | None = None) -> None:
        """Reload a connected server's catalog.

        Raises
        ------
        MCPServerNotFoundError
            If there's no connection with this id.
        """
        await self._get(id).discover(timeout=timeout or self._discovery_timeout)

    async def wait_for_connections(self, *, timeout: Duration | None = None) -> None:
        """Wait until every background connect and discovery has settled.

        Never raises. ``timeout`` bounds the wait; ``0`` returns at once.
        """
        pending = list(self._pending.values())
        seconds = None if timeout is None else to_seconds(timeout)
        if not pending or (seconds is not None and seconds <= 0):
            return
        await asyncio.wait(pending, timeout=seconds)

    def list_servers(self) -> list[MCPServerRecord]:
        """Return every stored server."""
        return self._store.records()

    def servers_state(self) -> MCPServersState:
        """Return what the ``cf_agent_mcp_servers`` frame carries."""
        state = MCPServersState()
        for record in self._store.records():
            connection = self._connections.get(record.id)
            if connection is None:
                status = "authenticating" if record.auth_url else "not-connected"
            else:
                status = connection.state.value
            state.servers[record.id] = MCPServerInfo(
                name=record.name,
                server_url=record.server_url,
                auth_url=record.auth_url,
                state=status,
                error=_sanitize(connection.error) if connection else None,
                instructions=connection.instructions if connection else None,
                capabilities=connection.capabilities if connection else None,
            )
        for server_id, connection in self._connections.items():
            state.tools.extend({**t, "serverId": server_id} for t in connection.tools)
            state.prompts.extend(
                {**p, "serverId": server_id} for p in connection.prompts
            )
            state.resources.extend(
                {**r, "serverId": server_id} for r in connection.resources
            )
        return state

    # The catalog

    def list_tools(
        self,
        *,
        server_id: str | Sequence[str] | None = None,
        server_name: str | Sequence[str] | None = None,
        state: _StateFilter = None,
    ) -> list[ServerTool]:
        """Return the tools of the servers matching every given filter."""
        return [
            ServerTool(server_id=sid, tool=tool)
            for sid, tool in self._entries(
                "tools", Tool.from_wire, server_id, server_name, state
            )
        ]

    def list_prompts(
        self,
        *,
        server_id: str | Sequence[str] | None = None,
        server_name: str | Sequence[str] | None = None,
        state: _StateFilter = None,
    ) -> list[ServerPrompt]:
        """Return the prompts of the servers matching every given filter."""
        return [
            ServerPrompt(server_id=sid, prompt=prompt)
            for sid, prompt in self._entries(
                "prompts", Prompt.from_wire, server_id, server_name, state
            )
        ]

    def list_resources(
        self,
        *,
        server_id: str | Sequence[str] | None = None,
        server_name: str | Sequence[str] | None = None,
        state: _StateFilter = None,
    ) -> list[ServerResource]:
        """Return the resources of the servers matching every given filter."""
        return [
            ServerResource(server_id=sid, resource=resource)
            for sid, resource in self._entries(
                "resources", Resource.from_wire, server_id, server_name, state
            )
        ]

    def list_resource_templates(
        self,
        *,
        server_id: str | Sequence[str] | None = None,
        server_name: str | Sequence[str] | None = None,
        state: _StateFilter = None,
    ) -> list[ServerResourceTemplate]:
        """Return the resource templates of the servers matching every given filter."""
        return [
            ServerResourceTemplate(server_id=sid, resource_template=template)
            for sid, template in self._entries(
                "resource_templates",
                ResourceTemplate.from_wire,
                server_id,
                server_name,
                state,
            )
        ]

    def _entries[T](
        self,
        kind: str,
        decode: Callable[[Mapping[str, Any]], T],
        server_id: str | Sequence[str] | None,
        server_name: str | Sequence[str] | None,
        state: _StateFilter,
    ) -> Iterator[tuple[str, T]]:
        ids, names, states = _as_set(server_id), _as_set(server_name), _as_set(state)
        if names is not None:
            matched = {r.id for r in self._store.records() if r.name in names}
            ids = matched if ids is None else ids & matched
        for sid, connection in self._connections.items():
            if (ids is not None and sid not in ids) or (
                states is not None and connection.state not in states
            ):
                continue
            for raw in getattr(connection, kind):
                try:
                    yield sid, decode(raw)
                except KeyError:  # a server's malformed entry: skip it
                    _log.warning("Skipping a malformed %s entry from %s", kind, sid)

    # Calls

    async def call_tool(
        self,
        server_id: str,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        timeout: Duration | None = None,
    ) -> CallToolResult:
        """Call a tool. A tool error is ``is_error=True``, not an exception.

        Raises
        ------
        MCPServerNotFoundError
            If there's no connection with this id.
        MCPServerNotReadyError
            If the server isn't ready.
        MCPRpcError, MCPTransportError, MCPAuthorizationRequiredError
            If the call fails.
        """
        return await self._get(server_id).call_tool(
            name, arguments, timeout=60 if timeout is None else timeout
        )

    async def read_resource(self, server_id: str, uri: str) -> ReadResourceResult:
        """Read a resource (raises as `call_tool`)."""
        return await self._get(server_id).read_resource(uri)

    async def get_prompt(
        self, server_id: str, name: str, arguments: Mapping[str, str] | None = None
    ) -> GetPromptResult:
        """Render a prompt (raises as `call_tool`)."""
        return await self._get(server_id).get_prompt(name, arguments)

    # Connections

    def _get(self, server_id: str) -> MCPClientConnection:
        connection = self._connections.get(server_id)
        if connection is None:
            raise MCPServerNotFoundError(server_id)
        return connection

    def _flow(self, record: MCPServerRecord) -> OAuthFlow:
        factory = self._create_oauth_provider or self._default_provider
        provider = factory(record.callback_url)
        provider.server_id = record.id
        provider.client_id = record.client_id
        return OAuthFlow(provider, server_url=record.server_url, http=self.http)

    def _default_provider(self, callback_url: str) -> MCPOAuthProvider:
        return DurableObjectOAuthProvider(
            self.lifecycle.storage, self._client_info["name"], callback_url
        )

    def _open(self, record: MCPServerRecord) -> MCPClientConnection:
        flow = self._flow(record) if record.callback_url else None
        if flow is not None:
            self._flows[record.id] = flow
        connection = MCPClientConnection(
            record.id,
            record.server_url,
            http=self.http,
            client_info=self._client_info,
            headers=record.headers,
            transport_type=record.transport_type,
            auth=flow,
            prior=record.snapshot,
            emit=self.lifecycle.emit,
            on_change=self._fire,
            on_authorize=self._authorize,
        )
        self._connections[record.id] = connection
        return connection

    def _authorize(self, connection: MCPClientConnection) -> None:
        flow = self._flows.get(connection.server_id)
        client_id = flow.provider.client_id if flow is not None else None
        if connection.auth_url is None:
            return
        self._store.set_auth(
            connection.server_id, auth_url=connection.auth_url, client_id=client_id
        )
        payload: dict[str, Any] = {
            "serverId": connection.server_id,
            "authUrl": connection.auth_url,
        }
        if client_id is not None:
            payload["clientId"] = client_id
        self.lifecycle.emit("mcp:client:authorize", payload)
        self._fire()  # again, now that auth_url is stored

    async def _connect(self, server_id: str, *, retry: bool) -> MCPConnectionState:
        connection = self._connections[server_id]
        record = self._store.get(server_id)
        options = (record.retry if record else None) or DEFAULT_CONNECT_RETRY

        async def attempt(_attempt: int) -> MCPConnectionState:
            state = await connection.init()
            if state == MCPConnectionState.FAILED:
                raise _ConnectFailedError
            return state

        if not retry:
            options = RetryOptions(max_attempts=1)
        try:
            state = await run_with_retries(
                attempt,
                options,
                should_retry=lambda error, _: isinstance(error, _ConnectFailedError),
            )
        except _ConnectFailedError:
            state = MCPConnectionState.FAILED
        self._store.update_session(server_id, connection.snapshot)
        return state

    async def _establish(self, server_id: str) -> None:
        # Restore and post-callback connects (index.ts:1904).
        connection = self._connections.get(server_id)
        if connection is None:
            self.lifecycle.emit("mcp:client:preconnect", {"serverId": server_id})
            return
        if connection.state in (
            MCPConnectionState.DISCOVERING,
            MCPConnectionState.READY,
        ):
            return
        if await self._connect(server_id, retry=True) == MCPConnectionState.CONNECTED:
            await connection.discover(timeout=self._discovery_timeout)

    def _track(self, server_id: str, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(self._leased(work))
        self._pending[server_id] = task

        def settled(done: asyncio.Task[None]) -> None:
            if self._pending.get(server_id) is done:
                del self._pending[server_id]

        task.add_done_callback(settled)

    async def _leased(self, work: Coroutine[Any, Any, None]) -> None:
        # Python tasks don't keep a Durable Object alive; a lease does.
        lease = await self._keep_alive() if self._keep_alive is not None else None
        try:
            await work
        finally:
            if lease is not None:
                lease.dispose()

    def _fire(self) -> None:
        self._changed.fire(None)

    # The OAuth callback

    def _callback_server(self, request: "Request") -> str | None:
        # index.ts:1591: a GET on a stored callback URL whose state names a server.
        if request.method != "GET":
            return None
        url = urlsplit(request.url)
        state = dict(parse_qsl(url.query)).get("state", "")
        parts = state.split(".")
        if len(parts) != 2 or not parts[1]:
            return None
        record = self._store.get(parts[1])
        if record is None or not record.callback_url:
            return None
        stored = urlsplit(record.callback_url)
        if (stored.scheme, stored.netloc, stored.path) != (
            url.scheme,
            url.netloc,
            url.path,
        ):
            return None
        return record.id

    async def _handle_callback(
        self, request: "Request", server_id: str
    ) -> MCPOAuthResult:
        # index.ts:1684-1810, with every edge case upstream handles.
        params = dict(parse_qsl(urlsplit(request.url).query))
        state, code, error = params["state"], params.get("code"), params.get("error")
        connection = self._connections.get(server_id)
        flow = self._flows.get(server_id)

        if connection is None:
            message = f'No connection found for serverId "{server_id}".'
            return MCPOAuthResult(server_id=server_id, success=False, error=message)
        if not code and not error:
            message = "Unauthorized: no code provided"
            if connection.is_auth_accepted():
                await _consume_if_valid(flow, state)  # a stale popup
                return self._callback_succeeded(connection)
            if flow is None or not (await flow.provider.check_state(state)).valid:
                return _ignored(server_id, message)  # can't fail someone else's
            return self._callback_failed(connection, message)

        try:
            if flow is None:
                raise MCPOAuthError("This server has no OAuth provider")
            check = await flow.provider.check_state(state)
            if not check.valid:
                if connection.is_auth_accepted():
                    return self._callback_succeeded(connection)
                reason = (
                    params.get("error_description")
                    or error
                    or check.error
                    or "Invalid state"
                )
                return _ignored(server_id, reason)
            if connection.is_auth_accepted():
                await flow.provider.consume_state(state)
                return self._callback_succeeded(connection)
            if connection.state not in (
                MCPConnectionState.AUTHENTICATING,
                MCPConnectionState.FAILED,
            ):
                raise MCPOAuthError(
                    f'Failed to authenticate from "{connection.state}" state'
                )

            connection.state = MCPConnectionState.CONNECTING
            await flow.provider.consume_state(state)
            if error or not code:
                await flow.provider.delete_code_verifier(state)
                raise MCPOAuthError(
                    params.get("error_description") or error or "No code"
                )
            await flow.exchange_code(code=code, state=state, iss=params.get("iss"))
        except MCPError as failure:
            return self._callback_failed(connection, str(failure))
        return self._callback_succeeded(connection)

    def _callback_succeeded(self, connection: MCPClientConnection) -> MCPOAuthResult:
        self._store.clear_auth_url(connection.server_id)
        connection.auth_url = None
        connection.error = None
        self._fire()
        return MCPOAuthResult(server_id=connection.server_id, success=True)

    def _callback_failed(
        self, connection: MCPClientConnection, message: str
    ) -> MCPOAuthResult:
        self._store.clear_auth_url(connection.server_id)
        connection.auth_url = None
        connection.state = MCPConnectionState.FAILED
        connection.error = message
        self._fire()
        return MCPOAuthResult(
            server_id=connection.server_id, success=False, error=message
        )

    async def _callback_response(
        self, result: MCPOAuthResult, request: "Request"
    ) -> Response:
        hook = self._on_oauth_callback
        if hook is not None:
            response = await self.lifecycle.run_in_host_context(
                lambda: hook(result, request), request=request
            )
            if response is not None:
                return response

        url = urlsplit(request.url)
        origin = f"{url.scheme}://{url.netloc}"
        redirect = self._success_redirect if result.success else self._error_redirect
        target = urljoin(f"{origin}/", redirect) if redirect else origin
        if redirect and not result.success:
            parts = urlsplit(target)
            query = [*parse_qsl(parts.query), ("error", result.error or "")]
            target = urlunsplit(parts._replace(query=urlencode(query)))
        return Response(None, status=302, headers={"location": target})


async def _consume_if_valid(flow: OAuthFlow | None, state: str) -> None:
    if flow is not None and (await flow.provider.check_state(state)).valid:
        await flow.provider.consume_state(state)


def _ignored(server_id: str, error: str) -> MCPOAuthResult:
    _log.warning(
        "Ignoring an OAuth callback for %s with unverified state: %s", server_id, error
    )
    return MCPOAuthResult(server_id=server_id, success=False, error=error)


def _normalize_url(url: str) -> str:
    # As WHATWG URL.href does for comparison: lower-case scheme and host, "/" path.
    parts = urlsplit(url)
    return urlunsplit(
        parts._replace(
            scheme=parts.scheme.lower(),
            netloc=parts.netloc.lower(),
            path=parts.path or "/",
        )
    )


def _sanitize(error: str | None) -> str | None:
    # index.ts:10051: no control characters, at most 500 characters.
    if error is None:
        return None
    cleaned = _CONTROL_CHARACTERS.sub("", error)
    if len(cleaned) > _MAX_ERROR_LENGTH:
        return cleaned[:_MAX_ERROR_LENGTH] + "..."
    return cleaned


def _as_set[T](value: T | Sequence[T] | None) -> set[Any] | None:
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, Sequence):
        return {value}
    return set(value)
