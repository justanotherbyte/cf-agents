# MCP (planning record)

> **Decided (2026-10-26): the client doesn't use the MCP SDK.** The bundle
> measurement in §5.3.1 showed `mcp` adds 15 to 19 MiB, mostly native code
> the client never runs, and limits MCP to 3.14 Workers. `agents.mcp` is
> now our own client with no dependency beyond cf-agents, so it runs on
> every Workers Python version. Only server hosting (`agents.mcp.server`)
> uses the SDK, through the `cf-agents[mcp-server]` extra. The client is
> built: see [mcp_api.md](./mcp_api.md) and [mcp_engine.md](./mcp_engine.md),
> which win wherever this doc differs. This doc's SDK analysis (§2, §3) now
> applies to the server extra only.

Plan for porting upstream's MCP support (`../agents/packages/agents/src/mcp/`,
`agents` v0.28.1, built on `@modelcontextprotocol/client` and
`/server` 2.3.1) on top of the MCP Python SDK (`mcp` on PyPI).
MCP is out of scope for phase 1 ([scope.md](./scope.md) §3); this doc
records what a port needs before that changes.

Related: [agents_wire_protocol.md](./agents_wire_protocol.md) §5.5 (the
`cf_agent_mcp_servers` frame), [core_disposable_store.md](./core_disposable_store.md)
§3 (how MCP state reaches the browser), [platform_verification.md](./platform_verification.md)
§2.8 and §6 (outbound requests and cancellation),
[agents-subpackages.md](./agents-subpackages.md) `mcp/`.

---

## 1. Summary

- **CPython 3.12+ is fine.** Every `mcp` release, including the current
  2.3.0, declares `requires-python >=3.10`.
- **Python Workers are the constraint, not the Python version.** What
  matters is which wheels the Pyodide package index for each Workers Python
  version has. `mcp` 2.x resolves only for **Python 3.14 Workers** (Pyodide
  314). On 3.12 and 3.13 Workers, the newest `mcp` that resolves is
  **1.12.4** (August 2025), because those indexes cap `pydantic` at 2.10.6.
- **Proposal:** ship MCP as an optional extra, `cf-agents[mcp]` with
  `mcp>=2.3,<3`. The core package keeps `requires-python >=3.12`, and MCP
  documents that it needs Python 3.14 Workers (§3).
- **Core work first:** yes, but only a small amount (§5). It covers
  packaging and the import boundary, making the `cf_agent_mcp_servers` frame
  dynamic, and two platform checks (outbound HTTP through `httpx2` on
  Workers, and the deploy size and import cost of `mcp`). Everything else the
  client needs already exists: capability `on_request`, `Emitter`,
  `retry()`, keep-alive and the `mcp:` observability prefix.

---

## 2. The MCP Python SDK's dependencies

`mcp` 2.3.0 (2026-10-02). The tree was resolved with uv on CPython 3.12:

| Dependency | Wheel | Notes |
| --- | --- | --- |
| `mcp-types` 2.3.0 | pure | Protocol models (pydantic) |
| `pydantic` ≥2.12 → `pydantic-core` | **native** | Determines whether the Workers resolution succeeds (§2.1) |
| `httpx2` (≥2.10) | pure | On `sys_platform == "emscripten"` it swaps `httpcore2`/`anyio`/`truststore` for `httpx2-jsfetch` ("httpx2 transports for Emscripten/Pyodide, backed by the JavaScript fetch API") |
| `anyio` ≥4.9 | pure | |
| `jsonschema` → `referencing` → `rpds-py` | **native** (`rpds-py`) | Has a Pyodide wheel on every index |
| `pyjwt[crypto]` → `cryptography` → `cffi` | **native** | Has a Pyodide wheel on 3.14; it's the largest package in the tree (15 MB unpacked on CPython) |
| `starlette`, `sse-starlette`, `python-multipart` | pure | Used by the server's HTTP stack (ASGI) |
| `opentelemetry-api` | pure | |
| `uvicorn` | pure | Skipped on emscripten (`sys_platform != "emscripten"`) |

The full tree is 31 MB unpacked on CPython. Importing `mcp.client.client`
on CPython 3.12 costs about 0.5 s and pulls in `cryptography` and
`httpx2`.

### 2.1 Resolution for each Workers Python version

The same command `pywrangler` (workers-py 1.17.8) runs: `uv pip compile`
against `cpython-<v>-emscripten-wasm32-musl`, with the Pyodide index for that
version and `--no-build`, alongside `workers-runtime-sdk` and our
`typing-extensions>=4.12`.

