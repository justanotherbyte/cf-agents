# MCP: public API (proposed)

The Python API for MCP clients on agents and for hosting MCP servers on
Workers. Upstream: `../agents/packages/agents/src/mcp/` and the MCP members
of `src/index.ts` (v0.28.1). The internals are in [mcp_engine.md](./mcp_engine.md).
Dependencies, Workers support and version policy are in [mcp.md](./mcp.md).

Everything here follows the existing conventions: keyword-only options, `async
def` hooks, durations as `timedelta | float` seconds, dataclass records
([utilities.md](./utilities.md) §1, §5), and `AgentOptions` for per-class
settings ([agent_api.md](./agent_api.md) §1.7).

---

## 1. Turning MCP on

```python
from agents import Agent, AgentOptions, MCPOptions


class MyAgent(Agent):
    options = AgentOptions(mcp=MCPOptions())
```

- **`AgentOptions.mcp: MCPOptions | None = None`.** `None` (the default)
  means the MCP capability isn't installed and `mcp` is never imported.
  `AIChatAgentOptions` inherits the field.
- **Why opt-in rather than upstream's "always installed":** `mcp` is an
  optional dependency that costs at least 0.5 s to import, so an agent that
  doesn't use it shouldn't pay for it. It has to be a class-level setting
  rather than "install on first `add_mcp_server`": an agent that wakes with
  servers in `cf_agents_mcp_servers` must restore them during startup, and
  capabilities can't be added after startup ([agent_api.md](./agent_api.md)
  §2.3). This closes [mcp.md](./mcp.md) §4.4 in favour of option 2.
- **`MCPOptions` lives in `agents.agent.types`** and imports nothing from
  `mcp`. That's why `from agents import MCPOptions` is free.

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class MCPOptions:
    client_name: str | None = None         # sent in the MCP handshake; default: the agent class name
    client_version: str = __version__      # default: the cf-agents version
    discovery_timeout: Duration = 15       # per-server list_* round after connecting
    oauth_success_redirect: str | None = None
    oauth_error_redirect: str | None = None  # gets ?error=<message>
```

The two redirects are upstream's `configureOAuthCallback({ successRedirect,
errorRedirect })` as static options. Upstream's `customHandler` becomes the
`on_mcp_oauth_callback` hook (§2.4).

---

## 2. Client: `Agent` members

### 2.1 Adding and removing servers

```python
class MyAgent(Agent):
    options = AgentOptions(mcp=MCPOptions())

    @callable
    async def connect_github(self) -> str | None:
        result = await self.add_mcp_server("GitHub", "https://api.githubcopilot.com/mcp/")
        return result.auth_url  # None once the server is ready

    @callable
    async def disconnect(self, server_id: str) -> None:
        await self.remove_mcp_server(server_id)
```

```python
async def add_mcp_server(
    self,
    name: str,
    url: str,
    *,
    id: str | None = None,
    headers: Mapping[str, str] | None = None,
    transport: Literal["auto", "streamable-http", "sse"] = "auto",
    callback_host: str | None = None,
    callback_path: str | None = None,
    agents_prefix: str = "agents",
    retry: RetryOptions | None = None,
) -> AddMcpServerResult: ...

