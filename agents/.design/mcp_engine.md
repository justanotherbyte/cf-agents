# MCP: internals (decided; built)

How the API in [mcp_api.md](./mcp_api.md) is built. The client is our own
code and has no runtime dependency beyond cf-agents, so it runs on 3.12,
3.13 and 3.14 Workers. Server hosting stays on the `mcp` SDK behind the
`cf-agents[mcp-server]` extra (3.14 Workers only). Why: [mcp.md](./mcp.md)
§5.3.1 measured `mcp` at 16 to 20 MiB per Worker, most of it native code
the client never uses.

Upstream: `mcp/client/index.ts` (`MCPClientManager`), `connection.ts`,
`storage.ts`, `do-oauth-client-provider.ts`, `server/handler-stateless.ts`,
`server/auth-context.ts`, and the MCP wiring in `src/index.ts`. The wire
facts below were taken from the `mcp` 2.3.0 Python SDK (`mcp/`,
`mcp_types/`), which the client ports. Line numbers refer to those trees.

---

## 1. Layout

```
src/agents/
├── _ffi.py               # + open_fetch, FetchResponse
├── agent/
│   ├── types.py          # + MCPOptions
│   ├── errors.py         # + MCPNotEnabledError
│   └── agent.py          # + install on options.mcp, add/remove/get_mcp_servers,
│                         #   the two hooks, the dynamic cf_agent_mcp_servers frame
├── chat/types.py         # + AIChatAgentOptions.wait_for_mcp_connections
└── mcp/                  # the client; never imports mcp
    ├── __init__.py       # re-exports, static __all__; doesn't import server
    ├── errors.py         # MCPError and subclasses
    ├── types.py          # enums, records, TypedDict payloads (stdlib only)
    ├── models.py         # protocol dataclasses with wire() names, and their decoding
    ├── urls.py           # normalize_server_id, is_blocked_url
    ├── sse.py            # incremental SSE decoder
    ├── http.py           # HttpTransport protocol, FetchTransport (choke point 1)
    ├── session.py        # McpSession: JSON-RPC over Streamable HTTP, both eras (choke point 2)
    ├── oauth.py          # MCPOAuthProvider, DurableObjectOAuthProvider
    ├── oauth_flow.py     # OAuthFlow: discovery, registration, PKCE, exchange, refresh
    ├── storage.py        # McpServerStore: cf_agents_mcp_servers and the server_options codec
    ├── connection.py     # MCPClientConnection: state machine, catalog, discovery, listener
    ├── manager.py        # MCPClientManager: the capability and facade, and the OAuth
    │                     #   callback (it owns the connections and flows a callback touches)
    └── server/           # needs cf-agents[mcp-server]
        ├── __init__.py   # import guard naming the extra
        ├── handler.py    # create_mcp_handler, McpHandler, route/CORS/Host/Origin checks
        ├── asgi.py       # the request ⇄ ASGI bridge for one request
        └── auth_context.py  # McpAuthContext, the ContextVar, get_mcp_auth_context
```

- **Layers**, lowest first: `http.py` (transport), `session.py` and
  `oauth_flow.py` (protocol), `connection.py`, then `manager.py` (facade).
  `storage.py` and `oauth.py` hold persisted state.
- **Where `mcp/` sits:** at the same level as `chat/`, above `agent`.
  `agents.mcp` may import `agent.types` but not `agent.agent`. Like
  upstream, `MCPClientManager` depends only on Lifecycle services, so it
  works on plain DOs.
- **The one upward import:** `Agent.__init__` imports `agents.mcp.manager`
  inside the `if options.mcp:` branch.
- **Not ported:** `rpc.ts`, `x402.ts`, `transports.ts`, `catalog.ts` /
  `invoker.ts` (AI SDK tool conversion), `McpAgent` and the legacy server
  files.

---

## 2. How it's installed and started

