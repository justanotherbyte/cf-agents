# MCP: internals (proposed)

How the API in [mcp_api.md](./mcp_api.md) is built. Upstream:
`mcp/client/index.ts` (`MCPClientManager`, 2,456 lines), `connection.ts`,
`storage.ts`, `do-oauth-client-provider.ts`, `server/handler-stateless.ts`,
`server/auth-context.ts`, and the MCP wiring in `src/index.ts`. The Python
SDK facts here were checked against `mcp` 2.3.0.

---

## 1. Layout

```
src/agents/
├── agent/
│   ├── types.py          # + MCPOptions (no mcp import)
│   ├── errors.py         # + MCPNotEnabledError
│   └── agent.py          # + install on options.mcp, add/remove/get_mcp_servers,
│                         #   the two hooks, the dynamic cf_agent_mcp_servers frame
├── chat/types.py         # + AIChatAgentOptions.wait_for_mcp_connections
└── mcp/
    ├── __init__.py       # import guard (§9) + client re-exports, explicit __all__
    ├── types.py          # MCPConnectionState, records, MCPServersState, persisted-options shapes
    ├── errors.py         # MCPError and subclasses
    ├── client/
    │   ├── __init__.py
    │   ├── manager.py    # MCPClientManager: registry, add/remove, restore, waiting, catalog
    │   │                 #   aggregation, OAuth callback routing, state-change emitter
    │   ├── connection.py # MCPClientConnection: the state machine and the connection task
    │   ├── storage.py    # cf_agents_mcp_servers, schema version key, server_options codec
    │   ├── oauth.py      # MCPOAuthProvider, DurableObjectOAuthProvider, the deferred-grant subclass
    │   ├── callback.py   # matching and validating callback requests; the default response
    │   ├── http.py       # the httpx2 client factory (headers, auth, timeouts)
    │   └── urls.py       # normalize_server_id, is_blocked_url, callback URL building
    └── server/
        ├── __init__.py
        ├── handler.py    # create_mcp_handler, McpHandler, route/CORS/Host/Origin checks
        ├── asgi.py       # the request ⇄ ASGI bridge for one request
        └── auth_context.py  # McpAuthContext, the ContextVar, get_mcp_auth_context
```

- **Where `mcp/` sits in the dependency order:** at the same level as
  `chat/`, above `agent`:
  `core → lifecycle → capabilities → agent → {chat, mcp}`. `agents.mcp`
  may import `agent.types` (for `MCPOptions`) but not `agent.agent`. Like
  upstream, `MCPClientManager` depends only on Lifecycle services, so it
  works on plain DOs.
- **The one upward import:** `Agent.__init__` imports
  `agents.mcp.client.manager` inside the `if options.mcp:` branch. That's
  the only place core touches `agents.mcp`, and the import-boundary test
  (§9) keeps it that way. Upstream builds MCP in `Agent`'s constructor too.
- **Matching upstream's files:** `manager.py` is `client/index.ts`,
  `connection.ts` → `connection.py`, `storage.ts` → `storage.py`,
  `do-oauth-client-provider.ts` → `oauth.py`, `handler-stateless.ts` →
  `server/handler.py`, and `auth-context.ts` → `server/auth_context.py`.
  `callback.py` and `urls.py` are pieces split out of the 2.4k-line
  `index.ts`. Not mirrored: `rpc.ts`, `x402.ts`, `transports.ts`
  (deprecated aliases), `catalog.ts` / `invoker.ts` (the AI SDK tool
  conversion), and the legacy server files.

---

## 2. How it's installed and started

```
Agent.__init__
  └─ options.mcp set?
       ├─ no  → self._mcp = None (the frame stays empty; self.mcp raises MCPNotEnabledError)
       └─ yes → self._mcp = self.use(MCPClientManager(
                    client_name=options.mcp.client_name or type(self).__name__,
                    create_oauth_provider=self.create_mcp_oauth_provider,
                    on_oauth_callback=self.on_mcp_oauth_callback, ...))
                self._mcp.on_server_state_changed.subscribe(self._on_mcp_state_changed)

MCPClientManager.on_start            (Lifecycle startup, before the agent's on_start)
  ├─ schema version key "cf_agents:mcp_schema_version" < 1 → CREATE TABLE IF NOT EXISTS, set to 1
  ├─ restore (§4): build a connection for every row; OAuth-pending rows go to AUTHENTICATING;
  │  the rest start connecting in the background
  └─ fire on_server_state_changed
```