| Workers Python | Pyodide index | `mcp` (unpinned) | `mcp>=2,<3` | `mcp>=1.30,<2` |
| --- | --- | --- | --- | --- |
| 3.12 | 0.27.7 | 1.12.4 | **fails** | **fails** |
| 3.13 | 0.28.3 | 1.12.4 | **fails** | **fails** |
| 3.14 (compat date ≥ 2026-09-08) | 314.0.7 | 2.3.0 | 2.3.0 | 1.30.0 |

The 3.12 and 3.13 failures come from `pydantic-core`: those indexes stop at
`pydantic` 2.10.6, and every `mcp` from 1.13.0 (2025-08-14) on requires
`pydantic>=2.11`.

**Imports on Pyodide 314:** `micropip.install("mcp==2.3.0")` followed by
importing `mcp.client.client`, `mcp.client.streamable_http`,
`mcp.server.lowlevel` and `mcp.server.mcpserver` works under Pyodide
314.0.7 in Node. It loaded Pyodide's own `pydantic` 2.12.5 /
`pydantic-core` 2.41.5 and `cryptography` 47.0.0, and the client import
took about 2 s cold. This hasn't been run in `workerd` yet (§5.3).

### 2.2 What 1.12.4 would cost

`mcp` 1.12.4 is the only option on 3.12 and 3.13 Workers, so it was
checked as a fallback and rejected (decided, pending review):

- It supports protocol versions 2024-11-05, 2025-03-26 and 2025-06-18.
  Upstream speaks 2025-11-25 and 2026-07-28 (the stateless, per-request
  envelope) through SDK v2, and so does `mcp` 2.x
  (`mcp_types.KNOWN_PROTOCOL_VERSIONS`).
- Its API is the 1.x generation (`ClientSession`, `FastMCP`, `httpx`). 2.x
  replaced it (`mcp.client.Client`, `mcp.server.mcpserver`, `httpx2`).
  Supporting both means two client and two server implementations.
- It uses `httpx`, which the runtime patches onto JS `fetch` and which never
  aborts on cancellation ([platform_verification.md](./platform_verification.md)
  §2.8).
- It's frozen: a fix to the protocol or a security fix lands only in 2.x.

---

## 3. Version policy (proposed)

```toml
[project.optional-dependencies]
mcp = ["mcp>=2.3,<3"]
```

- **Core is unchanged.** `requires-python >=3.12` and `dependencies` stay as
  they are. `import agents` never imports `mcp`, which is enforced by a test
  (§5.1).
- **No environment marker.** `python_version >= "3.14"` on the extra would
  silently drop MCP from the 3.12 CPython test environment and from a 3.13
  Worker. Without it, the CPython tests run on 3.12, and a 3.12/3.13 Worker
  that asks for `cf-agents[mcp]` fails at `pywrangler sync` with a
  resolution error. The docs say MCP needs Python 3.14 Workers, and
  `agents.mcp`'s import error says it again.
- **Lower bound 2.3** matches upstream's SDK generation (2.3.1) and is the
  release that was proven to resolve and import above. **Upper bound `<3`**,
  because the SDK changed its API at 2.0.
- If the 3.12 and 3.13 indexes ever get `pydantic>=2.12`, MCP gains those
  versions with no change on our side.

---

## 4. Upstream's shape, and the Python port

### 4.1 Client: `MCPClientManager` (`mcp/client/`, about 4.3k lines)

Upstream installs it on every `Agent` as `this.mcp`, a
`LifecycleCapability`:

- **Connections:** one `MCPClientConnection` per server, with states
  `authenticating → connecting → connected → discovering → ready` (or
  `failed`). Streamable HTTP, SSE (legacy) and RPC to an `McpAgent` Durable
  Object binding (`mcp/rpc.ts`).
- **Persistence:** the `cf_agents_mcp_servers` table (id, name,
  server_url, callback_url, client_id, auth_url, server_options JSON).
  `restoreConnectionsFromStorage` runs on start, and `waitForConnections`
  lets a chat turn wait for it.
- **OAuth:** `DurableObjectOAuthClientProvider` keeps the client info,
  tokens and PKCE verifier in DO storage. The capability's `onRequest`
  claims the callback URL.
- **Catalog:** tools, prompts, resources and resource templates namespaced
  by server id; `callTool`, `readResource`, `getPrompt`; `getAITools`
  (AI SDK tool objects).
