# Design docs: agents-py

Design records for the Python port of the Cloudflare Agents SDK. Upstream
reference: `../agents` (TypeScript, `packages/agents` v0.25.0 and
`packages/ai-chat`).

Each doc keeps its own **Decided** / **Open** sections up to date as the
design evolves. This index lists where to look.

| Doc | What it covers |
| --- | --- |
| [code_semantics.md](./code_semantics.md) | How SDK code is written and organized: style, project structure, typing, tooling (ruff, ty) |
| [scope.md](./scope.md) | What phase 1 includes and excludes, open questions, build order |
| [agents-subpackages.md](./agents-subpackages.md) | Map of every upstream `packages/agents` folder |
| [lifecycle_capabilities.md](./lifecycle_capabilities.md) | Lifecycle and the capability model |
| [agents_wire_protocol.md](./agents_wire_protocol.md) | The WebSocket protocol (and the `get-messages` HTTP route) the JS clients speak |
| [scheduling_queue_tasks_api.md](./scheduling_queue_tasks_api.md) | Python API for schedules, queues, tasks |
| [tasks_engine.md](./tasks_engine.md) | The Tasks engine: runs, the step journal, replay, claims and fencing, wakes, recovery |
| [subagents_engine.md](./subagents_engine.md) | Sub-agents (facets): identity, the registry, routing to the root, `/sub/` forwarding, root-owned sockets |
| [fibers_api.md](./fibers_api.md) | Python API and mechanics for fibers (`run_fiber`, `start_fiber`, …) |
| [fibers_engine.md](./fibers_engine.md) | Fibers internals: the `KeepAlive` and `Fibers` capabilities, recovery, facet leases over the route transport |
| [sessions_api.md](./sessions_api.md) | Sessions, scoped to what `AIChatAgent` needs |
| [sessions_engine.md](./sessions_engine.md) | Sessions internals: rows and splitting, the cached tail, the synchronous upsert for chat's cutover, path reads, the change feed |
| [ai_chat_agent_api.md](./ai_chat_agent_api.md) | `AIChatAgent`'s own API: writing messages (`persist_messages`, `save_messages`, `continue_last_turn`, `delete_messages`) |
| [chat_models.md](./chat_models.md) | `UIMessageChunk`, `UIMessage` / parts, stream rules for `on_chat_message` |
| [chat_engine.md](./chat_engine.md) | Step 12 plan: every upstream chat module sorted into sub-steps 12a–12d (models, turn path, client tools, recovery), plus each sub-step's design pass and implementation notes (12a, 12b done) |
| [streams_api.md](./streams_api.md) | Streams: durable chunk logs |
| [streams_engine.md](./streams_engine.md) | Streams internals: storage, the fenced append, the cutover, the read loop, chat's synchronous surface |
| [agent_api.md](./agent_api.md) | Core `Agent` API decisions |
| [sql_schemas.md](./sql_schemas.md) | Every SQLite table and KV key phase 1 uses |
| [utilities.md](./utilities.md) | SDK-wide principles (exceptions, durations, timestamps), method lookup, the FFI module, the dataclass convention, typed SQL, `retry()` |
| [core_disposable_store.md](./core_disposable_store.md) | Upstream `core/`: `DisposableStore` → `AsyncExitStack`, `Emitter` |
| [`[NICE_TO_HAVE_DO_NOT_INCLUDE_YET]_asgi_and_django_integrations.md`](./%5BNICE_TO_HAVE_DO_NOT_INCLUDE_YET%5D_asgi_and_django_integrations.md) | **Not in scope yet.** Ideas for agents alongside FastAPI / Starlette / Django (ASGI and WSGI) |
| [mcp.md](./mcp.md) | **Planning record.** Why the client doesn't use the MCP SDK (bundle size, 3.14-only), the SDK's dependencies and Workers support, the measured bundle sizes |
| [mcp_api.md](./mcp_api.md) | **Decided.** MCP's public API: `MCPOptions`, `add_mcp_server` and the other `Agent` members, `MCPClientManager`, `create_mcp_handler` |
| [mcp_upstream_usage.md](./mcp_upstream_usage.md) | **Reference.** Cloudflare's recommended MCP usage in the TypeScript Agents SDK (client, OAuth, elicitation, `createMcpHandler`), and where our plan differs |
| [mcp_engine.md](./mcp_engine.md) | **Decided; built.** MCP internals: our own Streamable HTTP client in both protocol eras, the connection state machine, restore, storage, the OAuth flow split across requests, the SDK-backed server handler, tests |
| [platform_verification.md](./platform_verification.md) | Results of running the "to verify" items on the Python Workers runtime |
| [observability.md](./observability.md) | Structured events: upstream pipeline, in-scope events, Python design |

## Where the open decisions are

No design decision is open. What remains:

**Deferred (recorded, not scheduled):**
- the read-time chat truncation helpers (`truncate_older_messages`, tool-output
  truncation) ([chat_engine.md](./chat_engine.md) §3 Q2);
- a naming pass over the inspect and cancel APIs, and `@task(name=...)`
  ([scheduling_queue_tasks_api.md](./scheduling_queue_tasks_api.md) §3);
- exponential backoff between failed startups ([agent_api.md](./agent_api.md) §1.10);
- typed stubs for `get_agent_by_name` ([agent_api.md](./agent_api.md) §1.14);
- an httpx transport that aborts on cancellation, and testing aiohttp
  ([platform_verification.md](./platform_verification.md) §6);
- MCP's deferred pieces: the legacy SSE transport, handshake-session
  resumption, elicitation, scope step-up ([mcp_engine.md](./mcp_engine.md) §12);
- running MCP on `workerd` in the verification Worker
  ([mcp_engine.md](./mcp_engine.md) §13);
- ASGI / Django integrations (the `[NICE_TO_HAVE_DO_NOT_INCLUDE_YET]` doc).

**Accepted limitations:**
- a bare facet keep-alive lease whose facet dies and never wakes again
  stays on the root until the root's isolate ends (leases are dropped on
  deletion and on the facet's restart; [fibers_engine.md](./fibers_engine.md) §6).

**Production checks** ([platform_verification.md](./platform_verification.md) §7): done.
Decided from them: a level-preserving log handler for the SDK's `agents.*`
loggers ([observability.md](./observability.md) §6).