- **Broadcasting waits until the agent has started**, as upstream's
  `mcpBroadcastReady` does. The manager starts before the agent has
  restored its own routing state, so `_on_mcp_state_changed` does nothing
  until the agent's startup finishes, then sends one broadcast.
- **The connect frame** replaces the constant `_MCP_SERVERS_FRAME` with
  `self._mcp_servers_frame()`: the empty frame when `_mcp is None`,
  otherwise `servers_state()` serialized. The broadcast goes to
  protocol-enabled connections only, like state broadcasts.

---

## 3. Connections (`connection.py`)

### 3.1 The state machine

The same as upstream (`connection.ts:49`):

```
no OAuth:  CONNECTING → CONNECTED → DISCOVERING → READY
OAuth:     AUTHENTICATING → (callback) → CONNECTING → CONNECTED → DISCOVERING → READY
any state → FAILED (error kept on the connection, sanitized in the frame)
```

Every transition fires the manager's `on_server_state_changed`.

### 3.2 The connection task

`mcp.Client` is an async context manager. On entry it starts an anyio task
group: the HTTP POST writer, and for handshake-era servers the GET event
stream. **anyio requires a cancel scope to be exited by the task that
entered it.** A connection outlives the request that created it, so it can't
be entered in one request and closed in another. Each connection therefore
owns one background task (`asyncio.create_task`, which works on Python
Workers per [platform_verification.md](./platform_verification.md) §2.7):

```python
async def _run(self) -> None:
    try:
        async with self._open_client() as client:     # transport + handshake / adopt
            self._client = client
            self._connected.set_result(None)           # init() is waiting on this
            await self._closing.wait()                 # parked until close()
    except AuthorizationRequired as pending:            # raised by the OAuth provider (§6)
        self._fail_or_authenticate(pending)
    except Exception as error:
        self._fail(error)
    finally:
        self._client = None
```

- **`init()`** starts `_run` and waits for `_connected`, or for the task to
  end. It returns the resulting state, which is upstream's `init()`
  contract.
- **Calls** (`call_tool`, `list_tools`, …) run on whatever task called them,
  using `self._client`. anyio's memory streams are safe to use from any task
  on the same loop.
- **`close()`** sets `_closing`, then waits for `_run` to finish with a
  bounded timeout. Exiting `Client` sends the session `DELETE` for
  handshake-era servers.
- **A connection that dies on its own** (the transport errors, or the server
  ends the session) ends `_run`, which moves to `FAILED` and fires a state
  change. Upstream does the same through `onclose` / `onerror`.

### 3.3 Building the client

`_open_client()` builds the transport, then `mcp.Client(transport,
client_info=Implementation(name, version), mode=..., prior_discover=...,
message_handler=self._on_message)`:

- **Transport `auto`** tries Streamable HTTP and falls back to SSE
  (`mcp.client.sse.sse_client`) only on the errors upstream treats as
  "this transport doesn't exist" (404 / 405 on the first POST). `auto` then
  records which transport worked, so a restore doesn't probe again.
- **Protocol era:** the first connect uses `mode="auto"`, where the SDK
  probes `server/discover` and falls back to the `initialize` handshake.
  The negotiated version, and for 2026-07-28 servers the `DiscoverResult`,
  are persisted (§5). On restore, a 2026-07-28 server uses
  `mode=<version>, prior_discover=<stored>`, so **reconnecting a modern
  server makes no network request**. A handshake-era server makes a new
  handshake: the Python SDK's `streamable_http_client` has no way to resume
  a stored `Mcp-Session-Id`, so upstream's session resumption
  (`_recoverStaleSession`) isn't ported.
- **HTTP** (`http.py`): one `httpx2.AsyncClient` per connection, with the
  stored `headers`, `auth=<OAuth provider>` when there's a callback URL, and
  the SDK's recommended timeouts (`create_mcp_http_client`). On Workers,
  `httpx2` sends through JS `fetch` via `httpx2-jsfetch`. Whether
  cancelling it aborts the request is still to be verified
  ([mcp.md](./mcp.md) §5.3). If it doesn't, `http.py` is where an aborting
  transport goes.
- **Notifications:** `_on_message` reacts to
  `notifications/{tools,prompts,resources}/list_changed` by re-running that
  one list (as upstream's `onListChanged`) and firing a state change.
  Upstream's debounced notification methods are a client option, and are
  left out of the first pass.

### 3.4 Discovery

`discover()` moves to `DISCOVERING` and runs `list_tools`, `list_prompts`,
`list_resources` and `list_resource_templates` concurrently, skipping any
the server's capabilities don't advertise. It holds the results in memory,
then moves to `READY`. Rules from upstream:

- It's bounded by `MCPOptions.discovery_timeout` (15 s). On timeout or error
  it goes back to `CONNECTED` with the error, not to `FAILED`.
- A new `discover()` cancels the one in flight (upstream's
  `_discoveryAbortController`).
- Pages are followed until `next_cursor` runs out, up to the SDK's limit.

The catalog isn't persisted (upstream doesn't either): after a restore, the
lists are empty until rediscovery finishes. `wait_for_connections` covers
that.