```
Agent.__init__
  └─ options.mcp set?
       ├─ no  → self._mcp = None (the frame stays empty; self.mcp raises MCPNotEnabledError)
       └─ yes → self._mcp = self.use(MCPClientManager(
                    client_name=options.mcp.client_name or type(self).__name__,
                    create_oauth_provider=self.create_mcp_oauth_provider,
                    on_oauth_callback=self.on_mcp_oauth_callback,
                    keep_alive=self._keep_alive.acquire, ...))     # after _fibers
                self._mcp.on_server_state_changed(self._on_mcp_state_changed)

MCPClientManager.on_start            (Lifecycle startup, before the agent's on_start)
  ├─ "cf_agents:mcp_schema_version" < 1 → CREATE TABLE IF NOT EXISTS, set to 1
  ├─ restore (§6): a connection for every row; rows with auth_url go to AUTHENTICATING;
  │  the rest start connecting in the background
  └─ fire on_server_state_changed
```

- **Broadcasting waits until the agent has started**, as upstream's
  `mcpBroadcastReady` does (`index.ts:2046,2410`). `_on_mcp_state_changed`
  does nothing until the agent's startup finishes, then sends one
  broadcast.
- **The connect frame** is `self._mcp_servers_frame()`: the empty frame when
  `_mcp is None`, otherwise `servers_state()` serialized. Broadcasts go to
  protocol-enabled connections only, like state broadcasts.
- **The callback route** is the manager's `on_request`. It's a selective
  capability, so Lifecycle runs it before `Agent.on_request`.

---

## 3. Transport (`http.py`, `_ffi.py`)

```python
@dataclass(slots=True, kw_only=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: str | None = None

class HttpResponse(Protocol):
    status: int
    headers: Mapping[str, str]          # lower-case names
    def chunks(self) -> AsyncIterator[bytes]: ...

class HttpTransport(Protocol):
    def request(self, request: HttpRequest) -> AbstractAsyncContextManager[HttpResponse]: ...
```

`FetchTransport` is the default. It wraps `_ffi.open_fetch` and turns a
`JsException` (network, DNS, TLS, or a dropped body) into
`MCPTransportError`.

`_ffi.open_fetch(method, url, *, headers, body)` is an async context
manager:

- it calls `js.fetch(url, init)` with `{method, headers, body, signal,
  redirect: "manual"}` and an `AbortController`;
- a `CancelledError` while waiting for headers aborts the controller;
- it yields a `FetchResponse` with `status`, lower-cased `headers` and
  `chunks()`, a `reader.read()` loop;
- its `finally` calls `reader.cancel()` and `controller.abort()`.

This is the only combination that aborts on cancellation
([platform_verification.md](./platform_verification.md) §2.8). Tests inject
a transport instead: `tests/mcp/_asgi_transport.py`'s `AsgiTransport` runs
an ASGI app in-process.

---

## 4. Session (`session.py`)

`McpSession(url, *, http, client_info, headers, auth, on_notification)` is
one negotiated conversation with one server. It has no background task:
every request is one POST whose response is read as it arrives.

### 4.1 The choke point

`request(method, params, *, timeout=60)` does what every call needs, once:

1. Build the envelope with an id from a per-session counter starting at 1.
2. Stamp it for the era (§4.3).
3. Add the stored headers, `mcp-session-id`, and `Authorization` from
   `auth.authorization()`.
4. On 401, refresh once, then authorize (§7).
5. Follow same-origin 307/308 redirects, at most 3. Any other redirect is
   -32600 naming `Location`.
6. Read a JSON or SSE response (§4.4) and dispatch in-stream
   notifications.
7. Answer server-initiated requests with -32601 (handshake era only).
8. Map HTTP status to JSON-RPC errors, and `TimeoutError` to
   `MCPRpcError(-32001)`.
9. On a 404 while holding a session id, re-`initialize` once and retry.

The endpoint methods (`list_page`, `call_tool`, `get_prompt`,
`read_resource`) are each a few lines that call `request()`. A reply's
`error` raises `MCPRpcError(code, message, data)`. A result with
`resultType: "input_required"` raises `MCPProtocolError` (elicitation is
deferred).

### 4.2 Constants

