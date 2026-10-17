# MCP: upstream's recommended usage (reference)

What Cloudflare's own docs recommend for MCP in the TypeScript Agents SDK,
pulled 2026-10-17 via the Cloudflare plugin's `agents-sdk` skill
(`references/mcp.md`), which points at these pages:

- [Client API](https://developers.cloudflare.com/agents/model-context-protocol/apis/client-api/) (updated 2026-07-27)
- [Handler API](https://developers.cloudflare.com/agents/model-context-protocol/apis/handler-api/) (2026-07-28)
- [Transport](https://developers.cloudflare.com/agents/model-context-protocol/protocol/transport/) (2026-07-27)
- [Migrate to MCP SDK v2](https://developers.cloudflare.com/agents/model-context-protocol/guides/migrate-to-mcp-sdk-v2/) (2026-07-28)
- [Securing MCP servers](https://developers.cloudflare.com/agents/model-context-protocol/guides/securing-mcp-server/) (2026-06-03)

The skill's one-line rule: **for new servers, prefer `createMcpHandler` over
the deprecated `McpAgent`.**

---

## 1. Client

### 1.1 The recommended shape

```ts
export class MyAgent extends Agent {
  async onRequest(request: Request) {
    const result = await this.addMcpServer("github", "https://mcp.github.com/mcp");
    if (result.state === "authenticating") return Response.redirect(result.authUrl);
    const state = this.getMcpServers();            // servers, tools, prompts, resources, resourceTemplates
    return new Response("MCP server connected");
  }
}
```

- **`addMcpServer` is idempotent on (name, URL)**, with the URL normalized
  (trailing slash, default port, hostname case). So calling it in
  `onStart()` is the recommended pattern: it doesn't duplicate on restart.
  The same name with a different URL creates a second connection, and both
  servers' tools are merged. To replace a server, remove the old one first.
- **Non-OAuth servers need no options.** For OAuth servers, `callbackHost` is
  derived from the incoming request or WebSocket URI, and is only set for
  custom domains.
- **Returns** `{ id, state: "ready" }` or `{ id, state: "authenticating",
  authUrl }`.
- **Stable `id`** gives readable tool keys (`tool_github_<name>`). Adding
  `{ id }` to a server already registered under a generated id **migrates
  it in place** (the row, the connection, the OAuth keys). It throws only
  when the same id belongs to a different (name, URL).
- **`transport.headers`** for bearer tokens or Cloudflare Access.
  `transport.type`: `auto` (default), `streamable-http`, `sse` (legacy).
- **SSRF blocking** as in our plan; loopback is allowed. For internal
  services in production, the docs point at the RPC transport.
- **`retry`** defaults to 3 attempts, 500 ms base, 5 s cap. It's persisted
  and used for restore and after OAuth.

### 1.2 OAuth

- The callback URL is `https://{host}/{agentsPrefix}/{agent}/{instance}/callback`.
  Tokens persist in the agent's storage.
- **With `sendIdentityOnConnect: false`, `callbackPath` is required**, and
  the Worker routes that path to the agent itself with `getAgentByName`.
  Callbacks are matched by the `state` parameter, not the path.
- **`this.mcp.configureOAuthCallback({ successRedirect, errorRedirect,
  customHandler })`**, called in `onStart()`. The documented default:
  success redirects to the app origin, and failure shows **an HTML error
  page**. (The 0.28.1 source redirects to the origin in both cases;
  `client/index.ts:478`.) `customHandler` is typically
  `<script>window.close()</script>` for popups.
- **`createMcpOAuthProvider(callbackUrl)`** is overridden for
  pre-registered client credentials or mTLS. It's used for both new and
  restored connections. `DurableObjectOAuthClientProvider` can be reused
  with a custom storage adapter.
- A custom provider must implement the v2 `OAuthClientProvider` contract,
  including persisting `OAuthDiscoveryState` across the redirect.
  `skipIssuerMetadataValidation` exists only as an escape hatch for known
  bad servers.

### 1.3 Using tools

- **`this.mcp.listTools(filter?)`** returns the raw catalog. Discovery uses
  `listTools()` or `getMcpServers().tools`.
- **`this.mcp.getAITools(filter?)`** returns AI SDK tools, namespaced by
  server id, and is passed straight to `generateText` / `streamText`.
- `MCPServerFilter`: `{ serverId?, serverName?, state? }`, each a value or
  an array, combined with AND. It's accepted by `listTools`,
  `listPrompts`, `listResources`, `listResourceTemplates` and
  `getAITools`.
- `callTool(params, options)` is the preferred signature. The
  `resultSchema` overload is deprecated.

### 1.4 Elicitation (first-class upstream)

- `this.mcp.configureElicitationHandlers({ form, url })`, in `onStart()`.
  Each handler gets `(request, serverId, signal?)` and returns `accept` /
  `decline` / `cancel`.
- It serves both protocol lanes. On the stateless lane the server returns
  `input_required`, the SDK calls the handler and retries the original call
  (multi-round-trip requests, "MRTR"), and the caller's `callTool` simply
  stays pending. On the legacy lane it handles pushed `elicitation/create`
  requests.
- Only modes with a handler are advertised. The advertised modes are
  persisted with the server, so a restored connection advertises the same
  ones.
- **The recommended UI pattern:** broadcast the request to clients, resolve
  it from a `@callable respondToElicitation(id, result)`, and time out at
  55 s, because MCP requests default to 60 s.
- In-flight interactive calls don't survive hibernation or a reconnect; the
  caller retries.

### 1.5 Restore and waiting

- Registrations, tokens and advertised capabilities are restored when the
  agent wakes.
- `this.mcp.waitForConnections({ timeout })` is for an `Agent` that needs
  the tools right after waking.
- **`AIChatAgent` calls it automatically: `waitForMcpConnections`
  defaults to `{ timeout: 10_000 }`.**
- Lower level: `registerServer` → `connectToServer` → `discoverIfConnected`,
  plus `closeConnection`, `closeAllConnections` and the
  `onServerStateChanged` event.
- Error helpers: `isUnauthorized(error)`, `isTransportNotImplemented(error)`.

### 1.6 Browser side

`useAgent({ onMcpUpdate })` receives `cf_agent_mcp_servers`. Broadcasts skip
connections where `shouldSendProtocolMessages` returned `false`. Error
strings are escaped before they're stored, so they're safe to show.

---

## 2. Server

### 2.1 `createMcpHandler` (recommended)

```ts
function createServer() {
  const server = new McpServer({ name: "hello-server", version: "1.0.0" });
  server.registerTool("hello", { description: "...", inputSchema: { name: z.string().optional() } },
    async ({ name }) => ({ content: [{ type: "text", text: `Hello, ${name ?? "World"}!` }] }));
  return server;
}

export default {
  fetch(request, env, ctx) {
    return createMcpHandler(createServer)(request, env, ctx);
  },
} satisfies ExportedHandler;
```

- **Pass the factory, not a server.** The docs say: "Do not create one
  global server instance or pass a constructed SDK v2 server directly." One
  server is built per request, so concurrent requests never share one. The
  factory may be async and receives `{ era: "modern" | "legacy", authInfo?,
  requestInfo? }`.
- **Don't `export default createMcpHandler(...)`**: Wrangler treats a
  function default export as a `WorkerEntrypoint` class.
- **Options:**

  | Option | Default |
  | --- | --- |
  | `route` | `/mcp` |
  | `corsOptions` | wildcard; `false` removes the headers |
  | `allowedHostnames` | — |
  | `allowedOriginHostnames` | — (`"*"` only behind trusted middleware) |
  | `authContext` | — |
  | `legacy` | `"stateless"`; `"reject"` for a stateless-only endpoint |
  | `responseMode` | `"auto"`; also `"json"` (drops notifications sent before the result) or `"sse"` |
  | `onerror` | — |
  | `maxSubscriptions` | 1024 |
  | `keepAliveMs` | 15 000 |

  SDK v1 options (`transport`, `sessionIdGenerator`, `enableJsonResponse`,
  `eventStore`, …) are rejected.
- **The legacy-stateless lane:**
  - each POST gets a new server;
  - GET and DELETE return 405;
  - there are no session ids;
  - pushed elicitation, sampling and roots requests fail immediately;
  - there's no replay.
- **Return value:** a callable plus `fetch(request, { authInfo, parsedBody })`
  for composing inside other middleware. It also has `notify.toolsChanged()`,
  `promptsChanged()`, `resourcesChanged()` and `resourceUpdated(uri)`,
  which publish to open `subscriptions/listen` streams. **To use `notify`,
  create the handler once at module scope**; notifications are local to one
  isolate.
- **Origin / Host:** every present Origin is validated (malformed, opaque
  and non-HTTP Origins get a 403). The default allowlist is localhost plus
  the `workers.dev` host plus a concrete CORS origin. Custom domains set
  both lists explicitly. CORS isn't authentication.
- **Elicitation on the server** is `inputRequired(...)` with
  integrity-protected `requestState` (MRTR), not pushed requests.

### 2.2 Auth

`@cloudflare/workers-oauth-provider` in front of the handler supplies
verified `AuthInfo` at `context.http.authInfo`. `getMcpAuthContext()`
returns application props. Never log or return `authInfo.token`. Servers
that proxy a third-party OAuth provider must show their own consent screen,
protected against CSRF and bound to the browser with a `__Host-` cookie,
and must sanitize client names and URLs in it.

### 2.3 Deprecated / migration only

- `McpAgent` is deprecated and feature-frozen.
- `createLegacyMcpHandler` (SDK v1 + `WorkerTransport`) is a temporary
  lane for sessionful servers.
- Passing a v1 server to `createMcpHandler` warns.
- `experimental_createMcpHandler` is deprecated.
- The RPC transport (an Agent to an `McpAgent` binding) applies only to
  existing `McpAgent` deployments.

---

## 3. Where our plan differs (to resolve)

| Upstream recommendation | Our plan ([mcp_api.md](./mcp_api.md), [mcp_engine.md](./mcp_engine.md)) |
| --- | --- |
| Factory only; never a shared server instance | Accepts an instance or a factory, and reuses the instance |
| Factory receives `{ era, authInfo, requestInfo }` | Zero-argument factory |
| `legacy`, `responseMode`, `onerror`, `maxSubscriptions`, `keepAliveMs` options | `json_response: bool` only |
| `notify.*` plus `subscriptions/listen` on a module-scope handler | `notify` not ported; subscriptions turned off |
| `handler.fetch(request, { authInfo, parsedBody })` | `fetch(request)` only |
| `getMcpServers()` includes `resourceTemplates` | The frame shape omits `resourceTemplates` |
| `AIChatAgent.waitForMcpConnections` defaults to 10 s | Default `False` |
| Elicitation (form + URL, both lanes) is a core feature | Deferred |
| Adding a stable `id` migrates an existing server in place | `migrateServerId` not ported |
| A failed OAuth callback shows an HTML error page (docs) | A redirect to the origin (matches the 0.28.1 source) |
| `isUnauthorized` / `isTransportNotImplemented` helpers | Typed exceptions only |
| `closeAllConnections()` | Not listed |