---

## 4. The manager (`manager.py`)

- **The registry:** `_connections: dict[str, MCPClientConnection]`, exposed
  read-only as `connections`.
- **`add_server`**, in upstream's order:
  1. normalize or generate the id, check for conflicts, apply the idempotent
     re-add rules ([mcp_api.md](./mcp_api.md) §2.1);
  2. `is_blocked_url`;
  3. write the row, then build the connection;
  4. `init()`. If it ends `AUTHENTICATING`, store `auth_url` and
     `client_id` on the row, emit `mcp:client:authorize` and return.
     `FAILED` raises `MCPConnectionError`. `CONNECTED` runs `discover()` and
     returns `ready`.
- **Restore and `wait_for_connections`:** restore puts one task per
  restorable server in `_pending: dict[str, asyncio.Task]`. Each task is
  `connect_with_retry` then `discover`, using the shared `retry()` utility
  with the server's stored `RetryOptions` (upstream defaults: 3 attempts,
  0.5 s base, 5 s cap). `wait_for_connections(timeout=)` is
  `asyncio.wait(_pending.values(), timeout=...)`, and exceptions are already
  turned into states, so it never raises.
- **Keep-alive:** a background restore or post-callback connect holds a
  keep-alive lease (the `KeepAlive` capability, through Lifecycle services)
  until it settles, so the DO isn't evicted halfway through a handshake.
  Upstream relies on the pending promise. This is the one place the port
  adds something, because Python tasks don't keep a DO alive.
- **Catalog aggregation:** `list_tools(...)` and the others filter the
  registry (server id, name, state) and flatten each connection's lists into
  `ServerTool` and the like, in registration order.
- **Removing a server:** close the connection, delete the row, call
  `provider.clear()` for its OAuth keys, cancel its pending task, fire a
  state change.
- **Observability:** `mcp:client:preconnect`, `mcp:client:connect`,
  `mcp:client:authorize`, `mcp:client:discover` and `mcp:client:close`
  with upstream's payloads, through `self.lifecycle.events`. They reach
  `Agent._emit` and the existing `mcp:` → `mcp` channel
  ([observability.md](./observability.md)).

---

## 5. Storage (`storage.py`)

The table and the version key are exactly upstream's
(`storage.ts:24`, `index.ts:398`):

```sql
CREATE TABLE IF NOT EXISTS cf_agents_mcp_servers (
  id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, server_url TEXT NOT NULL,
  callback_url TEXT NOT NULL, client_id TEXT, auth_url TEXT, server_options TEXT)
-- KV: "cf_agents:mcp_schema_version" = 1
```

`server_options` is JSON in upstream's camelCase shape
(`PersistedMcpServerOptions`). Python writes the subset it uses and keeps
any other keys it reads, so a row written by a TypeScript agent survives a
Python rewrite:

```json
{"transport": {"type": "streamable-http", "headers": {...}, "protocolVersion": "2026-07-28"},
 "discoverResult": {...},
 "retry": {"maxAttempts": 3, "baseDelayMs": 500, "maxDelayMs": 5000}}
```

Retry delays are stored in milliseconds, following the SDK rule that
storage and the wire use milliseconds ([utilities.md](./utilities.md) §1).
Writes go through the typed SQL helper ([utilities.md](./utilities.md) §6).
They're cold paths, so the wrapped storage is fine
([platform_verification.md](./platform_verification.md) §6.2).

---

## 6. OAuth (`oauth.py`, `callback.py`)

### 6.1 The provider and its storage

`MCPOAuthProvider` is an abstract base. It implements the SDK's
`TokenStorage` protocol (`get_tokens`, `set_tokens`, `get_client_info`,
`set_client_info`) and adds what a callback that arrives in a later request
needs:

```python
class MCPOAuthProvider(ABC):
    server_id: str; redirect_url: str
    @property
    def client_metadata(self) -> OAuthClientMetadata: ...      # dynamic registration request
    async def get_tokens(self) -> OAuthToken | None: ...         # + set_tokens, get/set_client_info
    async def create_state(self) -> str: ...                     # "<nonce>.<server_id>", stored
    async def check_state(self, state: str) -> bool: ...         # non-destructive
    async def consume_state(self, state: str) -> None: ...
    async def save_code_verifier(self, state: str, verifier: str) -> None: ...
    async def code_verifier(self, state: str) -> str | None: ...
    async def clear(self) -> None: ...                           # every key for this server
```

