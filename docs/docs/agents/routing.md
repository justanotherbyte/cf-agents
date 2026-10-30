---
title: "Route requests to Python agents and call them over RPC"
description: "Send HTTP and WebSocket requests to the right agent instance with route_agent_request, add auth and CORS, and call agent methods from Worker code."
---

Route requests to an agent by its name. Requests for the same name reach the
same instance. Use `route_agent_request` for URLs or `get_agent_by_name` to
call an agent from Worker code.

## Route by URL

`route_agent_request` forwards requests whose path is
`/agents/{agent}/{name}/...` to the agent instance called `{name}`:

```python
from workers import Request, Response, WorkerEntrypoint

from agents import route_agent_request


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        response = await route_agent_request(request, self.env)
        if response is None:
            return Response("Not found", status=404)
        return response
```

`{agent}` is a Durable Object binding name in kebab case. The binding
`ChatRoom` answers at `/agents/chat-room/...`, and `MY_AGENT` at
`/agents/my-agent/...`. The function scans `self.env` for bindings, so it needs
no list of agents.

The function returns one of these results:

- the agent's response, for a matching path,
- `None` for a path outside the prefix, or one with no `{name}` segment, so
  you can handle it yourself,
- a `400` with the body `Invalid request` when `{agent}` matches no binding.
  The log line names the segment that didn't match.

The agent receives the request unchanged, URL included. HTTP requests go to
`on_request`, and WebSocket upgrades become connections.

## Change the prefix

```python
response = await route_agent_request(request, self.env, prefix="api/agents")
```

The prefix may contain `/`. Clients then need the same prefix. With
`useAgent`, pass `prefix: "api/agents"`.

## Check requests before they reach the agent

Pass `on_before_connect` for WebSocket upgrades and `on_before_request` for
everything else. Each gets the request and an `AgentRoute`, whose
`class_name` is the binding name and `name` is the instance name.

Browsers can't set headers on a WebSocket upgrade, so this example reads the
token from the `Authorization` header for HTTP and from the `token` query
parameter for WebSockets:

```python
import hmac
from urllib.parse import parse_qs, urlparse

from workers import Request, Response, WorkerEntrypoint

from agents import AgentRoute, route_agent_request


class Default(WorkerEntrypoint):
    def _valid(self, token: str | None) -> bool:
        return token is not None and hmac.compare_digest(
            token.encode(), self.env.API_TOKEN.encode()
        )

    async def check_header(self, request: Request, route: AgentRoute) -> Response | None:
        header = request.headers.get("authorization") or ""
        if not self._valid(header.removeprefix("Bearer ")):
            return Response("Unauthorized", status=401)
        return None

    async def check_query(self, request: Request, route: AgentRoute) -> Response | None:
        token = parse_qs(urlparse(request.url).query).get("token", [None])[0]
        if not self._valid(token):
            return Response("Unauthorized", status=401)
        return None

    async def fetch(self, request: Request) -> Response:
        response = await route_agent_request(
            request,
            self.env,
            on_before_connect=self.check_query,
            on_before_request=self.check_header,
        )
        if response is None:
            return Response("Not found", status=404)
        return response
```

Set the token as a secret with `npx wrangler secret put API_TOKEN`, and for
`pywrangler dev`, put `API_TOKEN=...` in `.dev.vars`. On the client, pass it
with `useAgent({ query: { token } })`. `hmac.compare_digest` takes the same
time however many characters match, so the check doesn't leak the token
through timing.

A hook can return one of these values:

- `None` to forward the request unchanged,
- a `Response` to answer instead, as above,
- a `Request` to forward that request instead, for example with an added
  header.

To check it, request an agent URL without the token. The response is
`401 Unauthorized`, and the agent never runs.

## Allow cross-origin requests

`cors=True` answers `OPTIONS` preflights and adds permissive CORS headers to
HTTP responses:

```python
response = await route_agent_request(request, self.env, cors=True)
```

The permissive headers are:

```text
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, POST, HEAD, OPTIONS
Access-Control-Allow-Headers: *
Access-Control-Max-Age: 86400
```

They allow any origin. To restrict them, pass the headers yourself:

```python
response = await route_agent_request(
    request,
    self.env,
    cors={
        "Access-Control-Allow-Origin": "https://app.example.com",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "content-type, authorization",
    },
)
```

The headers also go on a `Response` that `on_before_request` returns.
WebSocket upgrade responses never get CORS headers.

## Other options

| Option          | Type                                   | Default | What it does                                                          |
| --------------- | -------------------------------------- | ------- | --------------------------------------------------------------------- |
| `jurisdiction`  | `str \| None`                          | `None`  | Keeps the agent in a jurisdiction, such as `"eu"`.                    |
| `location_hint` | `str \| None`                          | `None`  | Where to create the agent if it doesn't exist yet, such as `"wnam"`.  |
| `routing_retry` | `RoutingRetryOptions \| False \| None` | `None`  | Retries errors the platform marks as transient. `False` turns it off. |

An error counts as transient when the platform sets `retryable` on it and
doesn't set `overloaded`. By default the SDK makes 3 attempts. Before each
retry it waits a random time up to `min(0.1 * 2 ** (attempt - 1), 0.8)`
seconds.

Pass `RoutingRetryOptions(max_attempts=..., base_delay=..., max_delay=...)`
to change the retry settings. Its `on_retry` field takes an `async` function
that receives a `RoutingRetryEvent` before each wait, for logging. A
`max_attempts` below 1, a delay that isn't positive, or a `base_delay` above
`max_delay` raises `ValueError`.

## Call an agent from Worker code

`get_agent_by_name(binding, name)` returns a stub. Await a method on the stub
to call that method on the agent:

```python
from workers import Request, Response, WorkerEntrypoint

from agents import get_agent_by_name


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        counter = await get_agent_by_name(self.env.Counter, "from-worker")
        count = await counter.increment(5)
        return Response.from_json({"count": count})
```

```json
{"count": 5}
```

The call goes over
[Workers RPC](https://developers.cloudflare.com/workers/runtime-apis/rpc/),
not over a WebSocket, so:

- It can call any public `async` method, not only `@callable` ones.
- Pass arguments positionally. JavaScript RPC has no keyword arguments.
- Arguments and return values must be serializable by
  [structured clone](https://developers.cloudflare.com/workers/runtime-apis/rpc/#structured-clonable-types-and-more).
- `get_agent_by_name` starts the agent first, so `on_start()` has run before
  your call.

`await counter.fetch(request)` also works, and sends the request to the agent
as `route_agent_request` would.

## Route with your own URLs

To use paths that don't follow `/agents/{agent}/{name}`, pick the instance
yourself and forward the request to its stub:

```python
from urllib.parse import urlparse


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        path = urlparse(request.url).path
        if path.startswith("/me"):
            user_id = request.headers.get("x-user-id", "anonymous")
            agent = await get_agent_by_name(self.env.UserAgent, user_id)
            return await agent.fetch(request)
        return Response("Not found", status=404)
```

On the client, set `basePath: "me"` in `useAgent` so it connects to `/me`
instead of building the default path.

Any client can send any `x-user-id` header, so in production take the user id
from a token you verify, as in
[Check requests before they reach the agent](#check-requests-before-they-reach-the-agent).
Otherwise one user can reach another user's agent.