async def remove_mcp_server(self, id: str) -> None: ...
def get_mcp_servers(self) -> MCPServersState: ...
```

`add_mcp_server` behaves as upstream's HTTP overload (`index.ts:9660`):

- **Registers, connects and discovers.** It returns
  `AddMcpServerResult(id=..., state="ready")`, or `state="authenticating"`
  with `auth_url` when the server needs OAuth. The browser goes to
  `auth_url`, the provider redirects to the callback URL, and the
  connection finishes in the background (§2.4).
- **`id`** is a stable, caller-chosen id, normalized as
  upstream's `normalizeServerId` (lowercase, `[a-z0-9_-]`, starts with a
  letter, 64 characters at most). Without one, a random 8-character id is
  generated. Adding a server whose `id` already belongs to a different name or
  URL raises `MCPServerIdConflictError`.
- **It's idempotent.** Adding the same name and URL again returns the existing
  server's state instead of creating a second one. A server that is still
  authenticating returns its `auth_url` again. A failed one is reconnected.
- **`headers`** are sent on every request, for non-OAuth auth such as a bearer
  token or Cloudflare Access. They're persisted with the server (as upstream).
  Don't put a secret here that you wouldn't store in the agent's database.
- **The callback URL** is `{callback_host}/{agents_prefix}/{agent-kebab}/{name}/callback`,
  or `{callback_host}/{callback_path}`. `callback_host` defaults to the origin
  of the current request or connection (`get_current_agent()`). With no host
  available there's no callback URL, and OAuth servers fail with a clear
  error. When `send_identity_on_connect=False`, `callback_path` is required
  (as upstream), because the default URL contains the instance name.
- **Private addresses are refused** before anything is stored
  (`MCPBlockedUrlError`): RFC 1918, link-local, unique-local, unspecified and
  cloud metadata hosts. Loopback is allowed for local development.
- **On failure** it raises `MCPConnectionError`. The server stays stored, so
  `self.mcp.connect_server(id)` can retry it, as upstream.
- **Not ported:** the RPC overload (`addMcpServer(name, env.MY_MCP)` to an
  `McpAgent` binding), because `McpAgent` isn't ported (§4.3), and the
  deprecated positional `(name, url, callbackHost, agentsPrefix, options)`
  form.

`remove_mcp_server` closes the connection and deletes the row and the
server's OAuth keys. `get_mcp_servers()` returns what the
`cf_agent_mcp_servers` frame carries (§2.5).

### 2.2 Using a server's tools, prompts and resources

`self.mcp` is the `MCPClientManager` capability (§3). Without
`AgentOptions.mcp` it raises `MCPNotEnabledError`.

```python
class MyAgent(AIChatAgent):
    options = AIChatAgentOptions(mcp=MCPOptions(), wait_for_mcp_connections=5)

    async def on_chat_message(self, options: ChatMessageOptions) -> AsyncIterator[UIMessageChunk]:
        tools = self.mcp.list_tools()                # list[ServerTool]
        # ...hand tools to your model in its own format; when it calls one:
        chosen = tools[0]
        result = await self.mcp.call_tool(chosen.server_id, chosen.tool.name, {"query": "..."})
```

The SDK doesn't convert MCP tools into any model provider's format. This
matches `on_chat_message` being provider-agnostic
([scope.md](./scope.md) §2.4.1), so upstream's `getAITools` isn't ported.
`ServerTool.tool` is the SDK's own `mcp_types.Tool`, so `input_schema` is
plain JSON Schema that any provider adapter can use.

`AIChatAgentOptions.wait_for_mcp_connections` (`bool | Duration`, default
`False`) makes each chat turn wait for restoring servers before
`on_chat_message`, as upstream's `waitForMcpConnections`.

### 2.3 Hooks to override

```python
def create_mcp_oauth_provider(self, callback_url: str) -> MCPOAuthProvider: ...
async def on_mcp_oauth_callback(self, result: MCPOAuthResult, request: Request) -> Response | None: ...
```

- **`create_mcp_oauth_provider`**, as upstream's `createMcpOAuthProvider`.
  The default returns a `DurableObjectOAuthProvider` (dynamic client
  registration, tokens in DO storage). Override it for pre-registered client
  credentials or a different registration flow.
- **`on_mcp_oauth_callback`** runs after every callback. Return a `Response`
  to replace the default redirect, or `None` to keep it. This is upstream's
  `customHandler`. `result.error` can contain text from an external OAuth
  provider, so escape it before showing it to anyone.

### 2.4 OAuth, end to end

1. `add_mcp_server` gets a 401 from the server, so the provider registers a
   client and builds an authorization URL. The server is stored with that
   `auth_url`, its state becomes `authenticating`, and `add_mcp_server`
   returns the URL. Every connected client sees it in `cf_agent_mcp_servers`.
2. The user authorizes. The provider redirects to the callback URL, which
   `route_agent_request` delivers to the same agent instance.
3. The MCP capability claims the request in its `on_request` (it runs before
   the agent's own `on_request`). It checks the `state` nonce, exchanges the
   code for tokens, and replies with the redirect or the hook's response. The
   connection and discovery then finish in the background, and each change
   is broadcast.

This survives the agent being evicted between steps 1 and 2: everything the
callback needs (the state nonce, the PKCE verifier, the client registration)
is in storage, not in memory.

### 2.5 The `cf_agent_mcp_servers` frame

It has the same shape as upstream (wire §5.5), sent on connect and broadcast
on every server state change:

```json
{"type": "cf_agent_mcp_servers", "mcp": {
  "servers": {"<id>": {"name": "...", "server_url": "...", "auth_url": null,
                       "state": "ready", "error": null, "instructions": null,
                       "capabilities": {...}}},
  "tools": [{...MCP Tool..., "serverId": "<id>"}],
  "prompts": [...], "resources": [...]}}