`DurableObjectOAuthProvider` stores everything in DO KV, using **upstream's
key layout** (`/{client_name}/{server_id}/{client_id}/token`,
`.../client_info/`, `/{client_name}/{server_id}/state/{nonce}`, the
code-verifier keys). The values are the RFC 6749 / 7591 JSON shapes both
SDKs use. The client metadata is upstream's: `token_endpoint_auth_method:
"none"`, `grant_types: [authorization_code, refresh_token]`, and
`client_uri` set to the redirect URL's origin.

### 6.2 Splitting the authorization step across requests

The SDK's `OAuthClientProvider` (an `httpx2.Auth`) runs the whole flow
inside one request. It discovers metadata, registers, then calls
`redirect_handler(url)` **and awaits `callback_handler()` in the same
coroutine**, generating the state and PKCE verifier as local variables
(`oauth2.py:385`). On a DO, the callback is a different request, possibly
in a different isolate after the agent was evicted, so the flow can't wait
in memory.

**Proposed: a subclass that overrides `_perform_authorization_code_grant`**,
the one method that does the redirect and the wait:

- **On the first connect**, there's no pending code. It builds the
  authorization URL as the SDK does (endpoint, PKCE S256, `resource` per
  RFC 8707, `scope`, `prompt=consent` for `offline_access`), but with
  `state = provider.create_state()` and the verifier saved under that state.
  It then raises `AuthorizationRequired(auth_url)`. That unwinds out of
  `httpx2` and `Client.__aenter__` into `_run`, which moves to
  `AUTHENTICATING` with `auth_url`.
- **On the callback request**, `callback.py` checks that it's a GET on a
  stored `callback_url` (origin + path) whose `state` names a known server,
  as upstream's `isCallbackRequest` does. It then runs the two-phase state
  check (check now, consume once the exchange succeeds) and stores `(code,
  state)` on the connection. Finally it runs `init()` again, which hits the
  same 401, re-discovers, and calls the override again. This time the
  override returns `(code, verifier)` from the pending pair, after checking
  RFC 9207 `iss` as the SDK does. **The SDK then exchanges the code and
  saves the tokens through `TokenStorage`, and refreshes them later
  itself.**
- **Upstream's edge cases carry over:**
  - a callback for a connection that's already authenticated consumes its
    state and reports success (a stale popup);
  - a callback whose state can't be verified changes nothing (it can't fail
    someone else's connection);
  - a genuine callback can recover a connection that went `FAILED`.

**Risk:** `_perform_authorization_code_grant` is private SDK API. This is
why the extra caps `mcp<3`. A contract test runs the override against the
SDK's own auth server fixtures on every `mcp` version CI installs, so a
signature change fails CI rather than production. The alternative was
writing our own `httpx2.Auth` for metadata discovery, registration, the
exchange and refresh. That's about 600 lines of security-sensitive code the
SDK already maintains, so it's rejected unless the override breaks.

### 6.3 The callback response

`on_request` returns, in order: the hook's response when the hook returns
one, otherwise a redirect to `oauth_success_redirect` /
`oauth_error_redirect` (adding `?error=`) resolved against the request
origin, otherwise a redirect to the origin. The connection then continues
in a background task, as upstream does
(`void this.establishConnection(...)`), under a keep-alive lease.

---

## 7. Server (`server/`)

For each request:

```
McpHandler(request, env, ctx)
  ├─ path != route                 → 404
  ├─ OPTIONS                       → CORS preflight
  ├─ Host / Origin not allowed     → 403 (upstream's hostHeaderValidationResponse / originValidationResponse)
  ├─ set the auth ContextVar
  ├─ server = factory() if a factory was given, else the shared instance; take its low-level Server
  ├─ manager = StreamableHTTPSessionManager(lowlevel_server, stateless=True, json_response=...)
  ├─ async with manager.run():     # new manager per request: run() raises if called twice
  │     response = await asgi.serve(manager.handle_request, request)
  └─ add CORS headers → Response
```

- **A new session manager for every request.** `run()` can only be entered
  once per instance (`streamable_http_manager.py`, `_has_started`), and
  stateless mode needs its task group. Building one is cheap: it holds no
  sessions.
- **`asgi.py`** is a small request ⇄ ASGI bridge: scope from the Workers
  `Request`, `receive` reading the body, and `send` collecting the
  status, headers and body, or a streamed body for SSE responses. The
  Workers SDK's `workers.asgi.fetch` drives an app's lifespan on every
  request, and `MCPServer.streamable_http_app()` keeps one session manager
  across lifespans, so it's used for reference only. Whether its internals
  can be reused is an implementation detail checked in step 1 (§10).
- **Checking the server** happens before serving: a server with the SDK's
  auth server settings raises `MCPServerConfigError`, and subscriptions are
  turned off ([mcp_api.md](./mcp_api.md) §4.1).
- **Factories are called once per request**, before the session manager is
  built. An exception from the factory is a 500 that goes through the
  handler's error reporting (upstream's `handler-errors.ts`).
- **Tools run in the request's task.** When the handler is called from an
  agent's `on_request`, `get_current_agent()` is set inside them.

---

## 8. Chat

`AIChatAgentOptions.wait_for_mcp_connections: bool | Duration = False`
(upstream's `waitForMcpConnections: boolean | { timeout }`). Before
`on_chat_message`, a turn calls `self.mcp.wait_for_connections(timeout=...)`
when it's set and MCP is on. `True` means no timeout.

---

## 9. Packaging and tests

- **The import guard:** `agents/mcp/__init__.py` imports `mcp` first, and
  turns `ModuleNotFoundError` into an `ImportError` that names
  `cf-agents[mcp]` and Python 3.14 Workers.
- **The import boundary:** a test runs `import agents` and
  `import agents.chat` in a subprocess and asserts that none of `mcp`,
  `mcp_types`, `pydantic` or `httpx2` is in `sys.modules`. A second test
  does the same for an `Agent` without `options.mcp`.
- **Unit tests run on CPython with no network:**
  - **In-process:** `connection.py` takes a client factory, and tests pass
    `mcp.Client(MCPServer(...))`, which the SDK connects in-process. That
    covers the state machine, discovery, list-changed handling, the
    catalog, `call_tool` and restore.
  - **Over HTTP:** an `httpx2.ASGITransport` against
    `MCPServer.streamable_http_app()` covers both protocol eras, header
    auth, the `auto` transport fallback and the OAuth flow, using the SDK's
    `mcp.server.auth` provider fixtures.
  - **Storage, the frame, broadcasts and callback routing** use the existing
    fakes in `tests/fake_runtime.py`.
  - **Server:** `create_mcp_handler` driven with fake Workers `Request`
    objects, and a `mcp.Client` talking to it through an `httpx2`
    transport that calls the handler.
- **Contract tests:** the OAuth override (§6.2), and the persisted JSON
  against a fixture copied from upstream's storage tests.
- **On Workers:** the platform checks in [mcp.md](./mcp.md) §5.3, plus one
  round trip through each path (add a server, call a tool, OAuth with a
  local provider, `create_mcp_handler`) in the verification Worker.

---

## 10. Build order

| Step | Contents | Depends on |
| --- | --- | --- |
| 0 | Packaging and the import guard and boundary test; `MCPOptions` / `MCPNotEnabledError`; the dynamic frame with an empty state ([mcp.md](./mcp.md) §5.1–5.2) | — |
| 1 | Platform checks: `httpx2` in `workerd` (requests, SSE, cancellation), deploy size and cold start, the ASGI bridge (`mcp.md` §5.3) | 0 |
| 2 | `storage.py`, `urls.py`, `types.py`, `errors.py` | 0 |
| 3 | `connection.py` and `manager.py` without OAuth: add, remove, restore, wait, discovery, catalog, calls, events; the `Agent` members and the broadcast | 2 |
| 4 | OAuth: `oauth.py`, `callback.py`, the two hooks | 3 |
| 5 | `server/`: `create_mcp_handler` (server or factory), auth context | 0, 1 |
| 6 | Chat: `wait_for_mcp_connections` | 3 |
| 7 | User docs and an example (`examples/mcp-client`, `examples/mcp-server`) on a 3.14 Worker | 3–5 |

Steps 3 and 5 are independent, so the server can ship before the client's
OAuth.

---

## 11. Open

- **SSE-only servers (`transport="sse"`):** whether they're worth
  supporting in the first pass, given that the GET event stream's lifetime
  inside a DO hasn't been checked on Workers.
- **Whether an `httpx2` transport that aborts on cancellation becomes
  required** (depends on step 1).
- **MCP on facets:** the default callback URL for a sub-agent would need its
  `/sub/` path.
- **Elicitation handlers** (upstream's `configureElicitationHandlers`), as
  an `on_mcp_elicitation` hook later.