- **Agent API:** `addMcpServer` (URL or DO binding), `removeMcpServer`,
  `getMcpServers`, and a `cf_agent_mcp_servers` broadcast whenever a server's
  state changes (`onServerStateChanged` → `broadcastMcpServers`).

**Python port:**

- `MCPClientManager` becomes a `LifecycleCapability` in `agents.mcp.client`,
  wrapping `mcp.client.Client` for each server. It's not installed by
  default (§4.4). The states, the table and the JSON in `server_options`
  match upstream exactly, so storage written by one SDK can be read by the
  other.
- `Agent.add_mcp_server`, `remove_mcp_server` and `get_mcp_servers` are
  keyword-only, like the rest of the API.
- **The catalog is provider-agnostic, like `on_chat_message`**
  ([scope.md](./scope.md) §2.4.1). `list_tools()` returns MCP `Tool`
  models with `server_id`, and `call_tool(server_id, name, arguments)`.
  There's no `get_ai_tools`: converting tools for a provider belongs with
  the deferred provider adapters.
- **Not in the first pass:** the RPC transport to an `McpAgent` binding
  (it needs a Python `McpAgent`, §4.2), SSE-only legacy servers (unless
  `mcp` 2.x's SSE client works on Workers for free), x402 and elicitation
  handlers.

### 4.2 Server (`mcp/server/`)

- **`createMcpHandler`** (`handler-stateless.ts`) is upstream's
  recommended path: a Worker `fetch` handler wrapping an SDK v2 stateless
  server, with route matching, CORS and Host/Origin validation, and an auth
  context.
- **`McpAgent`** (`legacy-agent.ts`, 713 lines) is **deprecated** upstream:
  an SDK v1 server backed by a DO, with SSE and Streamable HTTP tunnelled
  through `cf_mcp_agent_event` frames and `_cf_scheduleDestroy`.

**Python port:** `create_mcp_handler(server, *, route="/mcp", cors=...,
allowed_hostnames=..., allowed_origin_hostnames=...)` in
`agents.mcp.server`, taking an `mcp.server.mcpserver` server. The
Python SDK's HTTP serving is ASGI (Starlette `StreamableHTTPSessionManager`
with `stateless=True`, which handles both the legacy handshake and
2026-07-28). The first version bridges it with the Workers SDK's `asgi`
adapter rather than reimplementing the transport. **`McpAgent` is not
ported** (it's deprecated upstream), which also removes the need for
`cf_mcp_agent_event` and `_cf_scheduleDestroy`.

### 4.3 Chat

`AIChatAgent` waits for MCP connections before a turn
(`waitForMcpConnections`, deferred in [chat_engine.md](./chat_engine.md)).
That arrives with the client as an `AIChatAgent` option. Tools reach the
model through the user's own `on_chat_message`.

### 4.4 Optional install

Upstream builds the MCP capability in every `Agent` constructor. Here,
`Agent.mcp` is a lazy property that imports `agents.mcp.client` on first
use and `use()`s the capability. One problem remains: a DO that already has
servers in `cf_agents_mcp_servers` must restore them on start, before any
user code touches `self.mcp`. Options:

1. In `on_start`, check whether the table exists and has rows (one cheap
   `sqlite_master` query) and install the capability if so.
2. Ask users who use MCP to opt in at class level (`options.mcp = True`, or
   `self.use(MCPClientManager(...))` in `__init__`, as with other extra
   capabilities, [agent_api.md](./agent_api.md) §2).

Option 2 matches how extra capabilities are already declared and avoids
hidden work in `on_start`. Option 1 matches upstream's "just call
`add_mcp_server`". **Open.**

---

## 5. Core work before MCP

### 5.1 Packaging and the import boundary (small)

- Add the `mcp` extra and include it in the dev environment, so CI and the
  Setup's `uv sync` install it.
- `agents/mcp/__init__.py` turns a missing `mcp` into an
  `ImportError` that names the extra and the Python 3.14 Workers
  requirement.
- A test, next to `tests/test_public_api.py`, asserting that
  `import agents` (and `agents.chat`) never loads `mcp`, `pydantic` or
  `httpx2`. Core users must not pay a 0.5 s or larger import.

### 5.2 A dynamic `cf_agent_mcp_servers` frame (small)

`agent.py` sends a constant `_MCP_SERVERS_FRAME` on connect. It should
become `Agent._mcp_servers_state()` (empty when MCP isn't installed)
plus a `broadcast_mcp_servers()` path that respects protocol-disabled
connections, as the state broadcast does. The wire output doesn't change
until a client is installed, so this can land and be tested on its own.

### 5.3 Platform checks (verification; no SDK code)

Add these to the verification Worker
([platform_verification.md](./platform_verification.md)), which needs a 3.14
compat date for this:

1. **`httpx2` on `workerd`:** a plain request, a streamed SSE response and
   cancellation. It goes through `httpx2-jsfetch`, not the runtime's
   `httpx` patch measured in §2.8. If it doesn't abort on cancellation,
   decide whether the deferred `FetchTransport` (§6.1 of that doc) becomes
   an `httpx2` transport that the client passes to `mcp.client.Client`.
2. **Deploy size and cold start:** the Worker's bundle size with
   `cf-agents[mcp]`, compared with the plan limits, and the first-request
   time with `mcp` imported at top level so the memory snapshot can hold
   it. Size is measured (§5.3.1). Production startup time isn't.
3. **The ASGI bridge** for `create_mcp_handler`: one stateless
   `tools/call` round trip in each protocol era.

#### 5.3.1 Bundle size (measured 2026-10-26)

Wrangler 4.129.0, Pyodide 314.0.7, `mcp` 2.3.0, cf-agents 0.1.3. "Upload"
is wrangler's `Total Upload`, which is the uncompressed size the limit
applies to.

| Worker | pywrangler upload | Wynd upload |
| --- | --- | --- |
| cf-agents only | 1.1 MiB | 0.8 MiB |
| + `mcp`, client imports only | 19.7 MiB | 16.0 MiB |
| + `mcp`, client and server | 19.7 MiB | 16.0 MiB |

- **The limits fit.** Since the 2026-10-12 limits page, both plans allow
  64 MiB uncompressed and have no compressed limit, so MCP isn't a
  paid-plan feature. Global scope must still run within 1 s, which isn't
  measured: locally, `wrangler dev` reached "Ready" about 3 s later with
  `mcp` (2 s with Wynd), without a memory snapshot.
- **Client-only costs the same as client and server.** `mcp/__init__.py`
  imports `mcp.client.client`, which imports `mcp.server` and
  `mcp.server.mcpserver`. `mcp.server.request_state` imports `cryptography`
  at module level. Any `import mcp.*` loads the server, starlette and
  cryptography.
- **Native code is most of it.** The `.so` files are 11.6 MiB of Wynd's
  16.0: cryptography's `_rust` 6.1 MiB, `_pydantic_core` 4.5 MiB, `rpds`
  0.4 MiB, `_cffi_backend` 0.1 MiB. Wynd keeps a `.so` whole. Its 3.8 MiB
  saving is half dropped files (cffi, pycparser, pyjwt and unused
  submodules) and half stripped comments and docstrings.
- **Wynd needs `keep = ["_cffi_backend"]`.** cryptography's `_rust.abi3.so`
  imports it from native code, so the shaker can't see it, and the shaken
  Worker fails at startup with `ModuleNotFoundError`. `wynd dev` serves the
  unshaken tree and doesn't catch this. Run
  `wrangler dev -c .wynd/build/wrangler.json` after `wynd deploy --dry-run`.

### 5.4 Already in place

Capability `on_request` (the OAuth callback), `Emitter`
(`core/events.py`, for server state changes), `retry()` (upstream's
`_connectWithRetry`), keep-alive (holding the DO up during a long
connect), and the `mcp:` → `mcp` observability channel prefix.

---

## 6. Build order (proposed)

1. §5.1 and §5.2, then the §5.3 checks.
2. Client, no OAuth: Streamable HTTP (both eras), persistence and restore,
   the catalog, `call_tool`, the broadcast, `wait_for_connections`,
   `mcp:*` events.
3. Client OAuth: the DO-backed provider and the callback route.
4. Server: `create_mcp_handler`.
5. Chat: `AIChatAgent` waiting on MCP connections.

---

## 7. Open

- §4.4: lazy install on start or explicit opt-in.
- Whether facets can host MCP clients. Upstream's OAuth callback is routed
  to the agent's own URL, which for a facet is a `/sub/` path.
- Whether to accept SSE-only servers in the first client pass (§4.1).
- Whether a Worker that imports `mcp` at top level stays under the 1 s
  startup limit on a real deploy (§5.3.1).