```

`state` is one of `authenticating`, `connecting`, `connected`,
`discovering`, `ready`, `failed`, or `not-connected` (a stored server with
no live connection yet). With MCP off, the frame stays the empty one sent
today, so `useAgent`'s `onMcpUpdate` behaves the same either way.

---

## 3. Client: `MCPClientManager`

`agents.mcp.MCPClientManager` is a `LifecycleCapability` (id `"mcp"`), so
like upstream it also works on a plain Durable Object
(`Lifecycle(self).use(MCPClientManager(...))`). `Agent` installs it when
`options.mcp` is set. Everything `add_mcp_server` does is implemented
here. The `Agent` method only works out the callback URL and the client
name.

```python
class MCPClientManager(LifecycleCapability):
    def __init__(
        self,
        *,
        client_name: str,
        client_version: str,
        create_oauth_provider: Callable[[str], MCPOAuthProvider] | None = None,
        on_oauth_callback: Callable[[MCPOAuthResult, Request], Awaitable[Response | None]] | None = None,
        oauth_success_redirect: str | None = None,
        oauth_error_redirect: str | None = None,
        discovery_timeout: Duration = 15,
    ) -> None: ...

    on_server_state_changed: Emitter[None]          # core/events.py; .subscribe(listener)

    # servers
    async def add_server(self, name: str, url: str, *, id: str | None = None,
                         callback_url: str | None = None, headers: Mapping[str, str] | None = None,
                         transport: TransportType = "auto", retry: RetryOptions | None = None,
                         ) -> AddMcpServerResult: ...
    async def connect_server(self, id: str) -> AddMcpServerResult: ...   # retry a failed one
    async def remove_server(self, id: str) -> None: ...
    async def close_connection(self, id: str) -> None: ...               # keep the row
    async def discover(self, id: str, *, timeout: Duration | None = None) -> None: ...
    async def wait_for_connections(self, *, timeout: Duration | None = None) -> None: ...
    def list_servers(self) -> list[MCPServerRecord]: ...
    def servers_state(self) -> MCPServersState: ...

    # catalog (sync: served from memory; empty until discovery finishes)
    def list_tools(self, *, server_id: str | Sequence[str] | None = None,
                   server_name: str | Sequence[str] | None = None,
                   state: MCPConnectionState | Sequence[MCPConnectionState] | None = None,
                   ) -> list[ServerTool]: ...
    def list_prompts(self, **filters) -> list[ServerPrompt]: ...
    def list_resources(self, **filters) -> list[ServerResource]: ...
    def list_resource_templates(self, **filters) -> list[ServerResourceTemplate]: ...

    # calls
    async def call_tool(self, server_id: str, name: str, arguments: Mapping[str, Any] | None = None,
                        *, timeout: Duration | None = None) -> CallToolResult: ...
    async def read_resource(self, server_id: str, uri: str) -> ReadResourceResult: ...
    async def get_prompt(self, server_id: str, name: str,
                         arguments: Mapping[str, str] | None = None) -> GetPromptResult: ...
```

- **The filters** are upstream's `MCPServerFilter`, as keyword arguments; all
  given filters must match. The `**filters` above is shorthand for the same
  three keywords.
- **The result types** (`CallToolResult`, …) are `mcp_types`' own pydantic
  models. They aren't re-wrapped: they're the SDK's public API, and wrapping
  them would only lag behind it.
- **`call_tool` on a server that isn't ready** raises `MCPServerNotReadyError`.
  A tool that reports `is_error=True` isn't an exception; it's returned as
  is, as upstream.
- **`wait_for_connections` never raises**, as upstream: it returns once every
  pending connect-and-discover has settled or `timeout` has passed. `0`
  returns immediately.
- **Upstream's public surface that is left out:** `mcpConnections` as a
  writable map (it becomes a read-only `connections` mapping),
  `registerServer` / `connectToServer` as two separate public steps (that
  becomes `add_server` plus `connect_server`), `migrateServerId`, the
  deprecated `connect()`, and the `unstable_getAITools` / `getAITools` pair.

### 3.1 Records and types (`agents/mcp/types.py`)

```python
class MCPConnectionState(StrEnum):
    AUTHENTICATING = "authenticating"; CONNECTING = "connecting"; CONNECTED = "connected"
    DISCOVERING = "discovering"; READY = "ready"; FAILED = "failed"

type TransportType = Literal["auto", "streamable-http", "sse"]

@dataclass(slots=True, kw_only=True)
class AddMcpServerResult:
    id: str
    state: Literal["ready", "authenticating"]
    auth_url: str | None = None

