---
title: "Build your first Python agent on Cloudflare Workers"
description: "Install cf-agents, write a counter agent with state and a callable method, route requests to it, call it from a JavaScript client and deploy it."
---

This guide builds a counter agent. Each counter has a name, keeps its count
between requests, and pushes every change to connected clients. It takes about
ten minutes.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) and Node.js 22 or later.
- Python 3.12 or later. `cf-agents` 0.1.3 doesn't install on older versions.
- A Cloudflare account. The free plan works.

## 1. Create a Python Worker

```sh
uvx --from workers-py pywrangler init
cd hello-agents  # the directory you chose during init
uv add cf-agents
```

You install the package as `cf-agents` and import it as `agents`. Check that
`pyproject.toml` now lists `cf-agents` under `dependencies`.

## 2. Write the agent

Replace `src/entry.py`:

```python title="src/entry.py"
from workers import Request, Response, WorkerEntrypoint

from agents import Agent, callable, route_agent_request


class Counter(Agent):
    initial_state = {"count": 0}

    @callable
    async def increment(self, by: int = 1) -> int:
        self.set_state({"count": self.state["count"] + by})
        return self.state["count"]

    async def on_request(self, request: Request) -> Response:
        if request.method == "POST":
            await self.increment()
        return Response.from_json(self.state)


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        response = await route_agent_request(request, self.env)
        if response is None:
            return Response("Not found", status=404)
        return response
```

- `initial_state` is the state a new counter starts with.
- `set_state()` saves the new state and sends it to every connected client.
- `@callable` lets a client call `increment` over its WebSocket.
- `on_request` answers plain HTTP requests to the agent.
- `route_agent_request` sends `/agents/counter/{name}` to the counter called
  `{name}`, and returns `None` for any other path.

## 3. Configure Wrangler

Replace `wrangler.jsonc`:

```jsonc title="wrangler.jsonc"
{
  "name": "hello-agents",
  "main": "src/entry.py",
  "compatibility_date": "2026-07-20",
  "compatibility_flags": ["python_workers"],
  "durable_objects": {
    "bindings": [{ "class_name": "Counter", "name": "Counter" }]
  },
  "migrations": [{ "tag": "v1", "new_sqlite_classes": ["Counter"] }]
}
```

Each agent class needs a Durable Object binding and a migration that creates
it as a SQLite-backed class. `route_agent_request` finds the agent by the
binding's `name`, converted to kebab case, so the binding `Counter` answers at
`/agents/counter/...`.

## 4. Run it locally

```sh
uv run pywrangler dev
```

In another terminal, increment a counter called `my-counter` twice:

```sh
curl -X POST http://localhost:8787/agents/counter/my-counter
curl -X POST http://localhost:8787/agents/counter/my-counter
```

```json
{"count": 1}
{"count": 2}
```

A different name is a different counter, with its own state:

```sh
curl http://localhost:8787/agents/counter/other
```

```json
{"count": 0}
```

If you get something else, check these in order:

1. `Not found` (404) comes from your `Default.fetch`. The path doesn't start
   with `/agents/`.
2. `Invalid request` (400) means `counter` matches no binding. Check the
   binding's `name` in `wrangler.jsonc`.
3. `Not implemented` (404) means the request reached the agent, but the class
   has no `on_request`.

[Troubleshooting](troubleshooting.md) lists more errors.

## 5. Call it from a client

Browsers and Node.js connect with the `agents` npm package. Create a separate
folder for the client and install it:

```sh
mkdir client && cd client
npm init -y
npm install agents
```

```js title="client/client.mjs"
import { AgentClient } from "agents/client";

const client = new AgentClient({
  agent: "Counter",
  name: "my-counter",
  host: "localhost:8787",
  onStateUpdate: (state) => console.log("state:", state),
});

const count = await client.call("increment", [10]);
console.log("increment returned", count);
client.close();
```

```sh
node client.mjs
```

```text
state: { count: 2 }
state: { count: 12 }
increment returned 12
```

The client receives the current state when it connects, then the new state
after `increment` runs. In React, the
[`useAgent`](clients.md#connect-from-react) hook does the same.

## 6. Deploy

From the Worker folder:

```sh
uv run pywrangler deploy
```

Wrangler prints the Worker's URL. Send the same `curl` request to it, and set
`host` in the client to that hostname.

## Next steps

- [Store and sync state](state.md) to type the state, validate changes and
  store rows in SQLite.
- [Connect clients](clients.md) to call methods from React and stream
  results.
- [Build a chat agent](chat.md) to stream replies from a model.