| Fact | Value | Source |
| --- | --- | --- |
| Handshake versions | `2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25` (latest) | `mcp_types/version.py:33-41` |
| Modern versions | `2026-07-28` | `version.py:43` |
| `_meta` keys | `io.modelcontextprotocol/protocolVersion`, `/clientInfo`, `/clientCapabilities` | `mcp_types/_types.py:53-72` |
| Headers | `mcp-protocol-version`, `mcp-method`, `mcp-name`, `Mcp-Param-<token>`, `mcp-session-id` | `mcp/shared/inbound.py:60-67,234`; `client/streamable_http.py:55-56` |
| `Mcp-Name` | `tools/call` and `prompts/get` → `name`, `resources/read` → `uri`; `=?base64?…?=` unless printable ASCII | `inbound.py:72-78,149` |
| `Mcp-Param-*` | one per `x-mcp-header` property in the tool's input schema; bools as `true`/`false`; the server rejects a call without them | `inbound.py:237-290,526-570` |
| Request headers | `accept: application/json, text/event-stream`, `content-type: application/json` | `streamable_http.py:139-157` |
| Error codes | -32020 header mismatch, -32022 unsupported version, -32000 connection closed, -32001 timeout, -32700, -32600, -32601, -32602, -32603 | `mcp_types/jsonrpc.py:73-106` |
| Timeouts | discover 10 s (`client/session.py:68`), requests 60 s | |
| SSE limits | 1 MiB per event; GET reconnects twice, 1000 ms default delay | `streamable_http.py:59-61` |

### 4.3 Era negotiation (`connect(prior=...)`)

A port of `client/_probe.py:52-114`:

1. **Restore:** if `prior` has a modern `protocol_version` and a
   `discover_result`, adopt them. No network request.
2. **Probe:** send `server/discover` at 2026-07-28, with `_meta
   {protocolVersion, clientInfo, clientCapabilities: {}}` and the
   `mcp-protocol-version` and `mcp-method` headers.
   - A valid result that lists a modern version: adopt it.
   - An unparseable result, or no modern version: initialize.
   - -32022 naming a mutual modern version, first attempt: probe again at
     that version.
   - -32022 whose `supported` list has no handshake version: raise.
   - Any other `MCPRpcError` (a 404 becomes -32601): initialize.
   - Network errors propagate. A 401 goes to OAuth and is never an era
     verdict.
3. **Initialize:** send `initialize {protocolVersion: "2025-11-25",
   capabilities: {}, clientInfo}` with no `mcp-protocol-version` header.
   Keep `mcp-session-id` from the response. The `protocolVersion` must be
   a handshake version, else `MCPProtocolError`. Send
   `notifications/initialized` and expect 202. A -32022 from `initialize`
   on the first attempt re-probes at the mutual version.

After negotiation, the handshake era sends `mcp-protocol-version` and
`mcp-session-id` on every request. The modern era merges the `_meta`
envelope into `params` and sends `mcp-protocol-version`, `mcp-method`,
`mcp-name` and `Mcp-Param-*`, which the server checks exactly
(`inbound.py:401-500`).

### 4.4 Reading responses

A port of `streamable_http.py:174-205,363-427`:

| Response | Result |
| --- | --- |
| 202 to a request | -32600 |
| 401 | OAuth (§7) |
| ≥400 with a JSON-RPC error body | that error |
| 404 with a session id | session expired: re-initialize and retry once |
| 404 without one | -32601 "Not Found" (drives the era fallback) |
| other ≥400 | -32603 |
| `application/json` | one message |
| `text/event-stream` | `message` events; empty data is a priming event; the response with our id ends the read and cancels the reader; notifications go to `on_notification`; stream end without a response is -32000 |
| other content type | -32600 |

### 4.5 The listener (`listen()`)

The connection runs it as one task after READY. It holds no keep-alive
lease and is cancelled on close.

- **Handshake era, with a session id:** a GET with `accept:
  text/event-stream`. 405 means the server offers no stream. It
  reconnects as `streamable_http.py:224-262` does.
- **Modern era, only when a capability has `listChanged`:** a
  `subscriptions/listen` request whose `params.notifications` asks for
  `toolsListChanged`, `promptsListChanged`, `resourcesListChanged`. The
  first frame is `notifications/subscriptions/acknowledged`
  (`_types.py:1024-1095`, `shared/subscriptions.py:36,79`).
- **`close()`** sends DELETE in the handshake era; 405 counts as done.

---

## 5. Connection (`connection.py`)

The state machine is upstream's (`connection.ts:53`):

```
no OAuth:  CONNECTING → CONNECTED → DISCOVERING → READY
OAuth:     AUTHENTICATING → (callback) → CONNECTING → CONNECTED → DISCOVERING → READY
any state → FAILED (error kept on the connection, sanitized in the frame)
```