@dataclass(slots=True, kw_only=True)
class MCPServerRecord:        # one cf_agents_mcp_servers row, decoded
    id: str; name: str; server_url: str; callback_url: str | None
    client_id: str | None; auth_url: str | None

@dataclass(slots=True, kw_only=True)
class ServerTool:             # likewise ServerPrompt, ServerResource, ServerResourceTemplate
    server_id: str
    tool: mcp_types.Tool

@dataclass(slots=True, kw_only=True)
class MCPOAuthResult:
    server_id: str | None
    success: bool
    error: str | None = None  # untrusted text from the OAuth provider
```

`MCPServersState` and `MCPServerInfo` are the frame's shape (§2.5), as
dataclasses with `wire()` field names
([utilities.md](./utilities.md) §5).

### 3.2 Errors (`agents/mcp/errors.py`)

All of them subclass `MCPError(AgentsException)`: `MCPConnectionError`,
`MCPServerIdConflictError`, `MCPBlockedUrlError`, `MCPServerNotFoundError`,
`MCPServerNotReadyError`, `MCPOAuthError`. `MCPNotEnabledError` is in
`agents/agent/errors.py` instead, because `Agent` raises it without
importing `agents.mcp`.

---

## 4. Server: hosting an MCP server on a Worker

### 4.1 `create_mcp_handler`

```python
from agents.mcp.server import create_mcp_handler, get_mcp_auth_context
from mcp.server.mcpserver import MCPServer
from workers import WorkerEntrypoint

server = MCPServer("Demo")


@server.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


handler = create_mcp_handler(server, route="/mcp")


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await handler(request, self.env, self.ctx)
```

```python
def create_mcp_handler(
    server: MCPServer | Server,
    *,
    route: str = "/mcp",
    cors: CORSOptions | None = CORSOptions(),     # None disables CORS headers
    allowed_hostnames: Sequence[str] | None = None,
    allowed_origin_hostnames: Sequence[str] | Literal["*"] | None = None,
    auth_context: McpAuthContext | None = None,
    json_response: bool = False,
) -> McpHandler: ...

class McpHandler:
    async def __call__(self, request: Request, env: Any, ctx: Any) -> Response: ...
    async def fetch(self, request: Request) -> Response: ...       # from an Agent's on_request
```

This is upstream's `createMcpHandler` (`handler-stateless.ts`):

- **Stateless.** Each request is served on its own and nothing is kept
  between requests. It handles both protocol eras: 2026-07-28 requests, and
  handshake-era (2025) clients through the SDK's stateless legacy path.
- **Route matching:** any other path gets a 404.
- **CORS:** applied to every response, including the preflight.
- **Host / Origin validation:** same defaults as upstream (localhost, the
  endpoint's `workers.dev` host, the CORS origin). Requests without an
  `Origin` header, which non-browser clients send, are allowed.
- **`get_mcp_auth_context()`** returns the `auth_context` passed in (props
  and verified OAuth info) inside a tool. It's a `ContextVar`, as upstream's
  `getMcpAuthContext` is `AsyncLocalStorage`.
- **Not ported in the first pass:** `notify` (server-initiated
  notifications have no channel in stateless mode), and the
  `handler-compat` / `handler-legacy` shims for upstream's own older APIs.

### 4.2 Using it from an agent

```python
class MyAgent(Agent):
    async def on_request(self, request: Request) -> Response:
        return await handler.fetch(request)
```

Tools then run inside the agent, so `get_current_agent()` works in them. The
endpoint is still stateless: each request is independent, and any
per-session state is the tool's job (for example, storing it in agent state).

### 4.3 Not ported: `McpAgent`

Upstream's `McpAgent` (`legacy-agent.ts`) is deprecated and built on SDK
v1. Upstream tells new servers to use `createMcpHandler`. Not porting it
also drops `cf_mcp_agent_event`, `_cf_scheduleDestroy` and the client's RPC
transport (`mcp/rpc.ts`).

---

## 5. Import paths

| Import | What it costs |
| --- | --- |
| `from agents import Agent, AgentOptions, MCPOptions` | Nothing from `mcp` |
| `from agents.mcp import MCPClientManager, MCPConnectionState, ServerTool, ...` | Imports `mcp` (the client side) |
| `from agents.mcp.server import create_mcp_handler, get_mcp_auth_context` | Imports `mcp` (the server side) |

Importing `agents.mcp` without the extra raises an `ImportError` that names
`cf-agents[mcp]` and the Python 3.14 Workers requirement.