- **`init()`** builds a new `McpSession` and calls `connect(prior=...)`.
  `MCPAuthorizationRequiredError` moves to AUTHENTICATING and calls
  `on_authorize(auth_url, client_id)`. Any other `MCPError` moves to FAILED
  and drops `prior`, so a retry negotiates afresh. It emits
  `mcp:client:connect`. A 404 or 405 on the first POST fails with "legacy
  SSE servers aren't supported yet".
- **`discover(timeout=)`**, a port of `connection.ts:527`: it cancels a
  discovery in flight, moves to DISCOVERING, and inside
  `asyncio.timeout()` gathers `tools/list`, `prompts/list`,
  `resources/list` and `resources/templates/list` for the capabilities the
  server advertises. Each list follows `nextCursor` and stops on a repeated
  cursor. A -32601 from one list leaves it empty and emits `discover` with
  the capability (`connection.ts:980`). Success moves to READY and starts
  the listener. An error or timeout returns to CONNECTED with the error.
- **The catalog** is kept as the server's raw dicts, so the frame echoes
  them exactly. `list_tools()` and the others parse them into dataclasses.
- **Notifications:** `notifications/{tools,prompts,resources}/list_changed`
  re-fetches that one list in a background task, then fires a state
  change. Tasks are kept in a set and cancelled on close.
- **Calls** that get `MCPAuthorizationRequiredError` move to
  AUTHENTICATING, call `on_authorize`, and re-raise.
- **`handle_callback(callback)`** holds the OAuth edge cases (§7.2).

---

## 6. Manager (`manager.py`) and storage (`storage.py`)

### 6.1 The manager

- **The registry:** `_connections: dict[str, MCPClientConnection]`, exposed
  read-only as `connections`.
- **`add_server`**, in upstream's order: normalize or generate the id,
  check conflicts and the idempotent re-add rules ([mcp_api.md](./mcp_api.md)
  §2.1), `is_blocked_url`, write the row, build the connection, `init()`.
  AUTHENTICATING stores `auth_url` and `client_id` on the row, emits
  `mcp:client:authorize` and returns. FAILED raises `MCPConnectionError`.
  CONNECTED runs `discover()` and returns `ready`.
- **Restore and `wait_for_connections`:** restore puts one task per
  restorable server in `_pending`. Each runs `run_with_retries` (defaults:
  3 attempts, 0.5 s base, 5 s cap, `index.ts:1140`; retries only on
  FAILED) then `discover`, under a keep-alive lease. A modern-era server
  restores with no network request. `wait_for_connections(timeout=)` is
  `asyncio.wait(...)`; failures are already states, so it never raises.
- **Catalog aggregation:** `list_tools(...)` and the others filter by
  server id, name and state (combined with AND) and flatten in registration
  order into `ServerTool` and the like.
- **Removing a server:** close the connection, delete the row, call
  `provider.clear()`, cancel its pending task, fire a state change.
- **`servers_state()`** gives a row without a connection `not-connected`,
  or `authenticating` when it has an `auth_url`. Errors are sanitized: C0
  controls other than `\t\n\r`, and DEL, are stripped, and the text is cut
  at 500 characters plus `...` (`index.ts:785-807,10051`).
- **Observability:** upstream's payloads (`observability/mcp.ts:7-38`)
  through `lifecycle.emit`: `mcp:client:preconnect {serverId}`, `connect
  {url, transport, state, error?}`, `authorize {serverId, authUrl,
  clientId?}`, `discover {url?, state?, error?, capability?}`, `close {url,
  transport?, state, error?}`.

### 6.2 Storage

The table and version key are exactly upstream's (`storage.ts:24`,
`index.ts:398`):

```sql
CREATE TABLE IF NOT EXISTS cf_agents_mcp_servers (
  id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, server_url TEXT NOT NULL,
  callback_url TEXT NOT NULL, client_id TEXT, auth_url TEXT, server_options TEXT)
-- KV: "cf_agents:mcp_schema_version" = 1
```

`server_options` is upstream's camelCase JSON. Python writes the subset it
uses and keeps any other keys it reads, so a row written by a TypeScript
agent survives a Python rewrite:

```json
{"transport": {"type": "auto", "headers": {}, "sessionId": "<only if the server sent one>",
               "protocolVersion": "2025-11-25 | 2026-07-28"},
 "discoverResult": {"...": "modern era only"},
 "retry": {"maxAttempts": 3, "baseDelayMs": 500, "maxDelayMs": 5000}}
```

- `retry` is written only when the caller passed it; delays are in
  milliseconds ([utilities.md](./utilities.md) §1).
- `update_session` ports `withMcpSession` (`storage.ts:170`); decoding
  applies upstream's drop rule (`storage.ts:141`).
- Python keeps `protocolVersion` and `discoverResult` for a modern server
  even without a `sessionId`. TypeScript only resumes when `sessionId` is
  present, so it negotiates afresh on such a row.
- A handshake-era row restores with a new `initialize` (one round trip per
  wake). Resuming the stored `sessionId` is deferred.

---

## 7. OAuth (`oauth.py`, `oauth_flow.py`, `callback.py`)

### 7.1 The provider and the flow

`MCPOAuthProvider` is an abstract base holding `server_id`, `client_id`,
`redirect_url` and `client_metadata`. Its async methods: `tokens`,
`save_tokens`, `clear_tokens`, `client_information`,
`save_client_information`, `clear_client_information`, `discovery_state`,
`save_discovery_state`, `create_state`, `check_state`, `consume_state`,
`save_code_verifier`, `code_verifier`, `delete_code_verifier`, `clear`.

`DurableObjectOAuthProvider(storage, client_name, redirect_url)` uses
upstream's DO KV layout (`do-oauth-client-provider.ts`):

| Key | Value |
| --- | --- |
| `/{c}/{s}/{clientId}/token` | RFC 6749 token JSON |
| `/{c}/{s}/{clientId}/client_info/` | RFC 7591 client JSON, plus `issuer` |
| `/{c}/{s}/state/{nonce}` | `{nonce, serverId, createdAt}`, valid 10 minutes |
| `/{c}/{s}/{clientId}/code_verifier/{nonce}` | `{verifier, createdAt}` |
| `/{c}/{s}/oauth_discovery` | `{authorizationServerUrl, resourceMetadataUrl?, resourceMetadata?, authorizationServerMetadata?, resource}` |

`clear()` lists `/{c}/{s}/` and deletes in chunks of 128. The client
metadata is upstream's (`do-oauth-client-provider.ts:108`):
`token_endpoint_auth_method: "none"`, `grant_types: [authorization_code,
refresh_token]`, `response_types: [code]`, `client_uri` set to the redirect
URL's origin.

`OAuthFlow(provider, *, server_url, http)` ports the SDK's `oauth2.py` and
`auth/utils.py` steps. The SDK runs the redirect and the callback in one
coroutine. A DO can't wait in memory for a callback that arrives in a later
request, possibly in a new isolate, so the flow is split the way upstream
TypeScript splits it.

### 7.2 Request A: a 401 on connect or on a call

Handling a 401 holds a per-server lock. Discovery sends its four lists at
once, so four 401s arrive together, and refresh tokens are single-use: a
request that waited on the lock retries with the tokens the first one
refreshed instead of refreshing again. Without the lock, the losing
refreshes fail and clear the new tokens (found by the contract tests).

1. **Refresh**, when the tokens have a `refresh_token` and client info
   exists: POST `grant_type=refresh_token` with `client_id` and `resource`.
   200 saves the tokens (carrying forward `scope` and `refresh_token`,
   `oauth2.py:533-560`) and the session retries once. Anything else clears
   the tokens and continues.
2. **Discover** the protected resource metadata (RFC 9728) from
   `WWW-Authenticate`'s `resource_metadata`, then the path-aware and root
   well-known URLs (`auth/utils.py:64-98`). Its `resource` must be allowed
   for the server URL. With none found, the issuer is the server's origin.
   Then the authorization server metadata (RFC 8414 and OIDC URLs,
   `utils.py:159-195`), whose issuer must match. Stored client info from a
   different issuer is dropped with its tokens (`utils.py:340`).
3. **Scope:** `WWW-Authenticate`'s, else PRM's, else ASM's
   `scopes_supported`, plus `offline_access` when the server supports it
   (`utils.py:101-134`).
4. **Register** (RFC 7591) when there's no client info, at
   `registration_endpoint` or `{origin}/register`.
5. **PKCE S256:** a 128-character verifier from `secrets`, its unpadded
   base64url SHA-256 as the challenge. `state` is
   `"{token_urlsafe(16)}.{server_id}"`.
6. **The authorization URL** at `authorization_endpoint` or
   `{origin}/authorize`, with `response_type=code`, `client_id`,
   `redirect_uri`, `state`, `code_challenge`, `code_challenge_method=S256`,
   `scope`, `prompt=consent` with `offline_access`, and `resource` (RFC 8707)
   when a PRM was found or the version is at least 2025-06-18
   (`oauth2.py:203-233,385-451`).
7. **Save the discovery state** and raise
   `MCPAuthorizationRequiredError(auth_url)`. The connection moves to
   AUTHENTICATING; the manager stores `auth_url` and `client_id` on the
   row and emits `mcp:client:authorize`.

Without a callback URL there's no provider, and a 401 is FAILED.

### 7.3 Request B: the callback

Startup has restored the row as AUTHENTICATING with its provider.

1. **Match** (`index.ts:1591`): a GET whose origin and path equal a stored
   `callback_url` and whose `state` splits into two parts on `.`.
   Anything else isn't claimed and falls through to the agent.
2. **Validate** (`index.ts:1619`): state, server id, a code or an error, a
   known server with a connection.
3. **Edge cases** (`index.ts:1684-1810`), in `connection.handle_callback`:

   | Case | Handling |
   | --- | --- |
   | invalid, connection already accepts auth | consume the stale state; success (a stale popup) |
   | invalid, state not redeemable | ignore it (it can't fail someone else's connection) |
   | invalid, otherwise | clear `auth_url`, FAILED |
   | valid, connection already accepts auth | consume; success |
   | neither AUTHENTICATING nor FAILED | FAILED |
   | FAILED with a genuine callback | recover: continue below |
   | `error` parameter | `MCPOAuthError`, FAILED |

   "Accepts auth" means READY, CONNECTED, CONNECTING or DISCOVERING
   (`index.ts:818`).
4. **Exchange:** CONNECTING, `consume_state`, then check RFC 9207 `iss`
   against the stored issuer (`utils.py:239`), POST
   `grant_type=authorization_code` with `code`, `redirect_uri`,
   `client_id`, `code_verifier` and `resource`, save the tokens and delete
   the discovery state. The verifier is deleted in a `finally`.
5. **Connect:** the manager clears `auth_url`, fires a state change, and
   runs connect-with-retry and discover in a tracked task under a
   keep-alive lease.
6. **Respond** (`index.ts:478`): the hook's `Response` if it returns one,
   else a redirect to `oauth_success_redirect`, or `oauth_error_redirect`
   with `?error=`, resolved against the request origin, else a redirect to
   the origin.

---

## 8. Server (`server/`)

For each request:

```
McpHandler(request, env, ctx)
  ├─ path != route                 → 404
  ├─ Host / Origin not allowed     → 403 (JSON-RPC -32000; checked before the preflight, as upstream)
  ├─ OPTIONS                       → CORS preflight
  ├─ set the auth ContextVar
  ├─ server = factory() if a factory was given, else the shared instance; take its low-level Server
  ├─ manager = StreamableHTTPSessionManager(lowlevel_server, stateless=True, json_response=...)
  ├─ async with manager.run():     # new manager per request: run() raises if called twice
  │     response = await asgi.serve(manager.handle_request, request)
  └─ add CORS headers → Response
```

- **A new session manager for every request.** `run()` can only be entered
  once per instance, and stateless mode needs its task group.
- **`asgi.py`** builds the scope from the Workers `Request`, feeds the
  body to `receive`, and collects `send` into a response, streamed for SSE.
- **Checking the server:** a server with the SDK's auth server settings
  (`MCPServer.settings.auth`) raises `MCPServerConfigError`.
- **Subscriptions are off.** The SDK has no public switch after
  construction, so the handler removes `subscriptions/listen`,
  `resources/subscribe` and `resources/unsubscribe` from the server's
  private `Server._request_handlers`, which also keeps the advertised
  capabilities honest. This changes the user's server object, and relies on
  private SDK API; the `mcp<3` cap covers it.
- **Lifespan** runs once per request, because `run()` enters it.
- **Factories are called once per request.** An exception from one is a
  500.
- **Tools run in the request's task**, so `get_current_agent()` works in
  them when the handler is called from an agent's `on_request`.

---

## 9. Chat

`AIChatAgentOptions.wait_for_mcp_connections: bool | Duration = False`.
In `_consume`, after the turn is registered and before `on_chat_message`,
a turn awaits `self._mcp.wait_for_connections(timeout=...)` when the
option is set and MCP is on. `True` means no timeout. A stop request
cancels the wait.

---

## 10. Packaging and tests

- **Packaging:** core dependencies don't change. `mcp-server =
  ["mcp>=2.3,<3"]` is the extra; `mcp>=2.3,<3` joins the dev group for
  contract tests.
- **The import boundary:** a subprocess imports `agents`, `agents.chat`
  and `agents.mcp` and starts an agent with `mcp=MCPOptions()`. None of
  `mcp`, `mcp_types`, `pydantic` or `httpx2` may load.
  `agents.mcp.server` without `mcp` raises an `ImportError` naming the
  extra.
- **Contract tests** run the client against the SDK's `MCPServer`
  (`streamable_http_app()`, both eras, JSON and SSE) through
  `AsgiTransport`, and the OAuth flow against the SDK's auth server
  (`mcp.server.auth`) with an in-memory provider in `conftest.py`.
- **Unit tests** cover `urls`, `sse`, `models`, `storage`, negotiation
  through a scripted transport, the OAuth provider's key layout, the
  callback edge cases, the manager, the agent members and the chat wait,
  using `tests/fake_runtime.py`.
- **On Workers:** the verification Worker checks `open_fetch` (cancel
  before headers, cancel mid-SSE, `redirect: "manual"`), and one round
  trip per path.

---

## 11. Build order

All phases are built (2026-10-26) and pass on CPython: the client's tests
run against the SDK's `MCPServer` and its OAuth authorization server. What
hasn't run is anything on `workerd` (§13).

| Phase | Work | Done when |
| --- | --- | --- |
| 0 | pyproject; `MCPOptions`, `MCPNotEnabledError`; `mcp/__init__.py`, `errors.py`; the dynamic frame (empty) | existing tests, the boundary test, ruff and ty pass |
| 1 | `urls`, `sse`, `codec`, `models`, `types`, `storage` | unit tests pass |
| 2 | `http.py`, `_ffi.open_fetch`, `AsgiTransport` | a round trip against the SDK app |
| 3 | session: modern era | contract tests pass |
| 4 | session: handshake era and negotiation | contract and scripted tests pass |
| 5 | listener | list-changed tests pass in both eras |
| 6 | connection and manager without OAuth | manager tests pass |
| 7 | Agent integration | agent tests pass |
| 8 | OAuth provider, flow, callback, hooks | OAuth contract and callback tests pass |
| 9 | chat wait | chat tests pass |
| 10 | server | handler tests pass |

---

## 12. Deferred

- The legacy SSE transport (`transport="sse"`). A stored `"sse"` row ends
  up FAILED.
- Resuming a handshake-era `Mcp-Session-Id` on restore.
- 403 `insufficient_scope` step-up, CIMD client ids, refreshing tokens
  before they expire (they're refreshed on a 401, as the TypeScript SDK
  does).
- Elicitation and multi-round-trip requests (`input_required` raises),
  `notifications/cancelled`, SSE resumption with `Last-Event-ID`.
- **MCP on facets:** the default callback URL would need the facet's
  `/sub/` path, so facets must pass `callback_path` for now.
- **Cancelling a discovery in flight** when another starts (upstream's
  `_discoveryAbortController`). Concurrent discoveries both run.
- **Moving a server to a new id** (upstream's `migrateServerId`). Adding a
  server with an `id` that's already stored under a random id creates a
  second row.

## 13. Open

- **Nothing has run on `workerd` yet.** The verification Worker still has to
  check `_ffi.open_fetch` (cancelling before headers and mid-SSE aborts the
  request, `redirect: "manual"` returns the 3xx), `kv_keys`, and one round
  trip per path: add a server, call a tool, OAuth, `create_mcp_handler`.
- **Does a long-lived listener fetch keep the DO active, and so billed?**
  Measure with the `open_fetch` probe. If it does, open the listener only
  after a list changes during a call.
